#!/usr/bin/env python3
"""
star 快照刷新（语义检索排序先验的数据源）。

REST search 自适应区间：count ≤ 990 直接翻页收全；超限二分细化。
低于 min_stars 的仓库不拉取：其先验贡献 log10(1+<2000) < 0.33，
对混合排序影响微小。

用法:
    python3 sync_stars.py                        # 默认同步 >=2000 star
    python3 sync_stars.py --min-stars 1000       # 自定义下限
    python3 sync_stars.py --db /path/to.db       # 指定数据库

环境变量:
    GH_SEARCH_DB  - sqlite 路径
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).parent.parent))
from _common.github_client import GitHubClient
from _common.sqlite_store import connect, upsert_stars

DEFAULT_STAR_MAX = 1_000_000
DEFAULT_MIN_STARS = 2000


def sync_stars(client: GitHubClient, conn, min_stars: int = DEFAULT_MIN_STARS,
               star_max: int = DEFAULT_STAR_MAX) -> int:
    """
    同步高星仓库的 star 快照到 repos.stars（语义检索的排序先验）。

    返回收集到的仓库数。
    """
    mapping: Dict[str, int] = {}
    calls = 0

    def _rest_search(q: str, **params) -> Dict[str, Any]:
        nonlocal calls
        time.sleep(2.1)          # REST search 限速 30 req/min
        calls += 1
        return client.rest("/search/repositories", {"q": q, **params})

    def _collect_range(lo: int, hi: int) -> None:
        q = f"is:public stars:{lo}..{hi} fork:false"
        total = _rest_search(q, per_page="1").get("total_count", 0)
        if total == 0:
            return
        if total > 990 and hi > lo:
            mid = (lo + hi) // 2
            _collect_range(lo, mid)
            _collect_range(mid + 1, hi)
            return
        pages = min((total + 99) // 100, 10)
        for p in range(1, pages + 1):
            r = _rest_search(q, per_page="100", page=str(p), sort="stars")
            for it in r.get("items", []):
                mapping[it["full_name"]] = int(it.get("stargazers_count") or 0)

    lo = min_stars
    while lo <= star_max:
        hi = min(lo * 2 - 1, star_max)
        before = len(mapping)
        _collect_range(lo, hi)
        upsert_stars(conn, mapping)
        print(f"[stars] {lo}..{hi}: 本段 +{len(mapping) - before}, 累计 {len(mapping)}", flush=True)
        lo = hi + 1

    print(f"[stars] 同步 {len(mapping)} 条 star 快照（{calls} 次 REST 调用）")
    return len(mapping)


def main():
    parser = argparse.ArgumentParser(description="同步高星仓库 star 快照")
    parser.add_argument("--min-stars", type=int, default=DEFAULT_MIN_STARS,
                        help=f"star 同步下限（默认 {DEFAULT_MIN_STARS}）")
    parser.add_argument("--max-stars", type=int, default=DEFAULT_STAR_MAX,
                        help=f"star 同步上限（默认 {DEFAULT_STAR_MAX}）")
    parser.add_argument("--db", default=None, help="sqlite 路径")
    args = parser.parse_args()

    client = GitHubClient()
    try:
        who = client.graphql("query { viewer { login } }")
        print(f"已认证用户: {who['viewer']['login']}")
    except Exception as e:
        sys.exit(f"gh 不可用: {e}")

    conn = connect(args.db)
    sync_stars(client, conn, min_stars=args.min_stars, star_max=args.max_stars)


if __name__ == "__main__":
    main()
