#!/usr/bin/env python3
"""
插件搜索脚本
提供 status 和 search 子命令，使用 Pinecone Python SDK 执行状态检查与搜索。

用法:
    python3 search_plugins.py status                         # 检查索引状态
    python3 search_plugins.py search "security scanning"      # 跨所有市场搜索
    python3 search_plugins.py search "test" --namespace NS    # 单市场搜索
    python3 search_plugins.py search "test" --filter category=security  # 过滤搜索

环境变量:
    PINECONE_API_KEY  - Pinecone API 密钥（必需）
    PINECONE_INDEX    - 索引名称（默认 claude-plugins-recommender）
    PINECONE_HOST     - 索引主机名（可选，自动检测）
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

from pinecone_client import (
    DEFAULT_INDEX_NAME,
    MARKET_NAMESPACES,
    PineconeClient,
)


# ── 结果提取 ──────────────────────────────────────────────────────────────────

def _extract_results(response: Optional[Dict]) -> List[Dict]:
    """从 Pinecone query 响应中提取插件信息"""
    if not response:
        return []

    results = []
    for item in response.get("matches", []):
        metadata = item.get("metadata", {})
        results.append({
            "id": item.get("id", ""),
            "score": item.get("score", 0),
            "name": metadata.get("name", ""),
            "category": metadata.get("category", ""),
            "marketplace": metadata.get("marketplace", ""),
            "author": metadata.get("author", ""),
            "homepage": metadata.get("homepage", ""),
            "description": metadata.get("text", ""),
        })
    return results


# ── status 子命令 ─────────────────────────────────────────────────────────────

def cmd_status(client: PineconeClient) -> Dict:
    """检查索引状态（替代 list-indexes + describe-index-stats）"""
    index_exists = client.index_exists()

    result = {
        "index_exists": index_exists,
        "index_name": client.index_name,
        "namespaces": {},
        "ready": False,
    }

    if not index_exists:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return result

    stats = client.describe_index_stats()
    if stats:
        namespaces = stats.get("namespaces", {})
        for ns, info in namespaces.items():
            result["namespaces"][ns] = {"vector_count": info.get("vectorCount", 0)}
        # 检查关键命名空间是否有数据
        has_official = namespaces.get("claude-plugins-official", {}).get("vectorCount", 0) >= 270
        has_karpathy = namespaces.get("karpathy-skills", {}).get("vectorCount", 0) >= 1
        result["ready"] = has_official and has_karpathy

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


# ── search 子命令 ─────────────────────────────────────────────────────────────

def cmd_search(
    client: PineconeClient,
    text: str,
    namespace: Optional[str] = None,
    filters: Optional[Dict] = None,
    top_k: int = 50,
    top_n: int = 15,
) -> Dict:
    """搜索插件（通过 Pinecone Python SDK）"""
    # 确定要搜索的命名空间
    if namespace:
        namespaces = [namespace]
    else:
        # 默认搜索所有已知市场命名空间
        namespaces = list(MARKET_NAMESPACES.values())

    all_results = []
    searched = []

    for ns in namespaces:
        response = client.query(
            namespace=ns,
            text=text,
            top_k=top_k,
            filter_dict=filters,
            rerank_top_n=top_n,
        )
        if response:
            searched.append(ns)
            ns_results = _extract_results(response)
            all_results.extend(ns_results)

    # 合并结果：按 score 降序排序，取 top_n
    all_results.sort(key=lambda r: r["score"], reverse=True)
    all_results = all_results[:top_n]

    output = {
        "query": text,
        "namespaces_searched": searched,
        "results": all_results,
        "total_results": len(all_results),
    }

    print(json.dumps(output, indent=2, ensure_ascii=False))
    return output


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="插件搜索脚本（通过 Pinecone Python SDK 查询）"
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # status 子命令
    subparsers.add_parser("status", help="检查索引状态")

    # search 子命令
    search_parser = subparsers.add_parser("search", help="搜索插件")
    search_parser.add_argument("text", help="搜索查询文本")
    search_parser.add_argument(
        "--namespace", "-n",
        default=None,
        help="限定搜索的命名空间（默认所有市场）",
    )
    search_parser.add_argument(
        "--filter", "-f",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help="元数据过滤（可重复，如 --filter category=security）",
    )
    search_parser.add_argument(
        "--top-k",
        type=int,
        default=50,
        help="每命名空间候选数（默认 50）",
    )
    search_parser.add_argument(
        "--top-n",
        type=int,
        default=15,
        help="最终返回数（默认 15）",
    )
    search_parser.add_argument(
        "--api-key",
        help="Pinecone API 密钥（也可通过 PINECONE_API_KEY 环境变量设置）",
    )
    search_parser.add_argument(
        "--index",
        default=None,
        help=f"索引名称（默认 {DEFAULT_INDEX_NAME}）",
    )

    # 也支持顶层 --api-key / --index（用于 status 子命令）
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

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # 解析 API key 和 index（子命令参数优先于顶层参数）
    api_key = getattr(args, "api_key", None) or os.environ.get("PINECONE_API_KEY")
    index_name = getattr(args, "index", None) or os.environ.get("PINECONE_INDEX", DEFAULT_INDEX_NAME)

    if not api_key:
        print("错误: 未设置 PINECONE_API_KEY", file=sys.stderr)
        print("用法: PINECONE_API_KEY=xxx python3 search_plugins.py <command>", file=sys.stderr)
        sys.exit(1)

    client = PineconeClient(api_key, index_name)

    if args.command == "status":
        cmd_status(client)
    elif args.command == "search":
        # 解析 filter
        filter_dict = None
        if args.filter:
            filter_dict = {}
            for f in args.filter:
                if "=" in f:
                    key, value = f.split("=", 1)
                    filter_dict[key.strip()] = value.strip()

        cmd_search(
            client=client,
            text=args.text,
            namespace=args.namespace,
            filters=filter_dict,
            top_k=args.top_k,
            top_n=args.top_n,
        )


if __name__ == "__main__":
    main()
