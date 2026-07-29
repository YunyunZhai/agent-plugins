#!/usr/bin/env python3
"""
Pinecone 客户端共享模块

提取自 sync_to_pinecone.py，供 sync_to_pinecone.py、check_status.py、
search_plugins.py 复用，避免三份脚本各自重复 Pinecone REST API 调用逻辑。

环境变量:
    PINECONE_API_KEY  - Pinecone API 密钥（必需）
    PINECONE_INDEX    - 索引名称（默认 claude-plugins-recommender）
    PINECONE_HOST     - 索引主机名（可选，自动检测）
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from pinecone import Pinecone
except ImportError:
    print("错误: 需要 pinecone 库。请先安装: pip install pinecone")
    sys.exit(1)


# ── 配置 ──────────────────────────────────────────────────────────────────────

MARKETPLACES_DIR = Path.home() / ".claude" / "plugins" / "marketplaces"
DEFAULT_INDEX_NAME = "claude-plugins-recommender"
DEFAULT_BATCH_SIZE = 96

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


def count_local_plugins() -> Dict[str, int]:
    """统计各市场的有效插件数量"""
    counts = {}
    if not MARKETPLACES_DIR.exists():
        return counts

    for market_dir in MARKETPLACES_DIR.iterdir():
        if not market_dir.is_dir():
            continue
        marketplace_json = market_dir / ".claude-plugin" / "marketplace.json"
        if not marketplace_json.exists():
            continue

        try:
            with open(marketplace_json, "r", encoding="utf-8") as f:
                data = json.load(f)

            valid = 0
            for plugin in data.get("plugins", []):
                desc = plugin.get("description", "")
                if not desc or not desc.strip():
                    continue
                if any(p in desc.lower() for p in PLACEHOLDER_PATTERNS):
                    continue
                valid += 1

            counts[market_dir.name] = valid
        except Exception as e:
            print(f"  {market_dir.name}: 读取失败 - {e}")

    return counts


# ── Pinecone API ──────────────────────────────────────────────────────────────

class PineconeClient:
    """Pinecone Python SDK 客户端"""

    def __init__(self, api_key: str, index_name: str):
        self.api_key = api_key
        self.index_name = index_name
        self.host = None
        self._pc = Pinecone(api_key=api_key)
        self._index = None

    def _normalize_host(self, host: str) -> str:
        normalized = host.strip()
        if normalized.startswith("https://"):
            normalized = normalized[len("https://") :]
        elif normalized.startswith("http://"):
            normalized = normalized[len("http://") :]
        return normalized.rstrip("/")

    def _get(self, obj: Any, key: str, default: Any = None) -> Any:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _resolve_host(self) -> str:
        """获取索引主机名，优先使用环境变量覆盖。"""
        if self.host:
            return self.host

        # 方法1: 环境变量（最高优先级，用于手动覆盖自动发现）
        env_host = os.environ.get("PINECONE_HOST")
        if env_host:
            normalized = self._normalize_host(env_host)
            if normalized:
                self.host = normalized
                return self.host

        # 方法2: 从控制面描述索引获取 host
        if not self.index_exists():
            print(f"错误: 索引 '{self.index_name}' 不存在")
            print("请先运行同步脚本创建索引:")
            print(f"  PINECONE_API_KEY=<key> python3 -m sync_to_pinecone")
            sys.exit(1)

        try:
            desc = self._pc.describe_index(name=self.index_name)
            host = self._get(desc, "host", "")
            if host:
                self.host = self._normalize_host(host)
                return self.host

            status = self._get(desc, "status", {})
            state = self._get(status, "state", "Unknown")
            print(f"错误: 索引 '{self.index_name}' 状态为 '{state}'，主机名尚未就绪")
            print("请等待索引完全就绪后重试，或设置 PINECONE_HOST 环境变量")
            sys.exit(1)
        except Exception as e:
            print(f"获取索引信息失败: {e}")

        print(f"错误: 无法获取索引 '{self.index_name}' 的主机名")
        print("索引可能正在创建中，请稍后重试，或设置 PINECONE_HOST 环境变量")
        sys.exit(1)

    def _get_index(self):
        """获取数据面 index 客户端。"""
        if self._index is not None:
            return self._index

        env_host = os.environ.get("PINECONE_HOST")
        if env_host:
            self.host = self._normalize_host(env_host)
            self._index = self._pc.index(host=self.host)
        else:
            # SDK 会自动通过 index name 解析 host，更稳妥。
            self._index = self._pc.index(self.index_name)
            host = getattr(self._index, "host", None)
            if host:
                self.host = self._normalize_host(host)
        return self._index

    def index_exists(self) -> bool:
        """检查索引是否存在"""
        try:
            return bool(self._pc.has_index(name=self.index_name))
        except Exception:
            return False

    def create_index(self) -> bool:
        """创建索引"""
        print(f"创建索引 '{self.index_name}'...")
        try:
            self._pc.create_index_for_model(
                name=self.index_name,
                cloud="aws",
                region="us-east-1",
                embed={
                    "model": "llama-text-embed-v2",
                    "field_map": {"text": "text"},
                },
            )
            print("索引创建请求已发送，等待就绪...")
            return self._wait_for_ready()
        except Exception as e:
            print(f"创建索引异常: {e}")
            return False

    def _wait_for_ready(self, timeout: int = 120) -> bool:
        """等待索引就绪"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                desc = self._pc.describe_index(name=self.index_name)
                status = self._get(desc, "status", {})
                state = self._get(status, "state", "")
                ready = bool(self._get(status, "ready", False))
                host = self._get(desc, "host", "")

                if state == "Ready" or ready:
                    if host:
                        self.host = self._normalize_host(host)
                    print("索引已就绪")
                    return True
                print(f"  索引状态: {state or 'Unknown'}...")
            except Exception:
                pass
            time.sleep(3)

        print(f"等待索引就绪超时（{timeout}s）")
        return False

    def upsert(self, namespace: str, records: List[Dict]) -> bool:
        """批量 upsert 记录"""
        try:
            response = self._get_index().upsert_records(
                namespace=namespace,
                records=records,
                timeout=30,
            )
            # UpsertRecordsResponse 的字段在不同版本里可能略有差异；
            # 优先用 record_count 判断是否真的接收了本批记录。
            record_count = self._get(response, "record_count", 0)
            if isinstance(record_count, int):
                return record_count == len(records)
            # 容错：如果 SDK 不返回 record_count，就只要没异常即可视为成功
            return True
        except Exception as e:
            print(f"    upsert 异常: {e}")
            return False

    def describe_index_stats(self) -> Optional[Dict]:
        """获取索引统计信息"""
        try:
            stats = self._get_index().describe_index_stats(timeout=10)
            namespaces = {}
            for ns, summary in self._get(stats, "namespaces", {}).items():
                vector_count = self._get(summary, "vector_count", 0)
                namespaces[ns] = {"vectorCount": vector_count}

            total_vector_count = self._get(stats, "total_vector_count", 0)
            return {
                "namespaces": namespaces,
                "totalVectorCount": total_vector_count,
            }
        except Exception as e:
            print(f"获取索引统计异常: {e}")
            return None

    def list_ids(self, namespace: str = "") -> List[str]:
        """列出指定命名空间中所有记录的 ID"""
        try:
            ids = []
            for page in self._get_index().list(limit=100, namespace=namespace):
                vectors = getattr(page, "vectors", None) or []
                for v in vectors:
                    vid = getattr(v, "id", None) or (v.get("id") if isinstance(v, dict) else None)
                    if vid:
                        ids.append(vid)
            return ids
        except Exception as e:
            print(f"  列出 ID 异常: {e}", file=sys.stderr)
            return []

    def delete_ids(self, namespace: str, ids: List[str]) -> bool:
        """从命名空间中删除指定 ID 的记录"""
        if not ids:
            return True
        try:
            self._get_index().delete(ids=ids, namespace=namespace, timeout=30)
            return True
        except Exception as e:
            print(f"    delete 异常: {e}")
            return False

    def query(
        self,
        namespace: str,
        text: str,
        top_k: int = 50,
        filter_dict: Optional[Dict] = None,
        rerank_top_n: int = 15,
    ) -> Optional[Dict]:
        """向量搜索 + 重排序

        通过 Pinecone integrated inference 的 search 接口执行搜索，
        并返回与旧 REST 版本兼容的结果结构。

        返回 Pinecone 响应 JSON，失败返回 None。
        """
        try:
            response = self._get_index().search(
                namespace=namespace,
                top_k=top_k,
                inputs={"text": text},
                filter=filter_dict,
                fields=["text", "name", "category", "marketplace", "author", "homepage"],
                rerank={
                    "model": "bge-reranker-v2-m3",
                    "rank_fields": ["text"],
                    "top_n": rerank_top_n,
                },
                timeout=30,
            )

            hits = self._get(self._get(response, "result", {}), "hits", [])
            matches = []
            for hit in hits:
                fields = self._get(hit, "fields", {}) or {}
                matches.append(
                    {
                        "id": self._get(hit, "id", ""),
                        "score": self._get(hit, "score", 0),
                        "metadata": dict(fields) if isinstance(fields, dict) else {},
                    }
                )

            return {"matches": matches}
        except Exception as e:
            print(f"查询异常: {e}", file=sys.stderr)
            return None
