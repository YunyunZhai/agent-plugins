#!/usr/bin/env python3
"""
在线语义召回（gh-search 三通道中的"语义通道"）。

输入自然语言 query → Pinecone 嵌入 → sqlite-vec kNN 召回 → 过滤 → 输出候选。

输出结构与 search_repos.py 完全一致（candidates_list 同构），便于 SKILL.md
在三通道（关键词 / 语义 / 并行）中 union 合并。

用法:
    python3 semantic_search.py --query "启动快的编码智能体" --json
    python3 semantic_search.py --query "python 安全扫描" --top-k 50 --min-stars 500 --json

环境变量:
    PINECONE_API_KEY  - Pinecone API 密钥(必需, 用于 query 嵌入)
    PINECONE_MODEL    - 嵌入模型(默认 llama-text-embed-v2)
    GH_SEARCH_DB      - sqlite 路径
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from sqlite_store import (
    EMBED_DIM,
    EMBED_MODEL,
    connect,
    get_repo,
    search_knn,
)

DEFAULT_TOP_K = 50
DEFAULT_MIN_STARS = 0


class SemanticError(RuntimeError):
    """语义召回失败"""


def embed_query(query: str, model: str = EMBED_MODEL) -> List[float]:
    """用 Pinecone 把用户 query 嵌入为向量。"""
    key = os.environ.get("PINECONE_API_KEY", "")
    if not key:
        raise SemanticError("未设置 PINECONE_API_KEY 环境变量")
    try:
        from pinecone import Pinecone
    except ImportError:
        raise SemanticError("缺少 pinecone 库: pip install --user pinecone")
    pc = Pinecone(api_key=key)
    try:
        r = pc.inference.embed(
            model=model,
            inputs=[query],
            parameters={"input_type": "query", "truncate": "END"},
        )
    except Exception as e:
        raise SemanticError(f"query 嵌入失败: {e}")
    return r.data[0].values


def fetch_live_stars(repo_ids: List[str], client: "GitHubClient") -> Dict[str, int]:
    """
    在线批量拉取最新 star 数（GraphQL 批量 alias）。
    索引不存 stars（避免频繁增量），召回后对候选子集拉实时 stars。
    返回 {full_name: stars}，只含仍存在/可访问的仓库。
    """
    if not repo_ids:
        return {}
    # 按 30 一批分批（GraphQL 别名过多会超限），遗漏的返回 0
    result: Dict[str, int] = {}
    batch_size = 30
    for i in range(0, len(repo_ids), batch_size):
        batch = repo_ids[i:i + batch_size]
        aliases = []
        for j, rid in enumerate(batch):
            # repo 对象用 repository(owner, name) 而非 search（避免 search 的 fallback 语义）
            owner, _, name = rid.partition("/")
            aliases.append(
                f'q{j}: repository(owner: "{owner}", name: "{name}") '
                f'{{ stargazerCount }}'
            )
        gql = "query { " + " ".join(aliases) + " }"
        try:
            data = client.graphql(gql)
        except Exception:
            continue  # 网络抖动时降级（该批 stars 记 0）
        for j, rid in enumerate(batch):
            node = data.get(f"q{j}")
            if node and "stargazerCount" in node:
                result[rid] = node["stargazerCount"]
    return result


def semantic_search(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    min_stars: int = DEFAULT_MIN_STARS,
    exclude_fork: bool = True,
    exclude_archived: bool = True,
    db_path: Optional[str] = None,
    model: str = EMBED_MODEL,
) -> Dict[str, Any]:
    """主流程：嵌入 → kNN 召回 → 在线拉最新 stars → 过滤 → 输出同构候选列表。"""
    conn = connect(db_path)
    qvec = embed_query(query, model)
    if len(qvec) != EMBED_DIM:
        raise SemanticError(f"query 向量维度 {len(qvec)} != {EMBED_DIM}")

    # kNN 召回（sqlite-vec 返回按距离升序）
    hits = search_knn(conn, qvec, k=max(top_k * 2, 20))

    # 收集候选 id（先排除 fork/archived 硬过滤）
    candidates: List[Dict[str, Any]] = []
    recalled = 0
    for h in hits:
        repo = get_repo(conn, h["id"])
        if not repo:
            continue
        recalled += 1
        if exclude_fork and repo.get("is_fork"):
            continue
        if exclude_archived and repo.get("is_archived"):
            continue
        candidates.append((repo, h["distance"]))
    if not candidates:
        return {"query": query, "mode": "semantic", "recalled": recalled,
                "candidates": 0, "candidates_list": [], "note": "无候选。"}

    # 在线批量拉最新 stars
    sys.path.insert(0, str(Path(__file__).parent))
    from github_client import GitHubClient
    client = GitHubClient()
    ids = [repo["id"] for repo, _ in candidates]
    live_stars = fetch_live_stars(ids, client)

    # 组装 + 在线 stars 过滤
    out: List[Dict[str, Any]] = []
    for repo, dist in candidates:
        stars = live_stars.get(repo["id"], 0)
        if stars < min_stars:
            continue
        topics = repo.get("topics") or []
        if isinstance(topics, str):
            try:
                topics = json.loads(topics)
            except Exception:
                topics = []
        out.append({
            "full_name": repo.get("id", ""),
            "description": repo.get("description"),
            "topics": topics,
            "primary_language": repo.get("primary_language"),
            "stars": stars,                              # 在线最新值
            "is_fork": bool(repo.get("is_fork")),
            "is_archived": bool(repo.get("is_archived")),
            "_semantic_distance": round(dist, 4),
        })

    # 按语义距离升序（近者优先）
    out.sort(key=lambda c: c.get("_semantic_distance", 1e9))
    return {
        "query": query,
        "mode": "semantic",
        "min_stars": min_stars,
        "recalled": recalled,
        "candidates": len(out),
        "candidates_list": out,
        "note": "语义召回（name/description/topics 向量匹配）。stars 为在线实时值，"
                "仅作过滤不作主排序（避免 star 淹没语义差异）。",
    }


def main():
    parser = argparse.ArgumentParser(description="语义召回")
    parser.add_argument("--query", required=True, help="用户检索意图（自然语言）")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                        help=f"kNN 召回数（默认 {DEFAULT_TOP_K}）")
    parser.add_argument("--min-stars", type=int, default=DEFAULT_MIN_STARS,
                        help=f"最小 star（默认 {DEFAULT_MIN_STARS}）")
    parser.add_argument("--db", default=None, help="sqlite 路径")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = parser.parse_args()

    try:
        result = semantic_search(args.query, args.top_k, args.min_stars, db_path=args.db)
    except SemanticError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"语义召回: {result['candidates']} 条候选（kNN {result['recalled']}）")
        for c in result["candidates_list"][:10]:
            print(f"  {c['stars']:>5}★ {c['full_name']:<45} {str(c['description'])[:50]}")


if __name__ == "__main__":
    main()