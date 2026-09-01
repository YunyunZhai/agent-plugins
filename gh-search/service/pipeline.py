"""管线编排：根据 channel 参数调用搜索核心函数，串联 enrich/readme/rerank 步骤。"""

import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 确保 scripts 目录在 path 中
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))


def _keyword_search(
    query: str, language: Optional[str], min_stars: int, top_k: int,
) -> List[Dict[str, Any]]:
    """关键词通道：GraphQL 召回 + 粗筛。"""
    from search.search_repos import step1_recall, step2_coarse_filter, _normalize
    from _common.github_client import GitHubClient

    client = GitHubClient()
    raw = step1_recall(client, query, language or "", min_stars, top_k * 3)
    filtered = step2_coarse_filter(raw, 180)
    return [_normalize(r) for r in filtered]


def _semantic_search(
    query: str, min_stars: int, top_k: int, star_weight: float,
    backend: str = "local", db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """语义通道：sqlite-vec kNN。"""
    from search.semantic_search import semantic_search as _semantic_search_fn

    result = _semantic_search_fn(
        query, top_k=top_k, min_stars=min_stars,
        db_path=db_path, star_weight=star_weight, backend=backend,
    )
    return result.get("candidates_list", [])


def _hybrid_search(
    query: str, language: Optional[str], min_stars: int, top_k: int,
    star_weight: float, backend: str = "local", db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """并行通道：关键词 + 语义 union。"""
    from search.hybrid_search import hybrid_search as _hybrid_search_fn

    result = _hybrid_search_fn(
        query, top_k=top_k, min_stars=min_stars, language=language,
        star_weight=star_weight, backend=backend, db_path=db_path,
    )
    return result.get("candidates_list", [])


def _enrich_metrics(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """成熟度指标过滤。"""
    from search.enrich_metrics import enrich, step3_filter
    from _common.github_client import GitHubClient

    client = GitHubClient()
    enriched = enrich(client, candidates)
    return step3_filter(enriched, min_commits_30d=3)


def _fetch_readme(candidates: List[Dict[str, Any]], max_chars: int = 2000) -> List[Dict[str, Any]]:
    """README 片段增强。"""
    from search.fetch_readme import enrich as enrich_readme
    from _common.github_client import GitHubClient

    client = GitHubClient()
    return enrich_readme(client, candidates, max_chars, 1200, 300)


def _rerank(
    candidates: List[Dict[str, Any]], query: str, top_n: int = 50,
) -> List[Dict[str, Any]]:
    """百炼 rerank 精排。"""
    from search.rerank_results import rerank as _rerank_fn
    import json
    import tempfile
    import os

    # rerank_results.py 需要文件输入，构造临时文件
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    try:
        json.dump({"results": candidates}, tmp, ensure_ascii=False)
        tmp.close()
        result = _rerank_fn(tmp.name, query, top_n=top_n)
        return result.get("results", [])
    finally:
        os.unlink(tmp.name)


def run_pipeline(
    query: str,
    channel: str = "keyword",
    language: Optional[str] = None,
    min_stars: int = 200,
    top_k: int = 50,
    star_weight: float = 0.03,
    do_enrich: bool = False,
    do_readme: bool = False,
    do_rerank: bool = False,
    backend: str = "local",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """执行完整搜索管线，返回结构化结果。"""
    t0 = time.time()
    pipeline_steps: List[str] = []
    elapsed: Dict[str, float] = {}

    # Step 1: 召回
    t_recall = time.time()
    if channel == "keyword":
        candidates = _keyword_search(query, language, min_stars, top_k)
    elif channel == "semantic":
        candidates = _semantic_search(query, min_stars, top_k, star_weight, backend, db_path)
    elif channel == "hybrid":
        candidates = _hybrid_search(query, language, min_stars, top_k, star_weight, backend, db_path)
    else:
        raise ValueError(f"Unknown channel: {channel}")
    pipeline_steps.append(f"recall({channel})")
    elapsed["recall"] = round(time.time() - t_recall, 3)

    # Step 2: 成熟度指标（可选）
    if do_enrich and candidates:
        t_enrich = time.time()
        candidates = _enrich_metrics(candidates)
        pipeline_steps.append("enrich")
        elapsed["enrich"] = round(time.time() - t_enrich, 3)

    # Step 3: README 片段（可选）
    if do_readme and candidates:
        t_readme = time.time()
        candidates = _fetch_readme(candidates)
        pipeline_steps.append("readme")
        elapsed["readme"] = round(time.time() - t_readme, 3)

    # Step 4: Rerank（可选）
    if do_rerank and candidates:
        t_rerank = time.time()
        candidates = _rerank(candidates, query, top_k)
        pipeline_steps.append("rerank")
        elapsed["rerank"] = round(time.time() - t_rerank, 3)

    elapsed["total"] = round(time.time() - t0, 3)

    return {
        "query": query,
        "channel": channel,
        "candidates": len(candidates),
        "candidates_list": candidates,
        "pipeline_steps": pipeline_steps,
        "elapsed": elapsed,
    }
