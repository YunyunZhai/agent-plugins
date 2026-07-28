#!/usr/bin/env python3
"""
插件市场数据同步到 Pinecone
读取本地 marketplace.json，构造向量记录，批量 upsert 到 Pinecone 索引。

用法:
    python3 sync_to_pinecone.py                          # 同步所有市场
    python3 sync_to_pinecone.py --marketplaces official  # 只同步指定市场
    python3 sync_to_pinecone.py --dry-run                # 预览，不实际上传
    python3 sync_to_pinecone.py --batch-size 50          # 自定义批大小

环境变量:
    PINECONE_API_KEY  - Pinecone API 密钥（必需）
    PINECONE_INDEX    - 索引名称（默认 claude-plugins-recommender）
    PINECONE_HOST     - 索引主机名（可选，自动检测）
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple

try:
    import requests
except ImportError:
    print("错误: 需要 requests 库。运行: pip install requests")
    sys.exit(1)


# ── 配置 ──────────────────────────────────────────────────────────────────────

MARKETPLACES_DIR = Path.home() / ".claude" / "plugins" / "marketplaces"
DEFAULT_INDEX_NAME = "claude-plugins-recommender"
DEFAULT_BATCH_SIZE = 100
PINECONE_API_VERSION = "2025-04"

# 市场名到命名空间的映射（市场名即命名空间名）
MARKET_NAMESPACES = {
    "claude-plugins-official": "claude-plugins-official",
    "claude-community": "claude-community",
    "ecc": "ecc",
    "karpathy-skills": "karpathy-skills",
    "mattpocock": "mattpocock",
}

# 占位符描述模式（过滤用）
PLACEHOLDER_PATTERNS = [
    "todo", "coming soon", "placeholder", "example", "template",
    "no description", "[skill-name]", "this skill should be used when",
]


# ── 数据读取 ──────────────────────────────────────────────────────────────────

def find_marketplace_files() -> List[Tuple[str, Path]]:
    """查找所有 marketplace.json 文件，返回 (市场名, 文件路径) 列表"""
    results = []
    if not MARKETPLACES_DIR.exists():
        return results

    for market_dir in MARKETPLACES_DIR.iterdir():
        if not market_dir.is_dir():
            continue
        marketplace_json = market_dir / ".claude-plugin" / "marketplace.json"
        if marketplace_json.exists():
            results.append((market_dir.name, marketplace_json))

    return results


def parse_marketplace(market_name: str, filepath: Path) -> List[Dict]:
    """解析单个 marketplace.json，返回插件记录列表"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    records = []
    for plugin in data.get("plugins", []):
        name = plugin.get("name", "")
        description = plugin.get("description", "")

        # 跳过无效插件
        if not description or not description.strip():
            continue
        if any(p in description.lower() for p in PLACEHOLDER_PATTERNS):
            continue

        author = ""
        if plugin.get("author"):
            if isinstance(plugin["author"], dict):
                author = plugin["author"].get("name", "")
            elif isinstance(plugin["author"], str):
                author = plugin["author"]

        category = plugin.get("category", "uncategorized") or "uncategorized"
        homepage = plugin.get("homepage", "") or ""

        # 构造 _id：marketplace::plugin-name（插件名中的 :: 替换为 --）
        safe_name = name.replace("::", "--")
        plugin_id = f"{market_name}::{safe_name}"

        # 构造嵌入文本
        text = f"Plugin: {name}. Category: {category}. Author: {author}. Description: {description}"

        records.append({
            "_id": plugin_id,
            "text": text,
            "name": name,
            "category": category,
            "marketplace": market_name,
            "author": author,
            "homepage": homepage,
        })

    return records


def read_all_plugins(marketplace_filter: Optional[str] = None) -> Dict[str, List[Dict]]:
    """读取所有市场的插件数据，返回 {市场名: [记录]}"""
    market_files = find_marketplace_files()
    if not market_files:
        print(f"错误: 未找到 marketplace.json 文件（检查 {MARKETPLACES_DIR}）")
        sys.exit(1)

    all_records = {}
    for market_name, filepath in market_files:
        if marketplace_filter and market_name != marketplace_filter:
            continue
        try:
            records = parse_marketplace(market_name, filepath)
            all_records[market_name] = records
            print(f"  {market_name}: {len(records)} 个有效插件")
        except Exception as e:
            print(f"  {market_name}: 解析失败 - {e}")

    return all_records


