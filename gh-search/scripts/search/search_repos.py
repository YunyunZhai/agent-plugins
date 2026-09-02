#!/usr/bin/env python3
"""
第 1+2 步：GitHub 原始召回 + 内存粗筛（纯元数据，不读取 README）。

Step1  GraphQL Search 原始召回：按语言 / star 阈值 / 活跃窗口（最近 6 个月有推送）
       召回 200-400 条候选仓库。使用 `fork:false`（GraphQL 搜 n 不支持 `not:fork`，
       后者会静默返回 0 结果）。
Step2  内存粗筛：
       - 硬过滤：丢弃 fork / archived / 超过 6 个月未推送的仓库
       - 语义初筛：topics 关键词匹配用户领域；description 为空则【保留不丢弃】
       （description 的语义匹配由 LLM 在 SKILL.md 工作流中判断，本脚本不做）

用法:
    python3 search_repos.py --query "网络安全 安全扫描" --language python
    python3 search_repos.py --query "web framework" --min-stars 500 --max-recalls 300
    python3 search_repos.py --query "启动快 资源占用低 编码智能体" --language rust

输出: JSON，含原始召回数、裁剪后候选、以及逐条元数据。
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from pathlib import Path

log = logging.getLogger("search_repos")

sys.path.insert(0, str(Path(__file__).parent.parent))
from _common.github_client import GitHubClient

# 候选字段（GraphQL search 返回后无需额外请求）
SEARCH_FIELDS = """
nameWithOwner
description
stargazerCount
forkCount
pushedAt
createdAt
licenseInfo { name }
repositoryTopics(first: 10) { nodes { topic { name } } }
"""

# 默认过滤阈值
DEFAULT_MIN_STARS = 200
DEFAULT_STALE_DAYS = 180          # 超过 6 个月未推送视为僵尸
DEFAULT_MAX_RECALLS = 400
SEARCH_PAGE_SIZE = 100
MAX_QUERY_TERMS = 5               # GitHub search 布尔算子上限 5 个；每组 ≤5 词（4 个 OR）留余量
MIN_AND_RESULTS = 20              # AND 召回低于该数时，同组词降级 OR 重搜补池


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_search_query(
    keywords: str,
    language: str,
    min_stars: int,
    pushed_since: str,
    mode: str = "and",
) -> str:
    """构造 Step1 GraphQL search 查询串。

    2026-08 修复：此前 --query 从未拼进检索式（等于高星活跃仓库随机采样）。
    现在关键词始终参与搜索；GitHub 裸词会匹配 name/description/topics/readme。

    mode="and"：多词空格连接（GitHub 隐式 AND，要求全部命中）——池子小而准，
                但任一词不命中就整体为 0（分词/索引漂移易踩空）。
    mode="or" ：多词 OR 连接（任一命中即匹配）——池子大而杂，靠后续排序兜底。
    """
    kw_words = [kw for kw in keywords.split() if kw]
    if len(kw_words) > 1:
        joiner = " " if mode == "and" else " OR "
        kw_part = joiner.join(kw_words)
    else:
        kw_part = kw_words[0] if kw_words else ""
    filters = []
    if language:
        filters.append(f"language:{language}")
    filters.append("is:public")
    filters.append("fork:false")
    filters.append("archived:false")
    filters.append(f"stars:>{min_stars}")
    filters.append(f"pushed:>={pushed_since}")
    parts = [kw_part] + filters if kw_part else filters
    return " ".join(parts)


def chunk_keywords(keywords: str, size: int = MAX_QUERY_TERMS) -> List[str]:
    """把关键词串按 size 词一组分块。

    GitHub search 限制布尔算子（AND/OR/NOT）≤5 个：超出时 REST 报
    `422 More than five AND / OR / NOT operators`，GraphQL 更坑——静默返回
    0 条。分块后每组作为独立搜索变体跑，合并去重，不丢召回。
    """
    words = [w for w in keywords.split() if w]
    if not words:
        return [""]
    if len(words) <= size:
        return [" ".join(words)]
    return [" ".join(words[i:i + size]) for i in range(0, len(words), size)]


def expand_keywords(intent: str) -> Dict[str, List[str]]:
    """将关键词/意图转为搜索变体列表。

    由调用方（外层 AI 插件）负责把用户意图转写成关键词再传入，
    此函数直接透传，不做额外清洗。
    """
    return {"en": [intent], "zh": []}


def recall_variant(
    client: GitHubClient,
    keywords: str,
    language: str,
    min_stars: int,
    max_recalls: int,
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    """单组关键词召回：AND 优先（精准小池），不足 MIN_AND_RESULTS 时同词降级 OR 补池。

    还原旧版（空格连接=AND）的精准召回；AND 踩空（GitHub 分词/索引漂移、
    词组过窄）时自动用 OR 保底，两轮结果按仓库去重合并。
    返回 (仓库列表, 来源统计)；统计含 and/or 两轮原始条数，供上层汇总输出。
    """
    raw_and = step1_recall(client, keywords, language, min_stars, max_recalls, mode="and")
    if len(raw_and) >= MIN_AND_RESULTS:
        return raw_and, {"and": len(raw_and), "or": 0}
    raw_or = step1_recall(client, keywords, language, min_stars, max_recalls, mode="or")
    seen, uniq = set(), []
    for r in raw_and + raw_or:
        key = r.get("nameWithOwner")
        if key and key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq, {"and": len(raw_and), "or": len(raw_or)}


def step1_recall(
    client: GitHubClient,
    keywords: str,
    language: str,
    min_stars: int,
    max_recalls: int,
    mode: str = "and",
) -> List[Dict[str, Any]]:
    """Step1：分页召回原始仓库列表。mode 透传给 build_search_query（and/or）。"""
    pushed_since = (_now() - timedelta(days=DEFAULT_STALE_DAYS)).strftime("%Y-%m-%d")
    query = build_search_query(keywords, language, min_stars, pushed_since, mode)
    log.debug("Step1 query: %s", query)

    repos: List[Dict[str, Any]] = []
    cursor: str | None = None
    has_next = True
    page = 0

    while has_next and len(repos) < max_recalls:
        page += 1
        after = f', after: "{cursor}"' if cursor else ""
        quoted = json.dumps(query)
        gql = (
            "query { search(query: %s, type: REPOSITORY, "
            "first: %d%s) { repositoryCount pageInfo { hasNextPage endCursor } "
            "nodes { ... on Repository { %s } } } }"
            % (quoted, SEARCH_PAGE_SIZE, after, SEARCH_FIELDS)
        )
        t0 = time.monotonic()
        data = client.graphql(gql)
        elapsed = time.monotonic() - t0
        search = data.get("search", {})
        repo_count = search.get("repositoryCount", 0)
        nodes = search.get("nodes", [])
        repos.extend(nodes)
        page_info = search.get("pageInfo", {})
        has_next = page_info.get("hasNextPage", False)
        cursor = page_info.get("endCursor")
        log.debug("Step1 page %d: got %d nodes (total %d/%d), repo_count=%d, %.2fs",
                  page, len(nodes), len(repos), max_recalls, repo_count, elapsed)

    log.debug("Step1 done: %d raw repos", len(repos))
    return repos


def _is_stale(pushed_at: str, stale_days: int) -> bool:
    """判断是否超过 stale_days 未推送。"""
    try:
        pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return True  # 无法解析的日期保守视为过期
    age = (_now() - pushed).days
    return age > stale_days


def step2_coarse_filter(
    repos: List[Dict[str, Any]],
    stale_days: int,
) -> List[Dict[str, Any]]:
    """Step2：硬过滤（fork/archive/stale）+ 保留 description 为空的仓库。"""
    kept: List[Dict[str, Any]] = []
    dropped_fork = dropped_archived = dropped_stale = 0

    for r in repos:
        # 查询侧已过滤，双保险
        if r.get("isFork"):
            dropped_fork += 1
            continue
        if r.get("isArchived"):
            dropped_archived += 1
            continue
        if _is_stale(r.get("pushedAt", ""), stale_days):
            dropped_stale += 1
            continue
        kept.append(r)

    log.debug("Step2 filter: %d → kept=%d (dropped: fork=%d, archived=%d, stale=%d)",
              len(repos), len(kept), dropped_fork, dropped_archived, dropped_stale)
    return kept


def _normalize(repo: Dict[str, Any]) -> Dict[str, Any]:
    """把 GraphQL 节点转成简洁的候选记录。"""
    topics = [
        t.get("topic", {}).get("name", "")
        for t in repo.get("repositoryTopics", {}).get("nodes", [])
    ]
    topics = [t for t in topics if t]
    return {
        "full_name": repo.get("nameWithOwner", ""),
        "description": repo.get("description"),
        "topics": topics,
        "stars": repo.get("stargazerCount", 0),
        "forks": repo.get("forkCount", 0),
        "pushed_at": repo.get("pushedAt"),
        "created_at": repo.get("createdAt"),
        "license": (repo.get("licenseInfo") or {}).get("name"),
        "is_fork": repo.get("isFork", False),
        "is_archived": repo.get("isArchived", False),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="第1+2步：GitHub 召回与粗筛")
    parser.add_argument("--query", default=None,
                        help="用户检索意图（自然语言）。未提供 --group 时作为唯一搜索词")
    parser.add_argument("--group", action="append", dest="groups", default=None,
                        help="语义关键词组（调用方完成意图转写后传入），可重复；"
                             "提供时优先于 --query 作为搜索变体")
    parser.add_argument("--language", default=None, help="限定编程语言")
    parser.add_argument("--min-stars", type=int, default=DEFAULT_MIN_STARS,
                        help=f"star 最低阈值（默认 {DEFAULT_MIN_STARS}）")
    parser.add_argument("--max-recalls", type=int, default=DEFAULT_MAX_RECALLS,
                        help=f"最大召回条数（默认 {DEFAULT_MAX_RECALLS}）")
    parser.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS,
                        help=f"超过该天数未推送视为僵尸（默认 {DEFAULT_STALE_DAYS}）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    parser.add_argument("--debug", action="store_true", help="输出调试日志到 stderr")
    args = parser.parse_args()

    if not args.query and not args.groups:
        parser.error("需要 --query（用户检索意图）或至少一个 --group")
    intent = args.query or " ".join(args.groups)

    from _common.logsetup import load_logging_config, setup as _setup_log
    log_cfg = load_logging_config()
    if args.debug:
        log_cfg["level"] = "debug"
        log_cfg["console"] = True
    print(f"[log] {_setup_log(log, **log_cfg)}", file=sys.stderr)

    log.debug("=== search_repos START ===")
    log.debug("query: %s", intent)
    log.debug("params: language=%s min_stars=%d max_recalls=%d stale_days=%d",
              args.language, args.min_stars, args.max_recalls, args.stale_days)

    client = GitHubClient()

    try:
        # 关键词通道：分块（防布尔算子超限）→ 多路搜索 → 合并去重。
        # 意图→关键词转写、语义初筛与排序均由上层大模型（SKILL 工作流）负责，
        # 本脚本保持纯确定性 CLI，不做任何 LLM 调用。
        if args.groups:
            base_variants = [g.strip() for g in args.groups if g.strip()]
            log.debug("caller groups: %s", base_variants)
        else:
            kw = expand_keywords(intent)
            log.debug("expand_keywords: %s", kw)
            base_variants = kw["en"] + kw["zh"] or [intent]
        variants: List[str] = []
        for v in base_variants:
            variants.extend(chunk_keywords(v))
        per_cap = max(30, args.max_recalls // len(variants))
        log.debug("variants: %s (per_cap=%d)", variants, per_cap)
        print(f"[expand] 搜索变体: {variants}", file=sys.stderr)
        merged: Dict[str, Dict[str, Any]] = {}
        for v in variants:
            try:
                t0 = time.monotonic()
                raw, src = recall_variant(client, v, args.language, args.min_stars, per_cap)
                log.debug("variant '%s': Step1=%d repos in %.2fs", v, len(raw), time.monotonic() - t0)
            except Exception as e:  # noqa: BLE001
                print(f"⚠️ 变体「{v}」召回失败: {e}", file=sys.stderr)
                continue
            filtered = step2_coarse_filter(raw, args.stale_days)
            for r in filtered:
                n = _normalize(r)
                merged.setdefault(n["full_name"], n)
            src_line = f"AND={src['and']}" + (f"+OR补池={src['or']}" if src["or"] else "")
            print(f"[variant] 「{v}」{src_line} → 过滤后{len(filtered)} 累计{len(merged)}",
                  file=sys.stderr)
            log.debug("variant '%s': after merge %d unique", v, len(merged))
        normalized = list(merged.values())
        print(f"[done] {len(variants)} 组变体 → 合并去重 {len(normalized)} 条候选",
              file=sys.stderr)
        log.debug("merged: %d unique candidates", len(normalized))
        for i, c in enumerate(normalized[:5]):
            log.debug("  top #%d: %s stars=%d desc=%s",
                      i+1, c["full_name"], c["stars"],
                      (c.get("description") or "")[:80])
    except Exception as e:  # noqa: BLE001
        print(f"❌ Step1 召回失败：{e}", file=sys.stderr)
        sys.exit(1)

    if not normalized:
        print("⚠️ 未召回任何仓库。可尝试放宽 star 阈值 / 语言 / 活跃窗口。", file=sys.stderr)
        sys.exit(0)

    result = {
        "query": intent,
        "language": args.language,
        "min_stars": args.min_stars,
        "recalled": len(normalized),
        "candidates": len(normalized),
        "candidates_list": normalized,
        "note": "关键词组召回 + 分块(≤5词) + AND优先OR兜底；初筛与排序由上层大模型完成",
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"召回后: {len(normalized)} 条候选")
        print("（候选已输出，含元数据；description 为空者保留）")
        print(json.dumps(normalized, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()