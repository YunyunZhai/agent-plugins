#!/usr/bin/env python3
"""
检查插件市场数据同步状态
对比本地 marketplace.json 插件数与 Pinecone 索引记录数。

用法:
    python3 check_status.py                     # 完整检查
    python3 check_status.py --local-only        # 仅检查本地数据
    python3 check_status.py --json              # JSON 输出

环境变量:
    PINECONE_API_KEY  - Pinecone API 密钥（可选，不提供则只检查本地）
    PINECONE_INDEX    - 索引名称（默认 claude-plugins-recommender）
    PINECONE_HOST     - 索引主机名（可选）
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    import requests
except ImportError:
    print("错误: 需要 requests 库。运行: pip install requests")
    sys.exit(1)


# ── 配置 ──────────────────────────────────────────────────────────────────────

MARKETPLACES_DIR = Path.home() / ".claude" / "plugins" / "marketplaces"
DEFAULT_INDEX_NAME = "claude-plugins-recommender"
PINECONE_API_VERSION = "2025-04"

PLACEHOLDER_PATTERNS = [
    "todo", "coming soon", "placeholder", "example", "template",
    "no description", "[skill-name]", "this skill should be used when",
]


# ── 本地数据统计 ──────────────────────────────────────────────────────────────

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


# ── Pinecone 索引统计 ─────────────────────────────────────────────────────────

def get_index_stats(api_key: str, index_name: str) -> Optional[Dict]:
    """获取 Pinecone 索引统计信息"""
    control_url = "https://api.pinecone.io"
    headers = {
        "Api-Key": api_key,
        "Content-Type": "application/json",
        "X-Pinecone-API-Version": PINECONE_API_VERSION,
    }

    # 获取主机名
    host = os.environ.get("PINECONE_HOST")
    if not host:
        try:
            resp = requests.get(
                f"{control_url}/indexes/{index_name}",
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200:
                print(f"  获取索引信息失败: {resp.status_code}")
                return None
            host = resp.json().get("status", {}).get("host", "")
        except Exception as e:
            print(f"  连接 Pinecone 失败: {e}")
            return None

    if not host:
        print("  无法获取索引主机名")
        return None

    # 获取统计
    try:
        resp = requests.post(
            f"https://{host}/vectors/describe_index_stats",
            headers=headers,
            json={},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"  获取索引统计失败: {resp.status_code} {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"  获取索引统计异常: {e}")
        return None


# ── 主流程 ────────────────────────────────────────────────────────────────────

def check_status(
    api_key: Optional[str] = None,
    index_name: str = DEFAULT_INDEX_NAME,
    local_only: bool = False,
    as_json: bool = False,
) -> Dict:
    """执行状态检查"""
    result = {"markets": {}, "total_local": 0, "total_index": 0, "synced": True}

    # 本地统计
    local_counts = count_local_plugins()
    total_local = sum(local_counts.values())
    result["total_local"] = total_local

    for market, count in sorted(local_counts.items()):
        result["markets"][market] = {"local": count, "index": None, "diff": None}

    # Pinecone 统计
    index_stats = None
    if not local_only and api_key:
        index_stats = get_index_stats(api_key, index_name)

    if index_stats:
        namespaces = index_stats.get("namespaces", {})
        total_index = sum(
            ns.get("vectorCount", 0) for ns in namespaces.values()
        )
        result["total_index"] = total_index

        for market in local_counts:
            index_count = namespaces.get(market, {}).get("vectorCount", 0)
            diff = local_counts[market] - index_count
            result["markets"][market]["index"] = index_count
            result["markets"][market]["diff"] = diff
            if abs(diff) > 10:
                result["synced"] = False

    # 输出
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return result

    print("=" * 55)
    print("插件市场数据状态")
    print("=" * 55)

    for market, info in sorted(result["markets"].items()):
        local = info["local"]
        index = info["index"]
        diff = info["diff"]

        if index is not None:
            status = "✓" if abs(diff or 0) <= 10 else "⚠"
            print(f"  {status} {market}: 本地 {local} / 索引 {index} (差 {diff:+d})")
        else:
            print(f"  - {market}: 本地 {local}")

    print(f"\n本地总计: {total_local} 个有效插件")
    if result["total_index"] > 0:
        print(f"索引总计: {result['total_index']} 条记录")
        if result["synced"]:
            print("状态: ✓ 数据基本同步")
        else:
            print("状态: ⚠ 数据可能过期，建议运行 sync_to_pinecone.py 刷新")
    else:
        print("索引: 未检查或不可达")

    return result


def main():
    parser = argparse.ArgumentParser(description="检查插件市场数据同步状态")
    parser.add_argument(
        "--local-only", "-l",
        action="store_true",
        help="仅检查本地数据，不查询 Pinecone",
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="以 JSON 格式输出",
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
    index_name = args.index or os.environ.get("PINECONE_INDEX", DEFAULT_INDEX_NAME)

    check_status(
        api_key=api_key,
        index_name=index_name,
        local_only=args.local_only,
        as_json=args.json,
    )


if __name__ == "__main__":
    main()
