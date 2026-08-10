#!/usr/bin/env python3
"""
第 3 步：高阶成熟度指标过滤（仅对第 2 步的小集合调用，不做全量查询）。

用 GraphQL 批量获取这批仓库的成熟度指标：
    - 30 天 commit 数：defaultBranchRef.target.history(since: <30天前>).totalCount
    - 合并 PR 数：pullRequests(states: MERGED).totalCount
用 REST /contributors 并行获取独立贡献者总数（含匿名）。

贡献者 <GraphQL 采样低估问题>：GraphQL history 单次最多返回 100 条 commit，
对年轻高产仓库（如 jcode，最近 100 条全出自同一作者）会严重低估贡献者数，
故改用 REST 精确计数。REST 调用并发执行以控制耗时。

过滤条件（可配置，默认）：
    - 独立贡献者 >= min_contributors（默认 8，过滤单人维护项目）
    - 活跃度双条件（满足其一即保留，避免误杀稳定低变更成熟项目）：
        近 30 天 commit >= min_commits_30d（默认 3）
        OR 最后推送在 6 个月内（稳定项目改动少也放行）

用法:
    python3 enrich_metrics.py --input candidates.json
    python3 enrich_metrics.py --input candidates.json --min-contributors 5 --min-commits-30d 1
    python3 enrich_metrics.py --repos "owner/name,owner/name2"   # 直接指定，便于调试

输入标准: 前一步 search_repos.py 输出的 JSON（含 candidates_list）
输出: 精简后的高质量候选集合（20-60 条），含全部指标。
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from github_client import GitHubClient

DEFAULT_MIN_CONTRIBUTORS = 8
DEFAULT_MIN_COMMITS_30D = 3
DEFAULT_STALE_DAYS = 180
BATCH_SIZE = 20           # 每批仓库数（GraphQL 别名，避免查询过大）
CONTRIB_CONCURRENCY = 8   # 贡献者 REST 调用并发数


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


def _fetch_contributors(
    client: GitHubClient,
    full_name: str,
    max_pages: int = 3,
) -> int:
    """用 REST /contributors 获取独立贡献者总数（含匿名，分页去重）。

    比 GraphQL commit 采样更准确：对年轻高产仓库（如 jcode，最近 100 条
    commit 全出自同一作者）Sample 会严重低估，而 REST 返回真实贡献者数。
    仅对 Step3 小集合（20-60 条）调用，成本可控。
    """
    seen = set()
    anon_idx = 0
    for page in range(1, max_pages + 1):
        data = None
        for attempt in range(3):  # 轻量重试，容忍瞬时网络抖动
            try:
                data = client.rest(
                    f"/repos/{full_name}/contributors?per_page=100&anon=1&page={page}"
                )
                break
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    break
        if not isinstance(data, list) or not data:
            break
        for c in data:
            # 独立贡献者：named 用 login 去重；匿名（login 为 null）每条记录
            # 视为一个独立贡献者（GitHub 匿名贡献者无稳定 ID，一条即一人）。
            login = c.get("login")
            if login:
                seen.add(("login", login))
            else:
                anon_idx += 1
                seen.add(("anon", anon_idx))
        if len(data) < 100:
            break
    return len(seen)


def enrich(client: GitHubClient, repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """批量拉取指标并合并到候选记录。

    GraphQL 指标（commit/PR/分支）按批查询；贡献者 REST 调用并发执行，
    避免逐仓库串行拖慢整体耗时。
    """
    since30 = _date_30d_ago()
    contrib_map: Dict[str, int] = {}

    # 并发拉取所有仓库的贡献者数
    def _contrib(full_name: str) -> tuple[str, int]:
        return full_name, _fetch_contributors(client, full_name)

    with ThreadPoolExecutor(max_workers=CONTRIB_CONCURRENCY) as pool:
        futures = [pool.submit(_contrib, r["full_name"]) for r in repos]
        for fut in as_completed(futures):
            try:
                name, cnt = fut.result()
                contrib_map[name] = cnt
            except Exception as e:  # noqa: BLE001
                print(f"⚠️ 贡献者查询失败：{e}", file=sys.stderr)

    enriched: List[Dict[str, Any]] = []
    for i in range(0, len(repos), BATCH_SIZE):
        batch = repos[i:i + BATCH_SIZE]
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
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 批量指标查询失败（跳过 {len(batch)} 条）：{e}", file=sys.stderr)
            continue
        for alias, r in zip(aliases, batch):
            m = _extract_metrics(alias, data)
            if m:
                r["contributors"] = contrib_map.get(r["full_name"], 0)
                r.update(m)
                enriched.append(r)
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
    min_contributors: int,
    min_commits_30d: int,
) -> List[Dict[str, Any]]:
    """Step3 过滤：贡献者阈值 + 活跃度双条件。"""
    kept = []
    for r in enriched:
        if r.get("contributors", 0) < min_contributors:
            continue
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
    parser.add_argument("--min-contributors", type=int, default=DEFAULT_MIN_CONTRIBUTORS,
                        help=f"独立贡献者最低阈值（默认 {DEFAULT_MIN_CONTRIBUTORS}）")
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
    kept = step3_filter(enriched, args.min_contributors, args.min_commits_30d)

    result = {
        "input_count": len(repos),
        "enriched_count": len(enriched),
        "output_count": len(kept),
        "min_contributors": args.min_contributors,
        "min_commits_30d": args.min_commits_30d,
        "results": kept,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Step3: 输入 {len(repos)} 条 → 富化 {len(enriched)} 条 → 保留 {len(kept)} 条")
        for r in kept:
            print(f"  {r['full_name']} ⭐{r.get('stars')} "
                  f"贡献者{r.get('contributors')} commits30d={r.get('commits_30d')} "
                  f"mergedPRs={r.get('merged_prs')}")


if __name__ == "__main__":
    main()