#!/usr/bin/env python3
"""
在线语义召回（gh-search 三通道中的"语义通道"）。

输入自然语言 query → 嵌入模型向量化 → sqlite-vec kNN 召回 → 过滤 → 输出候选。

输出结构与 search_repos.py 完全一致（candidates_list 同构），便于 SKILL.md
在三通道（关键词 / 语义 / 并行）中 union 合并。

用法:
    python3 semantic_search.py --query "启动快的编码智能体" --json
    python3 semantic_search.py --query "python 安全扫描" --top-k 50 --min-stars 500 --json

环境变量:
    PINECONE_API_KEY  - Pinecone API 密钥(必需, 用于 query 嵌入)
    PINECONE_MODEL    - 嵌入模型(默认 llama-text-embed-v2)
    DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL
                     - 百炼 OpenAI 兼容端点密钥/BaseURL(--backend dashscope 必需)
    DASHSCOPE_MODEL   - 百炼嵌入模型(默认 qwen3.7-text-embedding)
    GH_SEARCH_DB      - sqlite 路径
"""

import argparse
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("semantic_search")

sys.path.insert(0, str(Path(__file__).parent))
from sqlite_store import (
    EMBED_DIM,
    EMBED_MODEL,
    connect,
    count_readme_vectors,
    count_vectors,
    get_repo,
    search_knn,
)

DEFAULT_TOP_K = 50
DEFAULT_MIN_STARS = 0
DEFAULT_STAR_WEIGHT = 0.03   # 混合排序: 每 10 倍 star 抵扣 0.03 个语义距离单位
                             # (λ 扫描实测: 0.03 使 alist/LitePan 同入全库前50;
                             #  0.08 会放行 mega-list 挤掉真相关小项目)

DASHSCOPE_MODEL = os.environ.get("DASHSCOPE_MODEL", "qwen3.7-text-embedding")
DASHSCOPE_INSTRUCT = ("Given a web search query, retrieve relevant passages "
                      "that answer the query\nQuery: {}")


class SemanticError(RuntimeError):
    """语义召回失败"""


def hybrid_score(distance: float, stars: int, star_weight: float) -> float:
    """混合分 = 语义距离 − star 先验（越小越好）。

    背景：元数据稀疏的头部项目（如 alist 描述只写 "file list/WebDAV"）在纯距离
    排序下会被关键词堆砌的长尾淹没。log10 先验让每 10 倍 star 抵扣 star_weight
    个距离单位，量级上足以救回头部项目、又不至于让无关大热门碾压相关小项目。
    """
    return distance - star_weight * math.log10(1 + max(stars, 0))


def _dashscope_embed(query: str) -> List[float]:
    """百炼 OpenAI 兼容端点嵌入 query（qwen3.7-text-embedding，text_type=query + instruct）。"""
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    base = os.environ.get("DASHSCOPE_BASE_URL", "")
    if not key or not base:
        raise SemanticError("未设置 DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL 环境变量")
    import requests
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    body = {"model": DASHSCOPE_MODEL, "input": query, "dimensions": EMBED_DIM,
            "text_type": "query", "instruct": DASHSCOPE_INSTRUCT.format(query)}
    last = None
    for attempt in range(6):
        try:
            r = requests.post(base + "/embeddings", headers=headers, json=body, timeout=60)
            r.raise_for_status()
            return r.json()["data"][0]["embedding"]
        except Exception as e:
            last = e
            if attempt == 5:
                break
            time.sleep(5 * (attempt + 1))
    raise SemanticError(f"百炼 query 嵌入失败: {last}")


