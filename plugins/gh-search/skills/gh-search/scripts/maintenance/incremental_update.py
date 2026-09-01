#!/usr/bin/env python3
"""
增量更新语义索引（gh-search 维护流程）。

两种模式:
    week (每周, 默认): 抓近 N 天活跃仓库 -> 只插新 -> 只嵌入新增。
    month (每月): 抓近 N 天活跃仓库 -> 变化检测(对比 embed_text 哈希) ->
                  只对「文本变了」的仓库重嵌。

由于 repos 表只存语义字段, 增量目的从"字段刷新"转为"补新仓库 + 捕捉描述变化"。

用法:
    python3 incremental_update.py --mode week --since 7     # 每周只插新(默认)
    python3 incremental_update.py --mode month --since 30   # 每月变化检测重嵌

环境变量: 同 fetch_repos / build_index (GH_SEARCH_DB, PINECONE_API_KEY)
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from fetch_repos import RepoFetcher
from github_client import GitHubClient
from sqlite_store import connect, find_changed_repos
from build_index import EmbedError, build_index


class IncrementalUpdate:
    """
    增量更新语义索引。

    week 模式（每周）: 只插新仓库 + 只嵌入新增。
    month 模式（每月）: 变化检测 —— 抓近 N 天活跃仓库, 对比 embed_text,
                         文本变了的仓库更新元数据并强制重新嵌入。
    """

    def __init__(self, db_path: Optional[str] = None, since_days: int = 7,
                 min_stars: int = 100, workers: int = 1, mode: str = "week"):
        self.client = GitHubClient()
        self.db_path = db_path
        self.since_days = since_days
        self.min_stars = min_stars
        self.workers = workers
        self.mode = mode            # "week" 或 "month"
        self.conn = connect(db_path)

    def _since_date(self) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=self.since_days)).strftime("%Y-%m-%d")

    def plan_incremental_regions(self, fetcher: RepoFetcher,
                                 since_date: str) -> List[Dict[str, Any]]:
        """
        增量窗口按 stars 分片（pushed 窗口窄, 无需按日期分片）：
        GraphQL search 有 1000 条硬上限, 必须把 pushed窗口内的仓库按 star 段拆到 ≤1000。
        实测：近7天 + stars>100 ≈ 4.8 万 → 需 ~50 个 star 段。
        """
        date_fmt = f"{since_date}..{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        regions: List[Dict[str, Any]] = []
        cur = self.min_stars
        width = 100
        while cur <= 1_000_000_000:
            seg_end = min(cur + width - 1, 1_000_000_000)
            while True:
                c = fetcher.batch_count([(f"{cur}..{seg_end}", date_fmt)])[0]
                if c <= 1000 or width <= 1:
                    break
                width = max(1, width // 2)
                seg_end = min(cur + width - 1, 1_000_000_000)
            regions.append({"star_fmt": f"{cur}..{seg_end}", "date_fmt": date_fmt})
            cur = seg_end + 1
            width = max(50, width)
        return regions

    def fetch_incremental(self) -> List[str]:
        """
        抓取近 N 天活跃仓库。
        week 模式: 只插新, 返回新增仓库 id 列表（用于嵌入）。
        month 模式: 变化检测更新, 返回"文本变了需重嵌"的仓库 id 列表。
        """
        since = self._since_date()
        mode_label = "变化检测" if self.mode == "month" else "只插新"
        print(f"[incr] 增量窗口: pushed:>={since} + stars:>{self.min_stars} "
              f"(近 {self.since_days} 天, {mode_label})")

        fetcher = RepoFetcher(self.client, self.min_stars, 1_000_000_000,
                              db_path=self.db_path, date_field="pushed",
                              update_mode=(self.mode == "month"))
        regions = self.plan_incremental_regions(fetcher, since)
        print(f"[incr] 分片: {len(regions)} 个 star 段")

        for region in regions:
            fetcher.fetch_region(region)

        # 收集本次需要嵌入的 id:
        #   week 模式: 无需显式收集 —— build_index 默认只嵌入未嵌入的
        #   month 模式: 找出 embed_text 与上次嵌入时hash 不一致的（文本变了需重嵌）
        if self.mode == "month":
            need_embed_ids = find_changed_repos(self.conn)
        else:
            need_embed_ids = []
        print(f"[incr] 变化检测结果: {len(need_embed_ids)} 个仓库需重嵌")
        return need_embed_ids

    def embed(self, need_ids: Optional[List[str]] = None, dry_run: bool = False) -> Dict[str, Any]:
        """嵌入向量。
        week 模式: 默认只嵌未嵌入的（build_index 默认）。
        month 模式: 强制重嵌 need_ids（文本变了的仓库）。
        """
        if self.mode == "month" and need_ids:
            return build_index(self.db_path, limit=0, dry_run=dry_run, force_ids=need_ids)
        return build_index(self.db_path, limit=0, dry_run=dry_run)

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        need_ids = self.fetch_incremental()
        return self.embed(need_ids, dry_run=dry_run)


def main():
    parser = argparse.ArgumentParser(description="增量更新语义索引")
    parser.add_argument("--mode", default="week", choices=["week", "month"],
                        help="week=只插新(每周,默认) / month=变化检测重嵌(每月)")
    parser.add_argument("--since", type=int, default=7,
                        help="增量窗口天数（默认7；month 建议传 30）")
    parser.add_argument("--min-stars", type=int, default=100,
                        help="最小 star（默认100, 与全量一致；避免抓取海量低星活跃仓库）")
    parser.add_argument("--db", default=None, help="sqlite 路径")
    parser.add_argument("--workers", type=int, default=1,
                        help="抓取并发线程（本环境建议1，并发不可靠）")
    parser.add_argument("--dry-run", action="store_true", help="只预览不实际嵌入")
    args = parser.parse_args()

    upd = IncrementalUpdate(args.db, args.since, args.min_stars, args.workers, mode=args.mode)
    try:
        stats = upd.run(dry_run=args.dry_run)
        print(f"[incr] 完成: {stats}")
    except EmbedError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"❌ 增量更新失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()