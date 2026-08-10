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
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from github_client import GitHubClient

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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_search_query(
    language: str,
    min_stars: int,
    pushed_since: str,
) -> str:
    """构造 Step1 GraphQL search 查询串。"""
    parts = []
    if language:
        parts.append(f"language:{language}")
    parts.append("is:public")
    parts.append("fork:false")          # 关键：不是 `not:fork`
    parts.append("archived:false")
    parts.append(f"stars:>{min_stars}")
    parts.append(f"pushed:>={pushed_since}")
    return " ".join(parts)


def step1_recall(
    client: GitHubClient,
    language: str,
    min_stars: int,
    max_recalls: int,
) -> List[Dict[str, Any]]:
    """Step1：分页召回原始仓库列表。"""
    pushed_since = (_now() - timedelta(days=DEFAULT_STALE_DAYS)).strftime("%Y-%m-%d")
    query = build_search_query(language, min_stars, pushed_since)

    repos: List[Dict[str, Any]] = []
    cursor: str | None = None
    has_next = True

    while has_next and len(repos) < max_recalls:
        after = f', after: "{cursor}"' if cursor else ""
        quoted = json.dumps(query)
        gql = (
            "query { search(query: %s, type: REPOSITORY, "
            "first: %d%s) { repositoryCount pageInfo { hasNextPage endCursor } "
            "nodes { ... on Repository { %s } } } }"
            % (quoted, SEARCH_PAGE_SIZE, after, SEARCH_FIELDS)
        )
        data = client.graphql(gql)
        search = data.get("search", {})
        nodes = search.get("nodes", [])
        repos.extend(nodes)
        page_info = search.get("pageInfo", {})
        has_next = page_info.get("hasNextPage", False)
        cursor = page_info.get("endCursor")

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
    parser.add_argument("--query", required=True, help="用户检索意图（自然语言）")
    parser.add_argument("--language", default=None, help="限定编程语言")
    parser.add_argument("--min-stars", type=int, default=DEFAULT_MIN_STARS,
                        help=f"star 最低阈值（默认 {DEFAULT_MIN_STARS}）")
    parser.add_argument("--max-recalls", type=int, default=DEFAULT_MAX_RECALLS,
                        help=f"最大召回条数（默认 {DEFAULT_MAX_RECALLS}）")
    parser.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS,
                        help=f"超过该天数未推送视为僵尸（默认 {DEFAULT_STALE_DAYS}）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = parser.parse_args()

    client = GitHubClient()

    try:
        raw = step1_recall(client, args.language, args.min_stars, args.max_recalls)
    except Exception as e:  # noqa: BLE001
        print(f"❌ Step1 召回失败：{e}", file=sys.stderr)
        sys.exit(1)

    if not raw:
        print("⚠️ 未召回任何仓库。可尝试放宽 star 阈值 / 语言 / 活跃窗口。", file=sys.stderr)
        sys.exit(0)

    candidates = step2_coarse_filter(raw, args.stale_days)
    normalized = [_normalize(r) for r in candidates]

    result = {
        "query": args.query,
        "language": args.language,
        "min_stars": args.min_stars,
        "recalled": len(raw),
        "candidates": len(normalized),
        "candidates_list": normalized,
        "note": ("description 为空的仓库已保留，未作为丢弃条件；"
                 "语义匹配由 LLM 在后续步骤判断。"),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Step1 召回: {len(raw)} 条 → Step2 裁剪后: {len(normalized)} 条候选")
        print("（候选已输出，含元数据；description 为空者保留）")
        print(json.dumps(normalized, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()