def embed_query(query: str, model: str = EMBED_MODEL, backend: str = "pinecone") -> List[float]:
    """把用户 query 嵌入为向量。backend: pinecone | ark(方舟 doubao) | local(bge-m3) | dashscope(百炼 qwen)。"""
    if backend == "dashscope":
        return _dashscope_embed(query)
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
    best_source: Dict[str, str] = {}
    for hits in hit_lists:
        for rank, h in enumerate(hits):
            rrf[h["id"]] = rrf.get(h["id"], 0.0) + 1.0 / (60 + rank + 1)
            d = h["distance"]
            if h["id"] not in best_dist or d < best_dist[h["id"]]:
                best_dist[h["id"]] = d
                best_source[h["id"]] = h.get("_source", "repo")
    ordered = sorted(rrf.items(), key=lambda kv: -kv[1])
    return [{"id": rid, "distance": best_dist[rid], "_rrf": s,
             "_source": best_source.get(rid, "repo")} for rid, s in ordered]


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
    log.debug("=== semantic_search START ===")
    log.debug("query: %s", query)
    log.debug("params: top_k=%d min_stars=%d star_weight=%.3f exclude_fork=%s exclude_archived=%s backend=%s",
              top_k, min_stars, star_weight, exclude_fork, exclude_archived, backend)

    conn = connect(db_path)
    repo_count = count_vectors(conn)
    readme_count = 0
    try:
        readme_count = count_readme_vectors(conn)
    except Exception:
        pass
    log.debug("DB connected: %s  embed_model=%s  dim=%d", db_path or "(default)", EMBED_MODEL, EMBED_DIM)
    log.debug("DB stats: repo_vectors=%d  repo_readme_vectors=%d", repo_count, readme_count)

    t0 = time.monotonic()
    qvec = embed_query(query, model, backend)
    log.debug("query embed done in %.2fs, dim=%d", time.monotonic() - t0, len(qvec))
    if len(qvec) != EMBED_DIM:
        raise SemanticError(f"query 向量维度 {len(qvec)} != {EMBED_DIM}")

    # 混合模式窗口固定拉满到 vec0 上限 4096：star 先验只能重排已召回的候选，
    # 元数据稀疏的头部项目（alist 实测全库第 ~1400 名）必须先进池子才有的救。
    # sqlite-vec 是全库暴力扫描，深堆与浅堆成本几乎相同。
    knn_k = 4000 if star_weight > 0 else max(top_k * 2, 20)
    log.debug("knn_k=%d (star_weight=%.3f, top_k=%d)", knn_k, star_weight, top_k)

    # 各查询路: 中文(必有) + 英文(--dual-query)
    t0 = time.monotonic()
    hit_lists: List[List[Dict[str, Any]]] = [search_knn(conn, qvec, k=knn_k)]
    log.debug("repo_vectors kNN: %d hits in %.2fs (top1: %s dist=%.4f)",
              len(hit_lists[0]), time.monotonic() - t0,
              hit_lists[0][0]["id"] if hit_lists[0] else "-",
              hit_lists[0][0]["distance"] if hit_lists[0] else 0)
    for i, h in enumerate(hit_lists[0][:5]):
        log.debug("  repo channel #%d: %s dist=%.4f", i+1, h["id"], h["distance"])
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
    log.debug("README vectors: %s", f"{count_readme_vectors(conn)} rows" if has_readme else "none")
    if has_readme:
        t0 = time.monotonic()
        def _with_readme(hl):
            merged = {h["id"]: h["distance"] for h in hl}
            source = {h["id"]: "repo" for h in hl}
            repo_only = len(merged)
            for h in search_knn(conn, qvec, k=knn_k, table="repo_readme_vectors"):
                rid, d = h["id"], h["distance"]
                if rid not in merged or d < merged[rid]:
                    merged[rid] = d
                    source[rid] = "readme"
                elif rid not in source:
                    source[rid] = "repo"
            log.debug("README merge: repo_only=%d, after_merge=%d, new_from_readme=%d",
                      repo_only, len(merged), len(merged) - repo_only)
            return sorted(({"id": i, "distance": d, "_source": source.get(i, "repo")}
                           for i, d in merged.items()),
                          key=lambda x: x["distance"])
        hit_lists = [_with_readme(hl) for hl in hit_lists]
        log.debug("README merge done in %.2fs", time.monotonic() - t0)

    hits = _fuse_knn(hit_lists) if len(hit_lists) > 1 else hit_lists[0]
    log.debug("after fuse: %d hits", len(hits))

    # 收集候选 id（先排除 fork/archived 硬过滤）
    candidates: List[Dict[str, Any]] = []
    recalled = 0
    skipped_fork = 0
    skipped_archived = 0
    for h in hits:
        repo = get_repo(conn, h["id"])
        if not repo:
            continue
        recalled += 1
        if exclude_fork and repo.get("is_fork"):
            skipped_fork += 1
            continue
        if exclude_archived and repo.get("is_archived"):
            skipped_archived += 1
            continue
        candidates.append((repo, h["distance"], h.get("_source", "repo")))
    log.debug("candidates: recalled=%d, filtered(fork=%d, archived=%d), kept=%d",
              recalled, skipped_fork, skipped_archived, len(candidates))
    for i, (repo, dist, src) in enumerate(candidates[:10]):
        embed_text = (repo.get("embed_text") or "")[:120]
        readme_preview = (repo.get("readme_embed_text") or "")[:120]
        log.debug("  candidate #%d: %s dist=%.4f source=%s lang=%s",
                  i+1, repo.get("id"), dist, src, repo.get("primary_language"))
        log.debug("    embed_text: %s", embed_text)
        if readme_preview:
            log.debug("    readme_text: %s", readme_preview)
    if not candidates:
        return {"query": query, "mode": "semantic", "recalled": recalled,
                "candidates": 0, "candidates_list": [], "note": "无候选。"}

    # 打分用本地 star 快照（repos.stars, 允许周级陈旧）——深窗口下对数千候选
    # 逐个在线拉 star 不可行（130+ GraphQL 批次）；仅最终 top_k 在线刷新展示值
    out: List[Dict[str, Any]] = []
    skipped_stars = 0
    for repo, dist, _src in candidates:
        stars = int(repo.get("stars") or 0)
        if stars < min_stars:
            skipped_stars += 1
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
            "stars": stars,                              # 本地快照值（打分用）
            "_stars_live": None,
            "is_fork": bool(repo.get("is_fork")),
            "is_archived": bool(repo.get("is_archived")),
            "_semantic_distance": round(dist, 4),
        })

    # 排序：混合分（语义距离 − star 先验）或纯语义距离，截断到 top_k
    if star_weight > 0:
        for c in out:
            c["_score"] = round(hybrid_score(c["_semantic_distance"], c["stars"], star_weight), 4)
        out.sort(key=lambda c: c["_score"])
        note = (f"混合排序：score = 语义距离 − {star_weight}·log10(1+stars快照)，"
                "兼顾语义相关性与项目成熟度；--pure-semantic 可回退纯距离排序。")
    else:
        out.sort(key=lambda c: c.get("_semantic_distance", 1e9))
        note = "纯语义排序（距离升序），star 仅作过滤不作排序（避免 star 淹没语义差异）。"
    log.debug("before top_k truncate: %d candidates (min_stars filtered: %d)", len(out), skipped_stars)
    for i, c in enumerate(out[:5]):
        score = c.get("_score")
        score_str = f" score={score}" if score is not None else ""
        log.debug("  final #%d: %s dist=%.4f stars=%d%s",
                  i+1, c["full_name"], c["_semantic_distance"], c["stars"], score_str)
    out = out[:top_k]

    # 仅对最终 top_k 在线刷新实时 stars（展示精度；失败保留快照值）
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from github_client import GitHubClient
        client = GitHubClient()
        live = fetch_live_stars([c["full_name"] for c in out], client)
        for c in out:
            if c["full_name"] in live:
                c["stars"] = live[c["full_name"]]
                c["_stars_live"] = True
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 实时 stars 刷新失败，使用快照值: {e}", file=sys.stderr)

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
    parser.add_argument("--backend", choices=["pinecone", "ark", "local", "dashscope"],
                        default=os.environ.get("GH_SEARCH_BACKEND", "pinecone"),
                        help="查询嵌入后端（须与目标库向量模型一致；默认取 GH_SEARCH_BACKEND）")
    parser.add_argument("--db", default=None, help="sqlite 路径")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    parser.add_argument("--debug", action="store_true", help="输出调试日志到 stderr")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(message)s",
            datefmt="%H:%M:%S",
            stream=sys.stderr,
        )
    for noisy in ("pydot", "sentence_transformers", "urllib3", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    from logsetup import setup as _setup_log
    print(f"[log] {_setup_log(log, stderr_debug=args.debug)}", file=sys.stderr)

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