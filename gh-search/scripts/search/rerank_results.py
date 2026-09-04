#!/usr/bin/env python3
"""
第 4.5 步（可选）：Rerank 精排 —— 调用百炼 qwen3.7-text-rerank 模型对候选项目做精细化二次排序。

在 Step 3（成熟度指标）或 Step 4（README 增强）之后、LLM 最终排序之前执行。
对候选集按 query-document 相关性重新排序，输出带 _rerank_score 的结果。

用法:
    python3 rerank_results.py --input step3.json --query "启动快的编码智能体" --json
    python3 rerank_results.py --input step4.json --query "..." --top-n 20 --json
    python3 rerank_results.py --repos "a/x,b/y" --query "..." --json

环境变量:
    DASHSCOPE_API_KEY     - 百炼 API Key（必需）
    DASHSCOPE_RERANK_URL  - rerank API 端点（必需），
                            格式 https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com

失败时自动降级：无 API key 或调用失败 → 跳过 rerank，输出原始顺序。
"""

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("rerank_results")

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_MODEL = "qwen3.7-text-rerank"
DEFAULT_TOP_N = 50
BATCH_SIZE = 500
MAX_RETRIES = 3
RETRY_DELAY = 2


class RerankError(RuntimeError):
    """Rerank 失败"""


# GitHub 网页端 description 字段硬性上限；超过此长度的为 API 绕过写入的垃圾内容，
# 与 _common/sqlite_store.py 的 MAX_DESC_CHARS 保持一致，避免超长文本撑爆 rerank 输入。
MAX_DESC_CHARS = 350


def _build_document(repo: Dict[str, Any]) -> str:
    """将候选仓库记录拼接为 reranker 输入文档文本。

    对 description 做硬截断：单条 document 不能超过 rerank 服务上限（实测
    `qwen3.7-text-rerank` 单条超 50000 会报 InvalidParameter）。topics 保留前 10 个。
    """
    name = repo.get("full_name", "")
    desc = (repo.get("description") or "").strip()
    if len(desc) > MAX_DESC_CHARS:
        desc = desc[:MAX_DESC_CHARS]
    topics = repo.get("topics") or []
    if isinstance(topics, str):
        try:
            topics = json.loads(topics)
        except Exception:
            topics = []
    parts = [name]
    if desc:
        parts.append(desc)
    if topics:
        parts.append("Topics: " + ", ".join(topics[:10]))
    readme = (repo.get("readme_snippet") or "").strip()
    if readme:
        parts.append("README: " + readme[:2000])
    return ". ".join(parts)


def _call_rerank_api(
    query: str,
    documents: List[str],
    api_key: str,
    endpoint: str,
    model: str = DEFAULT_MODEL,
) -> List[Dict[str, Any]]:
    """调用百炼 OpenAI 兼容 rerank API，返回按相关性排序的结果列表。"""
    import requests

    url = f"{endpoint.rstrip('/')}/compatible-api/v1/reranks"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "query": query,
        "documents": documents,
        "top_n": len(documents),
    }

    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            t_req = time.monotonic()
            r = requests.post(url, headers=headers, json=body, timeout=60)
            r.raise_for_status()
            data = r.json()
            log.debug("rerank API attempt %d/%d ok in %.2fs (%d docs)",
                      attempt + 1, MAX_RETRIES, time.monotonic() - t_req, len(documents))
            return data.get("results", [])
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY * (attempt + 1)
                log.warning("rerank API attempt %d/%d failed, retrying in %.0fs: %s",
                            attempt + 1, MAX_RETRIES, delay, e)
                time.sleep(delay)
    raise RerankError(f"rerank API 调用失败（{MAX_RETRIES} 次重试后）: {last_err}")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _norm_log(value: Any, percentile: float = 1.0, floor: float = 0.0) -> float:
    num = max(_safe_float(value, 0.0), floor)
    if percentile <= 0:
        return 0.0
    return min(1.0, math.log1p(num) / max(math.log1p(percentile), 1e-9))


def _recency_score(pushed_at: Any) -> float:
    if not pushed_at:
        return 0.0
    try:
        if isinstance(pushed_at, str):
            if pushed_at.endswith("Z"):
                pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
            else:
                pushed = datetime.fromisoformat(pushed_at)
        else:
            pushed = datetime.fromisoformat(str(pushed_at))
        if pushed.tzinfo is None:
            pushed = pushed.replace(tzinfo=timezone.utc)
        days = max((datetime.now(timezone.utc) - pushed).days, 0)
        return max(0.0, 1.0 - min(days / 365.0, 1.0))
    except (TypeError, ValueError):
        return 0.0


