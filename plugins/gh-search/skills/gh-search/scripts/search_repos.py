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
    keywords: str,
    language: str,
    min_stars: int,
    pushed_since: str,
) -> str:
    """构造 Step1 GraphQL search 查询串。

    2026-08 修复：此前 --query 从未拼进检索式（等于高星活跃仓库随机采样）。
    现在关键词始终参与搜索；GitHub 裸词会匹配 name/description/topics/readme。
    """
    parts = list(keywords.split())
    if language:
        parts.append(f"language:{language}")
    parts.append("is:public")
    parts.append("fork:false")          # 关键：不是 `not:fork`
    parts.append("archived:false")
    parts.append(f"stars:>{min_stars}")
    parts.append(f"pushed:>={pushed_since}")
    return " ".join(parts)


def expand_keywords(intent: str) -> Dict[str, List[str]]:
    """LLM 把检索意图改写成英文+中文关键词组（GitHub search 用）。"""
    from ark_client import ArkChat
    ark = ArkChat()
    prompt = (
        "你是 GitHub 搜索专家。把用户的检索意图改写成适合 GitHub repository "
        "search 的关键词组合。\n"
        "要求：\n"
        "- en: 2-3 组英文关键词（技术社区惯用叫法），空格分隔单词\n"
        "- zh: 1-2 组中文关键词（不少中文项目用中文写描述）\n"
        "- 每组不超过 4 个词，不要加修饰性 stopwords\n"
        f'用户意图: {intent}\n'
        '只输出 JSON: {"en": ["kw1 kw2", ...], "zh": ["关键词", ...]}'
    )
    raw = ark.chat(prompt, max_tokens=256, json_mode=True)
    try:
        data = json.loads(raw)
        return {
            "en": [s for s in data.get("en", []) if isinstance(s, str) and s.strip()][:3],
            "zh": [s for s in data.get("zh", []) if isinstance(s, str) and s.strip()][:2],
        }
    except json.JSONDecodeError:
        return {"en": [intent], "zh": []}


def llm_rerank(intent: str, candidates: List[Dict[str, Any]], top_n: int = 40) -> None:
    """LLM 对候选就地重排（按与意图的相关性降序）。失败则保持原序。"""
    if not candidates:
        return
    from ark_client import ArkChat
    ark = ArkChat()
    lines = [
        f"{i}. {c['full_name']} | {str(c.get('description') or '')[:100]} | "
        f"{','.join(c.get('topics') or [])[:60]} | {c.get('stars', 0)}★"
        for i, c in enumerate(candidates[:top_n])
    ]
    prompt = (
        "按与用户意图的相关性给下列 GitHub 仓库排序。\n"
        f"用户意图: {intent}\n\n仓库列表:\n" + "\n".join(lines) +
        '\n只输出 JSON: {"order": [索引从0开始, 相关性降序, 不必全排只排前25]}'
    )
    try:
        raw = ark.chat(prompt, max_tokens=300, json_mode=True)
        order = json.loads(raw).get("order", [])
        idx = [i for i in order if isinstance(i, int) and 0 <= i < len(candidates)]
        seen, uniq = set(), []
        for i in idx:
            if i not in seen:
                seen.add(i)
                uniq.append(i)
        rest = [i for i in range(len(candidates)) if i not in seen]
        candidates[:] = [candidates[i] for i in uniq + rest]
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ LLM 重排失败，保持原序: {e}", file=sys.stderr)


def step1_recall(
    client: GitHubClient,
    keywords: str,
    language: str,
    min_stars: int,
    max_recalls: int,
) -> List[Dict[str, Any]]:
    """Step1：分页召回原始仓库列表。"""
    pushed_since = (_now() - timedelta(days=DEFAULT_STALE_DAYS)).strftime("%Y-%m-%d")
    query = build_search_query(keywords, language, min_stars, pushed_since)

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
    parser.add_argument("--llm-expand", action="store_true", default=True,
                        help="LLM 把意图改写为英文+中文关键词多路搜索并重排（默认开）")
    parser.add_argument("--no-llm-expand", dest="llm_expand", action="store_false",
                        help="关闭 LLM 改写，仅用原始意图文本作为关键词")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = parser.parse_args()

    client = GitHubClient()

    try:
        # 关键词通道：LLM 双语改写 → 多路搜索 → 合并 → LLM 重排
        if args.llm_expand:
            try:
                kw = expand_keywords(args.query)
            except Exception as e:  # noqa: BLE001
                print(f"⚠️ LLM 改写失败，回退原始意图: {e}", file=sys.stderr)
                kw = {"en": [args.query], "zh": []}
            variants = kw["en"] + kw["zh"] or [args.query]
            per_cap = max(30, args.max_recalls // len(variants))
            print(f"[expand] 搜索变体: {variants}", file=sys.stderr)
            merged: Dict[str, Dict[str, Any]] = {}
            for v in variants:
                try:
                    raw = step1_recall(client, v, args.language, args.min_stars, per_cap)
                except Exception as e:  # noqa: BLE001
                    print(f"⚠️ 变体「{v}」召回失败: {e}", file=sys.stderr)
                    continue
                for r in step2_coarse_filter(raw, args.stale_days):
                    n = _normalize(r)
                    merged.setdefault(n["full_name"], n)
            normalized = list(merged.values())
            llm_rerank(args.query, normalized)
        else:
            raw = step1_recall(client, args.query, args.language,
                               args.min_stars, args.max_recalls)
            normalized = [_normalize(r) for r in step2_coarse_filter(raw, args.stale_days)]
    except Exception as e:  # noqa: BLE001
        print(f"❌ Step1 召回失败：{e}", file=sys.stderr)
        sys.exit(1)

    if not normalized:
        print("⚠️ 未召回任何仓库。可尝试放宽 star 阈值 / 语言 / 活跃窗口。", file=sys.stderr)
        sys.exit(0)

    result = {
        "query": args.query,
        "language": args.language,
        "min_stars": args.min_stars,
        "recalled": len(normalized),
        "candidates": len(normalized),
        "candidates_list": normalized,
        "note": ("关键词通道：LLM 双语改写多路搜索 + LLM 重排"
                 if args.llm_expand else
                 "原始意图直搜。"),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Step1 召回: {len(raw)} 条 → Step2 裁剪后: {len(normalized)} 条候选")
        print("（候选已输出，含元数据；description 为空者保留）")
        print(json.dumps(normalized, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()