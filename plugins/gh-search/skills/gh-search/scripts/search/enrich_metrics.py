#!/usr/bin/env python3
"""
第 3 步：高阶成熟度指标过滤（仅对第 2 步的小集合调用，不做全量查询）。

2026-08 优化：单次 GraphQL 批量查询拿回全部指标，不再逐仓库调 REST：
    - 30 天 commit 数：defaultBranchRef.target.history(since: <30天前>).totalCount
    - 合并 PR 数：pullRequests(states: MERGED).totalCount
    - 默认分支名
（独立贡献者数已移除：REST /contributors 是此前的主要耗时来源，
 且其防玩具项目的作用可由 star 阈值 + 活跃度条件替代。）

过滤条件（可配置，默认）：
    - 活跃度双条件（满足其一即保留，避免误杀稳定低变更成熟项目）：
        近 30 天 commit >= min_commits_30d（默认 3）
        OR 最后推送在 6 个月内（稳定项目改动少也放行）

用法:
    python3 enrich_metrics.py --input candidates.json
    python3 enrich_metrics.py --input candidates.json --min-commits-30d 1
    python3 enrich_metrics.py --repos "owner/name,owner/name2"   # 直接指定，便于调试

输入标准: 前一步 search_repos.py 输出的 JSON（含 candidates_list）
输出: 精简后的高质量候选集合（20-60 条），含全部指标。
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _common.github_client import GitHubClient

DEFAULT_MIN_COMMITS_30D = 3
DEFAULT_STALE_DAYS = 180
BATCH_SIZE = 100          # 单次 GraphQL 别名批大小；每仓库约 4 个节点，
                          # 100 仓库 ≈ 400 节点 << GitHub 5000 复杂度上限


def _env_now() -> datetime:
    return datetime.now(timezone.utc)


def _date_30d_ago() -> str:
    return (_env_now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date_180d_ago() -> str:
    return (_env_now() - timedelta(days=DEFAULT_STALE_DAYS)).strftime("%Y-%m-%d")


def _split_owner_name(full_name: str) -> tuple[str, str]:
    owner, _, name = full_name.partition("/")
    return owner, name


def _build_metrics_query(alias: str, owner: str, name: str, since30: str) -> str:
    """为单个仓库构造指标查询片段（GraphQL 别名）。"""
    return f"""
{alias}: repository(owner: "{owner}", name: "{name}") {{
  defaultBranchRef {{
    name
    target {{ ... on Commit {{ commits30: history(since: "{since30}", first: 1) {{ totalCount }} }} }}
  }}
  mergedPRs: pullRequests(states: MERGED) {{ totalCount }}
}}"""


def _extract_metrics(alias: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从单个 GraphQL 节点提取指标；仓库不存在/无权限返回 None。"""
    repo = data.get(alias)
    if not repo:
        return None
    branch = repo.get("defaultBranchRef") or {}
    target = branch.get("target") or {}
    commits30 = (target.get("commits30") or {}).get("totalCount", 0)
    merged_prs = (repo.get("mergedPRs") or {}).get("totalCount", 0)
    return {
        "commits_30d": commits30,
        "merged_prs": merged_prs,
        "default_branch": branch.get("name"),
    }


def _query_batch(
    client: GitHubClient,
    batch: List[Dict[str, Any]],
    since30: str,
) -> List[Dict[str, Any]]:
    """对一批仓库执行单次 GraphQL 查询；失败时逐个重试并跳过不存在的仓库。"""
    fragments = []
    aliases = []
    for j, r in enumerate(batch):
        alias = f"r{j}"
        owner, name = _split_owner_name(r["full_name"])
        aliases.append(alias)
        fragments.append(_build_metrics_query(alias, owner, name, since30))
    gql = "query { " + " ".join(fragments) + " }"
    try:
        data = client.graphql(gql)
    except Exception:  # noqa: BLE001
        # 批量失败 → 逐个重试，跳过不存在/无权限的仓库
        results = []
        for r in batch:
            owner, name = _split_owner_name(r["full_name"])
            single = "query { " + _build_metrics_query("r0", owner, name, since30) + " }"
            try:
                single_data = client.graphql(single)
            except Exception:  # noqa: BLE001
                print(f"  跳过 {r['full_name']}（不存在或无权限）", file=sys.stderr)
                continue
            m = _extract_metrics("r0", single_data)
            if m:
                r.update(m)
                results.append(r)
        return results
    results = []
    for alias, r in zip(aliases, batch):
        m = _extract_metrics(alias, data)
        if m:
            r.update(m)
            results.append(r)
    return results


