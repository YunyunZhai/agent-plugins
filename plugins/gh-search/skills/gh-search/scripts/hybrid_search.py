#!/usr/bin/env python3
"""
通道 3：并行合并搜索（关键词 + 语义 union）。

同时调用 search_repos.py（关键词通道）和 semantic_search.py（语义通道），
将两边结果按 union 合并去重，输出统一的 candidates_list。

用法:
    python3 hybrid_search.py --query "启动快的编码智能体" --json
    python3 hybrid_search.py --query "python 安全扫描" --top-k 20 --json

依赖:
    search_repos.py, semantic_search.py（同目录）
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

log = logging.getLogger("hybrid_search")

sys.path.insert(0, str(Path(__file__).parent))


def hybrid_search(
    query: str,
    top_k: int = 50,
    min_stars: int = 0,
    language: str | None = None,
    star_weight: float = 0.03,
    backend: str = "local",
    db_path: str | None = None,
) -> Dict[str, Any]:
    """并行调用关键词通道和语义通道，union 合并结果。"""
    from search_repos import main as _  # noqa: F401 – ensure importable

    log.debug("=== hybrid_search START ===")
    log.debug("query: %s", query)
    log.debug("params: top_k=%d min_stars=%d language=%s star_weight=%.3f backend=%s",
              top_k, min_stars, language, star_weight, backend)

    results: Dict[str, Dict[str, Any]] = {}

    # ── 通道 1: 关键词 ──
    log.debug("--- channel 1: keyword search ---")
    t0 = time.monotonic()
    try:
        from search_repos import step1_recall, step2_coarse_filter, _normalize
        from github_client import GitHubClient

        client = GitHubClient()
        raw = step1_recall(client, query, language, min_stars, top_k * 3)
        filtered = step2_coarse_filter(raw, 180)
        keyword_candidates = [_normalize(r) for r in filtered]
        log.debug("keyword: %d raw → %d filtered in %.2fs",
                  len(raw), len(keyword_candidates), time.monotonic() - t0)
        for i, c in enumerate(keyword_candidates[:5]):
            log.debug("  keyword #%d: %s stars=%d desc=%s",
                      i+1, c["full_name"], c["stars"],
                      (c.get("description") or "")[:80])
        results["keyword"] = {
            "candidates": keyword_candidates,
            "elapsed": time.monotonic() - t0,
        }
    except Exception as e:
        log.error("keyword search failed: %s", e)
        results["keyword"] = {"candidates": [], "error": str(e)}

    # ── 通道 2: 语义 ──
    log.debug("--- channel 2: semantic search ---")
    t1 = time.monotonic()
    try:
        from semantic_search import semantic_search

        sem_result = semantic_search(
            query, top_k=top_k, min_stars=min_stars,
            db_path=db_path, star_weight=star_weight, backend=backend,
        )
        semantic_candidates = sem_result.get("candidates_list", [])
        log.debug("semantic: %d candidates in %.2fs (kNN %d)",
                  len(semantic_candidates), time.monotonic() - t1,
                  sem_result.get("recalled", 0))
        for i, c in enumerate(semantic_candidates[:5]):
            log.debug("  semantic #%d: %s stars=%d dist=%.4f desc=%s",
                      i+1, c["full_name"], c["stars"],
                      c.get("_semantic_distance", 0),
                      (c.get("description") or "")[:80])
        results["semantic"] = {
            "candidates": semantic_candidates,
            "elapsed": time.monotonic() - t1,
        }
    except Exception as e:
        log.error("semantic search failed: %s", e)
        results["semantic"] = {"candidates": [], "error": str(e)}

    # ── 合并: union 去重 ──
    log.debug("--- merging ---")
    merged: Dict[str, Dict[str, Any]] = {}
    sources: Dict[str, str] = {}

    for c in results.get("keyword", {}).get("candidates", []):
        name = c.get("full_name", "")
        if name:
            merged[name] = c
            sources[name] = "keyword"

    for c in results.get("semantic", {}).get("candidates", []):
        name = c.get("full_name", "")
        if name:
            if name in merged:
                sources[name] = "both"
            else:
                merged[name] = c
                sources[name] = "semantic"

    merged_list = list(merged.values())

    # 统计来源
    from_keyword = sum(1 for s in sources.values() if s == "keyword")
    from_semantic = sum(1 for s in sources.values() if s == "semantic")
    from_both = sum(1 for s in sources.values() if s == "both")
    log.debug("merge: keyword_only=%d semantic_only=%d both=%d total=%d",
              from_keyword, from_semantic, from_both, len(merged_list))

    # 截断
    merged_list = merged_list[:top_k]
    log.debug("final top %d:", len(merged_list))
    for i, c in enumerate(merged_list[:10]):
        log.debug("  #%d: %s stars=%d source=%s",
                  i+1, c["full_name"], c["stars"], sources.get(c["full_name"], "?"))

    return {
        "query": query,
        "mode": "hybrid",
        "min_stars": min_stars,
        "star_weight": star_weight,
        "recalled": {
            "keyword": len(results.get("keyword", {}).get("candidates", [])),
            "semantic": len(results.get("semantic", {}).get("candidates", [])),
        },
        "merge_stats": {
            "keyword_only": from_keyword,
            "semantic_only": from_semantic,
            "both": from_both,
            "total": len(merged_list),
        },
        "candidates": len(merged_list),
        "candidates_list": merged_list,
        "elapsed": {
            "keyword": results.get("keyword", {}).get("elapsed", 0),
            "semantic": results.get("semantic", {}).get("elapsed", 0),
        },
        "note": "并行合并：关键词 + 语义 union 去重。",
    }


def main():
    parser = argparse.ArgumentParser(description="通道3：并行合并搜索")
    parser.add_argument("--query", required=True, help="用户检索意图")
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--min-stars", type=int, default=0)
    parser.add_argument("--language", default=None)
    parser.add_argument("--star-weight", type=float, default=0.03)
    parser.add_argument("--backend", default="local")
    parser.add_argument("--db", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--debug", action="store_true")
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

    result = hybrid_search(
        args.query, args.top_k, args.min_stars, args.language,
        args.star_weight, args.backend, args.db,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        s = result.get("merge_stats", {})
        print(f"并行合并: keyword={s.get('keyword_only',0)} + semantic={s.get('semantic_only',0)} "
              f"+ both={s.get('both',0)} = {s.get('total',0)} 候选")
        for c in result["candidates_list"][:10]:
            print(f"  {c['stars']:>6}★ {c['full_name']}")


if __name__ == "__main__":
    main()
