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
from typing import Dict, Optional

from pinecone_client import DEFAULT_INDEX_NAME, PineconeClient, count_local_plugins


# ── Pinecone 索引统计 ─────────────────────────────────────────────────────────

def get_index_stats(api_key: str, index_name: str) -> Optional[Dict]:
    """获取 Pinecone 索引统计信息"""
    client = PineconeClient(api_key, index_name)
    if not client.index_exists():
        print("  索引不存在")
        return None
    return client.describe_index_stats()


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