def enrich(client: GitHubClient, repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """批量拉取指标并合并到候选记录（每 100 条一次 GraphQL 调用）。"""
    since30 = _date_30d_ago()
    enriched: List[Dict[str, Any]] = []
    batches = 0
    for i in range(0, len(repos), BATCH_SIZE):
        batch = repos[i:i + BATCH_SIZE]
        enriched.extend(_query_batch(client, batch, since30))
        batches += 1
    print(f"[gh] GraphQL 网络调用 {batches} 次，覆盖 {len(enriched)}/{len(repos)} 条",
          file=sys.stderr)
    return enriched


def _is_stale(pushed_at: Optional[str]) -> bool:
    if not pushed_at:
        return True
    try:
        pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return (_env_now() - pushed).days > DEFAULT_STALE_DAYS


def step3_filter(
    enriched: List[Dict[str, Any]],
    min_commits_30d: int,
) -> List[Dict[str, Any]]:
    """Step3 过滤：活跃度双条件（防单人玩具项目交由 star 阈值与上层初筛把关）。"""
    kept = []
    for r in enriched:
        # 活跃度双条件，满足其一即保留
        fresh = r.get("commits_30d", 0) >= min_commits_30d
        active = not _is_stale(r.get("pushed_at"))
        if not (fresh or active):
            continue
        kept.append(r)
    # 按 star 降序，便于大模型优先看高置信项目
    kept.sort(key=lambda x: x.get("stars", 0), reverse=True)
    return kept


def _load_repos(input_path: Optional[str], repos_arg: Optional[str]) -> List[Dict[str, Any]]:
    if repos_arg:
        return [{"full_name": n.strip()} for n in repos_arg.split(",") if n.strip()]
    if input_path:
        with open(input_path) as f:
            return json.load(f).get("candidates_list", [])
    raise SystemExit("需要 --input 或 --repos 参数")


def main() -> None:
    parser = argparse.ArgumentParser(description="第3步：成熟度指标过滤")
    parser.add_argument("--input", default=None, help="前一步 search_repos.py 输出的 JSON")
    parser.add_argument("--repos", default=None, help="逗号分隔的仓库名，便于调试")
    parser.add_argument("--min-commits-30d", type=int, default=DEFAULT_MIN_COMMITS_30D,
                        help=f"近30天 commit 阈值（默认 {DEFAULT_MIN_COMMITS_30D}）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = parser.parse_args()

    repos = _load_repos(args.input, args.repos)
    if not repos:
        print("⚠️ 输入为空，无候选可过滤。", file=sys.stderr)
        sys.exit(0)

    client = GitHubClient()
    enriched = enrich(client, repos)
    kept = step3_filter(enriched, args.min_commits_30d)

    result = {
        "input_count": len(repos),
        "enriched_count": len(enriched),
        "output_count": len(kept),
        "min_commits_30d": args.min_commits_30d,
        "results": kept,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Step3: 输入 {len(repos)} 条 → 富化 {len(enriched)} 条 → 保留 {len(kept)} 条")
        for r in kept:
            print(f"  {r['full_name']} ⭐{r.get('stars')} "
                  f"commits30d={r.get('commits_30d')} "
                  f"mergedPRs={r.get('merged_prs')}")


if __name__ == "__main__":
    main()