# ── Pinecone API ──────────────────────────────────────────────────────────────

class PineconeClient:
    """Pinecone REST API 客户端"""

    def __init__(self, api_key: str, index_name: str):
        self.api_key = api_key
        self.index_name = index_name
        self.host = None
        self._control_url = "https://api.pinecone.io"

    def _headers(self, content_type: str = "application/json") -> Dict:
        return {
            "Api-Key": self.api_key,
            "Content-Type": content_type,
            "X-Pinecone-API-Version": PINECONE_API_VERSION,
        }

    def _resolve_host(self) -> str:
        """获取索引主机名"""
        if self.host:
            return self.host

        # 方法1: 从 describe_index 获取
        try:
            resp = requests.get(
                f"{self._control_url}/indexes/{self.index_name}",
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                info = resp.json()
                self.host = info.get("status", {}).get("host", "")
                if self.host:
                    return self.host
        except Exception:
            pass

        # 方法2: 环境变量
        env_host = os.environ.get("PINECONE_HOST")
        if env_host:
            self.host = env_host
            return self.host

        print(f"错误: 无法获取索引 '{self.index_name}' 的主机名")
        print("请设置 PINECONE_HOST 环境变量或确保索引已创建")
        sys.exit(1)

    def index_exists(self) -> bool:
        """检查索引是否存在"""
        try:
            resp = requests.get(
                f"{self._control_url}/indexes/{self.index_name}",
                headers=self._headers(),
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def create_index(self) -> bool:
        """创建索引"""
        print(f"创建索引 '{self.index_name}'...")
        try:
            resp = requests.post(
                f"{self._control_url}/indexes",
                headers=self._headers(),
                json={
                    "name": self.index_name,
                    "cloud": "aws",
                    "region": "us-east-1",
                    "embed": {
                        "model": "llama-text-embed-v2",
                        "fieldMap": {"text": "text"},
                    },
                },
                timeout=30,
            )
            if resp.status_code in (200, 201):
                print("索引创建请求已发送，等待就绪...")
                return self._wait_for_ready()
            else:
                print(f"创建索引失败: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            print(f"创建索引异常: {e}")
            return False

    def _wait_for_ready(self, timeout: int = 120) -> bool:
        """等待索引就绪"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = requests.get(
                    f"{self._control_url}/indexes/{self.index_name}",
                    headers=self._headers(),
                    timeout=10,
                )
                if resp.status_code == 200:
                    status = resp.json().get("status", {}).get("state", "")
                    if status == "Ready":
                        print("索引已就绪")
                        return True
                    print(f"  索引状态: {status}...")
            except Exception:
                pass
            time.sleep(3)

        print(f"等待索引就绪超时（{timeout}s）")
        return False

    def upsert(self, namespace: str, records: List[Dict]) -> bool:
        """批量 upsert 记录"""
        host = self._resolve_host()
        url = f"https://{host}/vectors/upsert"

        try:
            resp = requests.post(
                url,
                headers=self._headers(),
                json={"vectors": records, "namespace": namespace},
                timeout=30,
            )
            if resp.status_code == 200:
                return True
            else:
                print(f"    upsert 失败: {resp.status_code} {resp.text[:200]}")
                return False
        except Exception as e:
            print(f"    upsert 异常: {e}")
            return False

    def describe_index_stats(self) -> Optional[Dict]:
        """获取索引统计信息"""
        host = self._resolve_host()
        url = f"https://{host}/vectors/describe_index_stats"

        try:
            resp = requests.post(
                url,
                headers=self._headers(),
                json={},
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                print(f"获取索引统计失败: {resp.status_code} {resp.text[:200]}")
                return None
        except Exception as e:
            print(f"获取索引统计异常: {e}")
            return None


# ── 主流程 ────────────────────────────────────────────────────────────────────

def sync(
    api_key: str,
    index_name: str,
    marketplace_filter: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> bool:
    """执行同步流程"""
    print("=" * 60)
    print("插件市场数据同步到 Pinecone")
    print("=" * 60)

    # 1. 读取本地数据
    print("\n[1/4] 读取本地市场数据...")
    all_records = read_all_plugins(marketplace_filter)
    total = sum(len(r) for r in all_records.values())
    print(f"  共计 {total} 个有效插件")

    if total == 0:
        print("没有找到有效插件，退出")
        return False

    if dry_run:
        print("\n[DRY RUN] 预览前 5 条记录:")
        for market, records in all_records.items():
            for r in records[:2]:
                print(f"  [{market}] {r['name']}: {r['text'][:80]}...")
        print(f"\n[DRY RUN] 将上传 {total} 条记录到 {len(all_records)} 个命名空间")
        return True

    # 2. 初始化 Pinecone 客户端
    print(f"\n[2/4] 连接 Pinecone（索引: {index_name}）...")
    client = PineconeClient(api_key, index_name)

    if not client.index_exists():
        print("  索引不存在，尝试创建...")
        if not client.create_index():
            return False
    else:
        print("  索引已存在")

    # 3. 批量 upsert
    print(f"\n[3/4] 上传数据（批大小: {batch_size}）...")
    success_count = 0
    fail_count = 0

    for market_name, records in all_records.items():
        namespace = MARKET_NAMESPACES.get(market_name, market_name)
        batches = [records[i : i + batch_size] for i in range(0, len(records), batch_size)]
        print(f"  {market_name} ({namespace}): {len(records)} 条，{len(batches)} 批")

        for i, batch in enumerate(batches):
            if client.upsert(namespace, batch):
                success_count += len(batch)
                print(f"    批 {i + 1}/{len(batches)} ✓ ({len(batch)} 条)")
            else:
                fail_count += len(batch)
                print(f"    批 {i + 1}/{len(batches)} ✗ 失败")

    # 4. 验证
    print(f"\n[4/4] 验证上传结果...")
    stats = client.describe_index_stats()
    if stats:
        namespaces = stats.get("namespaces", {})
        for market_name in all_records.keys():
            namespace = MARKET_NAMESPACES.get(market_name, market_name)
            expected = len(all_records[market_name])
            actual = namespaces.get(namespace, {}).get("vectorCount", 0)
            status = "✓" if actual >= expected else "⚠"
            print(f"  {status} {namespace}: 索引 {actual} / 预期 {expected}")

    print(f"\n完成: 成功 {success_count} 条, 失败 {fail_count} 条")
    return fail_count == 0


def main():
    parser = argparse.ArgumentParser(description="同步插件市场数据到 Pinecone")
    parser.add_argument(
        "--marketplaces", "-m",
        help="只同步指定市场（逗号分隔，如 official,ecc）",
    )
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"每批 upsert 的记录数（默认 {DEFAULT_BATCH_SIZE}）",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="预览模式，不实际上传",
    )
    parser.add_argument(
        "--api-key",
        help="Pinecone API 密钥（也可通过 PINECONE_API_KEY 环境变量设置）",
    )
    parser.add_argument(
        "--index",
        default=None,
        help=f"索引名称（默认 {DEFAULT_INDEX_NAME}）",
    )

    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("PINECONE_API_KEY")
    if not api_key and not args.dry_run:
        print("错误: 未设置 PINECONE_API_KEY")
        print("用法: PINECONE_API_KEY=xxx python3 sync_to_pinecone.py")
        print("  或: python3 sync_to_pinecone.py --api-key xxx")
        sys.exit(1)

    index_name = args.index or os.environ.get("PINECONE_INDEX", DEFAULT_INDEX_NAME)

    # 解析市场过滤器
    marketplace_filter = None
    if args.marketplaces:
        # 支持简写映射
        aliases = {"official": "claude-plugins-official", "community": "claude-community"}
        filters = []
        for f in args.marketplaces.split(","):
            f = f.strip()
            filters.append(aliases.get(f, f))
        if len(filters) == 1:
            marketplace_filter = filters[0]
        else:
            # 多个市场时不做过滤，逐个处理
            marketplace_filter = None

    success = sync(
        api_key=api_key or "",
        index_name=index_name,
        marketplace_filter=marketplace_filter,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
