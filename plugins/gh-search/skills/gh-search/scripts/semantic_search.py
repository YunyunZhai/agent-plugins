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
import math
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
DEFAULT_STAR_WEIGHT = 0.08   # 混合排序: 每 10 倍 star 抵扣 0.08 个语义距离单位


class SemanticError(RuntimeError):
    """语义召回失败"""


def hybrid_score(distance: float, stars: int, star_weight: float) -> float:
    """混合分 = 语义距离 − star 先验（越小越好）。

    背景：元数据稀疏的头部项目（如 alist 描述只写 "file list/WebDAV"）在纯距离
    排序下会被关键词堆砌的长尾淹没。log10 先验让每 10 倍 star 抵扣 star_weight
    个距离单位，量级上足以救回头部项目、又不至于让无关大热门碾压相关小项目。
    """
    return distance - star_weight * math.log10(1 + max(stars, 0))


def embed_query(query: str, model: str = EMBED_MODEL, backend: str = "pinecone") -> List[float]:
    """把用户 query 嵌入为向量。backend: pinecone | ark(方舟 doubao) | local(bge-m3)。"""
    if backend == "ark":
        try:
            from ark_client import ArkEmbed
            return ArkEmbed().embed([query])[0]
        except Exception as e:
            raise SemanticError(f"方舟 query 嵌入失败: {e}")
    if backend == "local":
        try:
            from build_index import _get_local_model
            v = _get_local_model().encode([query], normalize_embeddings=True,
                                          show_progress_bar=False)
            return v[0].tolist()
        except Exception as e:
            raise SemanticError(f"本地模型 query 嵌入失败: {e}")
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


def translate_to_english(query: str) -> Optional[str]:
    """LLM 把中文意图翻译成英文检索表达；失败返回 None（单路回退）。"""
    try:
        from ark_client import ArkChat
        out = ArkChat().chat(
            f"把下面的检索意图翻译成一句用于向量检索的英文描述，只输出英文本身:\n{query}",
            max_tokens=128,
        ).strip()
        return out or None
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 英文翻译失败，单中文查询回退: {e}", file=sys.stderr)
        return None