def fetch_repo_maturity_metrics(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """批量补齐 GitHub 官方成熟度字段：stars / forks / watchers / subscribers / recency / archive."""
    if not candidates:
        return candidates

    names: List[str] = []
    seen = set()
    for repo in candidates:
        full_name = repo.get("full_name") or repo.get("nameWithOwner")
        if not full_name or full_name in seen:
            continue
        seen.add(full_name)
        names.append(full_name)
    if not names:
        return candidates

    try:
        from _common.github_client import GitHubClient
    except Exception:
        return candidates

    fragments: List[str] = []
    for idx, full_name in enumerate(names):
        owner, _, name = full_name.partition("/")
        if not name:
            continue
        fragments.append(
            f'r{idx}: repository(owner: "{owner}", name: "{name}") {{ '
            'nameWithOwner '
            'isArchived '
            'createdAt '
            'updatedAt '
            'pushedAt '
            'stargazerCount '
            'watcherCount '
            'forkCount '
            'subscribers { totalCount } '
            'issues(states: OPEN) { totalCount } '
            '}}'
        )
    if not fragments:
        return candidates

    gql = "query { " + " ".join(fragments) + " }"
    try:
        client = GitHubClient()
        data = client.graphql(gql)
    except Exception as e:
        log.warning("GitHub maturity enrichment failed, continuing without metadata: %s", e)
        return candidates

    by_name = {}
    for idx, full_name in enumerate(names):
        repo = data.get(f"r{idx}")
        if not repo:
            continue
        by_name[full_name] = {
            "archived": bool(repo.get("isArchived")),
            "created_at": repo.get("createdAt"),
            "updated_at": repo.get("updatedAt"),
            "pushed_at": repo.get("pushedAt"),
            "stargazers_count": _safe_int(repo.get("stargazerCount"), 0),
            "watchers_count": _safe_int(repo.get("watcherCount"), 0),
            "forks_count": _safe_int(repo.get("forkCount"), 0),
            "subscribers_count": _safe_int((repo.get("subscribers") or {}).get("totalCount"), 0),
            "open_issues_count": _safe_int((repo.get("issues") or {}).get("totalCount"), 0),
        }

    enriched: List[Dict[str, Any]] = []
    for repo in candidates:
        full_name = repo.get("full_name") or repo.get("nameWithOwner")
        merged = dict(repo)
        if full_name in by_name:
            merged.update(by_name[full_name])
        enriched.append(merged)
    return enriched


def compute_repo_maturity(repo: Dict[str, Any], percentiles: Optional[Dict[str, float]] = None) -> float:
    """轻量成熟度分：用于 rerank 后的二次排序补正，不覆盖主相关性。

    公式：0.38*stars + 0.18*forks + 0.12*watchers + 0.10*subscribers + 0.17*recency + 0.05*issue
    其中所有分量都做对数压缩与归一化，确保大项目不会压过小而准的项目。
    """
    if not isinstance(repo, dict):
        return 0.0

    if repo.get("archived") is True or repo.get("is_archived") is True:
        return 0.0

    percentiles = percentiles or {
        "stars": 10000.0,
        "forks": 2000.0,
        "watchers": 3000.0,
        "subscribers": 1000.0,
        "issues": 500.0,
    }

    stars = _norm_log(repo.get("stargazers_count", repo.get("stars", 0)), percentiles.get("stars", 10000.0))
    forks = _norm_log(repo.get("forks_count", repo.get("forks", 0)), percentiles.get("forks", 2000.0))
    watchers = _norm_log(repo.get("watchers_count", repo.get("watchers", 0)), percentiles.get("watchers", 3000.0))
    subscribers = _norm_log(repo.get("subscribers_count", repo.get("subscribers", 0)), percentiles.get("subscribers", 1000.0))
    issues = _norm_log(repo.get("open_issues_count", repo.get("open_issues", 0)), percentiles.get("issues", 500.0))
    recency = _recency_score(repo.get("pushed_at", repo.get("pushedAt")))

    maturity = (
        0.38 * stars +
        0.18 * forks +
        0.12 * watchers +
        0.10 * subscribers +
        0.17 * recency +
        0.05 * min(1.0, issues)
    )
    return max(0.0, min(1.0, maturity))


def apply_maturity_rerank(candidates: List[Dict[str, Any]], maturity_lambda: float = 0.10) -> List[Dict[str, Any]]:
    """在 rerank 结果上做轻量成熟度修正：仍以 rerank_score 为主排序，maturity 仅做微调。"""
    if not candidates:
        return candidates

    scored = []
    for repo in candidates:
        rerank_score = _safe_float(repo.get("_rerank_score", repo.get("rerank_score", 0.0)), 0.0)
        maturity = compute_repo_maturity(repo)
        repo = dict(repo)
        repo["_maturity_score"] = round(maturity, 6)
        repo["_final_score"] = round(rerank_score + maturity_lambda * maturity, 6)
        scored.append(repo)

    scored.sort(key=lambda r: r.get("_final_score", 0.0), reverse=True)
    return scored


def rerank(
    input_path: str,
    query: str,
    top_n: int = DEFAULT_TOP_N,
    model: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
    maturity_lambda: float = 0.10,
) -> Dict[str, Any]:
    """主流程：加载候选 → 构造文档 → 调 rerank API → 写回分数 → 按 rerank + maturity 重新排序。"""
    log.debug("=== rerank START ===")
    log.debug("query: %s", query)

    with open(input_path) as f:
        raw = json.load(f)
    candidates = raw.get("results") or raw.get("candidates_list") or []
    if not candidates:
        log.debug("输入为空，跳过 rerank")
        return {"query": query, "reranked": False, "input_count": 0,
                "output_count": 0, "results": []}

    log.debug("loaded %d candidates from %s", len(candidates), input_path)

    api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
    endpoint = endpoint or os.environ.get("DASHSCOPE_RERANK_URL",
                                          os.environ.get("DASHSCOPE_BASE_URL", "").replace("/compatible-mode/v1", ""))
    if not api_key or not endpoint:
        log.debug("missing DASHSCOPE_API_KEY or DASHSCOPE_RERANK_URL, skip rerank")
        print("⚠️ 未配置 DASHSCOPE_API_KEY / DASHSCOPE_RERANK_URL，跳过 rerank",
              file=sys.stderr)
        return {
            "query": query,
            "reranked": False,
            "input_count": len(candidates),
            "output_count": len(candidates),
            "results": candidates,
            "note": "rerank 跳过：缺少环境变量",
        }

    docs = [_build_document(c) for c in candidates]
    log.debug("built %d documents", len(docs))

    all_results: List[Dict[str, Any]] = []
    for i in range(0, len(candidates), BATCH_SIZE):
        batch_docs = docs[i:i + BATCH_SIZE]
        batch_candidates = candidates[i:i + BATCH_SIZE]
        log.debug("rerank batch %d-%d (%d docs)", i, i + len(batch_docs), len(batch_docs))
        try:
            t0 = time.monotonic()
            api_results = _call_rerank_api(query, batch_docs, api_key, endpoint, model)
            elapsed = time.monotonic() - t0
            log.debug("rerank batch done in %.2fs, got %d results", elapsed, len(api_results))
        except RerankError as e:
            print(f"⚠️ {e}", file=sys.stderr)
            print("⚠️ rerank 失败，使用原始顺序", file=sys.stderr)
            return {
                "query": query,
                "reranked": False,
                "input_count": len(candidates),
                "output_count": len(candidates),
                "results": candidates,
                "note": f"rerank 失败: {e}",
            }

        for item in api_results:
            idx = item.get("index", 0)
            score = item.get("relevance_score", 0)
            if 0 <= idx < len(batch_candidates):
                batch_candidates[idx]["_rerank_score"] = round(score, 4)
        all_results.extend(batch_candidates)

    rank_before = {c["full_name"]: i for i, c in enumerate(candidates)}
    all_results = fetch_repo_maturity_metrics(all_results)
    all_results = apply_maturity_rerank(all_results, maturity_lambda=maturity_lambda)
    output = all_results[:top_n]

    moves = []
    for i, c in enumerate(output[:max(5, top_n)]):
        before = rank_before.get(c["full_name"])
        if before is None:
            continue
        delta = before - i
        marker = f"▲{delta}" if delta > 0 else (f"▼{-delta}" if delta < 0 else "=")
        moves.append(f"{marker}{c['full_name']}")
    if moves:
        log.debug("rerank rank-moves (↓ up / ↑ down): %s", " ".join(moves))

    log.debug("rerank done: %d → %d (top_n=%d)", len(candidates), len(output), top_n)
    for i, c in enumerate(output[:5]):
        log.debug("  #%d: %s rerank=%.4f final=%.4f maturity=%.4f",
                  i + 1, c["full_name"], c.get("_rerank_score", 0), c.get("_final_score", 0), c.get("_maturity_score", 0))

    return {
        "query": query,
        "reranked": True,
        "model": model,
        "input_count": len(candidates),
        "output_count": len(output),
        "results": output,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="第4.5步：Rerank 精排")
    parser.add_argument("--input", required=True, help="Step 3/4 输出的 JSON")
    parser.add_argument("--query", required=True, help="用户检索意图")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N,
                        help=f"输出条数上限（默认 {DEFAULT_TOP_N}）")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"rerank 模型（默认 {DEFAULT_MODEL}）")
    parser.add_argument("--api-key", default=None, help="百炼 API Key（默认读环境变量）")
    parser.add_argument("--endpoint", default=None, help="rerank API 端点（默认读环境变量）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    parser.add_argument("--debug", action="store_true", help="输出调试日志到 stderr")
    args = parser.parse_args()

    from _common.logsetup import load_logging_config, setup as _setup_log
    log_cfg = load_logging_config()
    if args.debug:
        log_cfg["level"] = "debug"
        log_cfg["console"] = True
    print(f"[log] {_setup_log(log, **log_cfg)}", file=sys.stderr)

    result = rerank(
        args.input, args.query, args.top_n, args.model,
        api_key=args.api_key, endpoint=args.endpoint,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("reranked"):
            print(f"Rerank: {result['input_count']} 条 → 排序后 {result['output_count']} 条")
            for c in result["results"][:10]:
                score = c.get("_rerank_score", 0)
                print(f"  {score:.4f} {c['full_name']}")
        else:
            print(f"Rerank 跳过: {result.get('note', '未知原因')}")
            for c in result["results"][:10]:
                print(f"  {c['full_name']}")


if __name__ == "__main__":
    main()
