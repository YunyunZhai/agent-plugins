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
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("rerank_results")

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_MODEL = "qwen3.7-text-rerank"
DEFAULT_TOP_N = 50
BATCH_SIZE = 100
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
            r = requests.post(url, headers=headers, json=body, timeout=60)
            r.raise_for_status()
            data = r.json()
            return data.get("results", [])
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
    raise RerankError(f"rerank API 调用失败（{MAX_RETRIES} 次重试后）: {last_err}")


def rerank(
    input_path: str,
    query: str,
    top_n: int = DEFAULT_TOP_N,
    model: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
) -> Dict[str, Any]:
    """主流程：加载候选 → 构造文档 → 调 rerank API → 写回分数 → 排序输出。

    失败时降级为原始顺序，不抛异常。
    """
    log.debug("=== rerank START ===")
    log.debug("query: %s", query)

    # 加载候选
    with open(input_path) as f:
        raw = json.load(f)
    candidates = raw.get("results") or raw.get("candidates_list") or []
    if not candidates:
        log.debug("输入为空，跳过 rerank")
        return {"query": query, "reranked": False, "input_count": 0,
                "output_count": 0, "results": []}

    log.debug("loaded %d candidates from %s", len(candidates), input_path)

    # 检查环境变量（显式参数优先，其次读环境变量）
    api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
    endpoint = endpoint or os.environ.get("DASHSCOPE_RERANK_URL", "")
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

    # 构造文档
    docs = [_build_document(c) for c in candidates]
    log.debug("built %d documents", len(docs))

    # 分批调 API
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

        # 将 API 结果写回候选记录
        for item in api_results:
            idx = item.get("index", 0)
            score = item.get("relevance_score", 0)
            if 0 <= idx < len(batch_candidates):
                batch_candidates[idx]["_rerank_score"] = round(score, 4)
        all_results.extend(batch_candidates)

    # 按 rerank 分数降序排列
    all_results.sort(key=lambda c: c.get("_rerank_score", 0), reverse=True)

    # 截断到 top_n
    output = all_results[:top_n]

    log.debug("rerank done: %d → %d (top_n=%d)", len(candidates), len(output), top_n)
    for i, c in enumerate(output[:5]):
        log.debug("  #%d: %s score=%.4f", i + 1, c["full_name"],
                  c.get("_rerank_score", 0))

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

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(message)s",
            datefmt="%H:%M:%S",
            stream=sys.stderr,
        )
    from _common.logsetup import setup as _setup_log
    print(f"[log] {_setup_log(log, stderr_debug=args.debug)}", file=sys.stderr)

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