def _fuse_knn(hit_lists: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """多路 kNN 结果 RRF 融合：score = Σ 1/(60+rank)，距离取各路最小值。"""
    rrf: Dict[str, float] = {}
    best_dist: Dict[str, float] = {}
    for hits in hit_lists:
        for rank, h in enumerate(hits):
            rrf[h["id"]] = rrf.get(h["id"], 0.0) + 1.0 / (60 + rank + 1)
            d = h["distance"]
            if h["id"] not in best_dist or d < best_dist[h["id"]]:
                best_dist[h["id"]] = d
    ordered = sorted(rrf.items(), key=lambda kv: -kv[1])
    return [{"id": rid, "distance": best_dist[rid], "_rrf": s} for rid, s in ordered]


def semantic_search(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    min_stars: int = DEFAULT_MIN_STARS,
    exclude_fork: bool = True,
    exclude_archived: bool = True,
    db_path: Optional[str] = None,
    model: str = EMBED_MODEL,
    star_weight: float = DEFAULT_STAR_WEIGHT,
    dual_query: bool = False,
    backend: str = "pinecone",
) -> Dict[str, Any]:
    """主流程：嵌入 → kNN 召回 → 在线拉最新 stars → 过滤 → 排序 → 截断输出。

    star_weight > 0 时按混合分排序（语义距离 − star 先验），并把 kNN 召回窗口
    放宽到 top_k×10（让头部项目有进入候选池的机会）；= 0 时回退纯距离排序。
    """
    conn = connect(db_path)
    qvec = embed_query(query, model, backend)
    if len(qvec) != EMBED_DIM:
        raise SemanticError(f"query 向量维度 {len(qvec)} != {EMBED_DIM}")

    # kNN 召回（sqlite-vec 返回按距离升序）。
    # 混合模式下窗口放宽到 top_k×10：star 先验只能重排已召回的候选，
    # 窗口太浅时头部项目（如 alist 距离 1.25 > 第100名的 ~1.19）根本进不了池子。
    # sqlite-vec 是全库暴力扫描，取 500 与取 100 成本几乎相同。
    knn_k = max(top_k * 10, 500) if star_weight > 0 else max(top_k * 2, 20)

    # 各查询路: 中文(必有) + 英文(--dual-query)
    hit_lists: List[List[Dict[str, Any]]] = [search_knn(conn, qvec, k=knn_k)]
    en_text = None
    if dual_query:
        en_text = translate_to_english(query)
        if en_text:
            print(f"[dual] EN: {en_text}", file=sys.stderr)
            hit_lists.append(search_knn(conn, embed_query(en_text, model, backend),
                                        k=knn_k))

    # README 双通道: 每路结果与 README 表按 id 取最小距离（同模型同空间可直接比较）
    try:
        has_readme = count_readme_vectors(conn) > 0
    except Exception:
        has_readme = False
    if has_readme:
        def _with_readme(hl):
            merged = {h["id"]: h["distance"] for h in hl}
            for h in search_knn(conn, qvec, k=knn_k, table="repo_readme_vectors"):
                rid, d = h["id"], h["distance"]
                if rid not in merged or d < merged[rid]:
                    merged[rid] = d
            return sorted(({"id": i, "distance": d} for i, d in merged.items()),
                          key=lambda x: x["distance"])
        hit_lists = [_with_readme(hl) for hl in hit_lists]

    hits = _fuse_knn(hit_lists) if len(hit_lists) > 1 else hit_lists[0]

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

    # 排序：混合分（语义距离 − star 先验）或纯语义距离，截断到 top_k
    if star_weight > 0:
        for c in out:
            c["_score"] = round(hybrid_score(c["_semantic_distance"], c["stars"], star_weight), 4)
        out.sort(key=lambda c: c["_score"])
        note = (f"混合排序：score = 语义距离 − {star_weight}·log10(1+stars)，"
                "兼顾语义相关性与项目成熟度；--pure-semantic 可回退纯距离排序。")
    else:
        out.sort(key=lambda c: c.get("_semantic_distance", 1e9))
        note = "纯语义排序（距离升序），star 仅作过滤不作排序（避免 star 淹没语义差异）。"
    out = out[:top_k]
    return {
        "query": query,
        "mode": "semantic",
        "min_stars": min_stars,
        "star_weight": star_weight,
        "recalled": recalled,
        "candidates": len(out),
        "candidates_list": out,
        "note": "语义召回（name/description/topics 向量匹配）。" + note,
    }


def main():
    parser = argparse.ArgumentParser(description="语义召回")
    parser.add_argument("--query", required=True, help="用户检索意图（自然语言）")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                        help=f"kNN 召回数（默认 {DEFAULT_TOP_K}）")
    parser.add_argument("--min-stars", type=int, default=DEFAULT_MIN_STARS,
                        help=f"最小 star（默认 {DEFAULT_MIN_STARS}）")
    parser.add_argument("--star-weight", type=float, default=DEFAULT_STAR_WEIGHT,
                        help=f"star 先验权重（默认 {DEFAULT_STAR_WEIGHT}，0=纯语义距离排序）")
    parser.add_argument("--pure-semantic", action="store_true",
                        help="回退纯语义距离排序（等价 --star-weight 0）")
    parser.add_argument("--dual-query", action="store_true",
                        help="中英双语查询 RRF 融合（LLM 翻译，需 ARK_API_KEY）")
    parser.add_argument("--backend", choices=["pinecone", "ark", "local"],
                        default=os.environ.get("GH_SEARCH_BACKEND", "pinecone"),
                        help="查询嵌入后端（须与目标库向量模型一致；默认取 GH_SEARCH_BACKEND）")
    parser.add_argument("--db", default=None, help="sqlite 路径")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = parser.parse_args()

    star_weight = 0.0 if args.pure_semantic else args.star_weight
    try:
        result = semantic_search(args.query, args.top_k, args.min_stars,
                                 db_path=args.db, star_weight=star_weight,
                                 dual_query=args.dual_query, backend=args.backend)
    except SemanticError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"语义召回: {result['candidates']} 条候选（kNN {result['recalled']}）")
        for c in result["candidates_list"][:10]:
            score = f" score={c['_score']}" if "_score" in c else ""
            dist = f" dist={c['_semantic_distance']}"
            print(f"  {c['stars']:>6}★ {c['full_name']:<45}{dist}{score}")


if __name__ == "__main__":
    main()