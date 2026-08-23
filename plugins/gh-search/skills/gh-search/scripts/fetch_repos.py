#!/usr/bin/env python3
"""
全量抓取 GitHub 仓库元数据（语义索引 Step 1）。

将 GitHub 上 stars 在 [star_min, star_max] 的公开非 fork 非 archived 仓库，
按「star 值 × created 年段」预分片成若干 ≤1000 条的小区域，逐区域 GraphQL search
分页抓取，直接写入本地 sqlite（sqlite_store）。

为什么需要分片:
    GitHub GraphQL search 单次查询最多返回 1000 条（硬上限）。stars:>100 直接查询
    (47万仓库) 远超 1000，必须拆片。本脚本用「批量 count 探测分布 + 宽度自适应合并」
    生成分片（预分片），比递归二分高效（一次 GraphQL 调用查多段 count）。

分片算法:
    1. plan_slices(): 从 star_min 开始，用「倍增-减半」宽度策略试探:
        - 试 width 段的 count (批量, 一次查 N 段)
        - count ≤ 1000  → 接受该段, 前进并加倍 width(尝试更大合并)
        - count > 1000 → 减半 width 重试
        - width 减到 0 又仍 >1000 → 是该单值全历史超限, 按年段再拆
    高 star 段(如 >10000)仓库稀疏, width 自动放大, region 数很少。

用法:
    python3 fetch_repos.py --stars-min 100 --stars-max 1000000
    python3 fetch_repos.py --resume <regions.pkl>   # 中断续抓

输出:
    sqlite 表 repos(元数据) 直接落库；分片进度 pickle 到 regions.pkl

环境变量:
    GH_SEARCH_DB        - sqlite 路径（默认插件 data 目录）
    GH_SEARCH_TIMEOUT   - 单次 gh 调用超时秒（默认 60）
"""

import argparse
import json
import os
import pickle
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from github_client import GitHubClient, GitHubError
from sqlite_store import (
    connect,
    count_repos,
    insert_new_repo,
    update_if_text_changed,
)

DEFAULT_STAR_MIN = 100
DEFAULT_STAR_MAX = 1_000_000
DATE_START = "2008-01-01"
DATE_END = "2026-12-31"          # 覆盖至今（脚本运行时可被覆盖）
MAX_REGION_SIZE = 1000           # GraphQL search 单查询上限
PAGE_SIZE = 100                  # GraphQL search 单页最大值; 静态元数据字段量小, 大页高效
BATCH_COUNT = 40                 # 一次 GraphQL 批量 count 的别名数量
RESUME_FILE = Path(__file__).parent / "regions.pkl"

# 抓取字段（语义索引只需静态元数据; 动态字段不落库, 检索时在线拉取）
SEARCH_FIELDS = """
name
nameWithOwner
isFork
isArchived
description
primaryLanguage { name }
repositoryTopics(first: 10) { nodes { topic { name } } }
"""


# 低段边界：star 值从此以上单值全历史 <1000（实测 star≈250 已降到 756）
LOW_SEG_MAX = 1000

# 固定时间窗（实测：低星单值每窗均 <1000，除最低星值的 [2017-2020] 可达 1200+，抓取分页覆盖）
TIME_WINDOWS = [
    ("2008-01-01", "2012-12-31"),
    ("2013-01-01", "2016-12-31"),
    ("2017-01-01", "2020-12-31"),
    ("2021-01-01", "2023-12-31"),
    ("2024-01-01", "2026-12-31"),
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _split_owner_name(name_with_owner: str) -> Tuple[str, str]:
    owner, _, name = name_with_owner.partition("/")
    return owner, name


class RateLimitHandler:
    """GraphQL rate limit 处理：优先用返回的 resetAt 等待。"""

    def __init__(self, client: GitHubClient):
        self._client = client

    def wait_if_needed(self) -> None:
        try:
            data = self._client.graphql("query { rateLimit { remaining resetAt } }")
            rate = data.get("rateLimit", {})
            remaining = rate.get("remaining", 5000)
            if remaining < 50:
                reset_at = rate.get("resetAt", "")
                wait = self._seconds_until_reset(reset_at)
                if wait > 0:
                    print(f"[rate] 剩余 {remaining} 次, 等待 {wait:.0f}s 到 {reset_at}...")
                    time.sleep(min(wait + 2, 600))
        except Exception:
            pass

    @staticmethod
    def _seconds_until_reset(reset_at: str) -> float:
        try:
            dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
            return max(0.0, (dt - now_utc()).total_seconds())
        except Exception:
            return 0.0


class RepoFetcher:
    """分片抓取器：生成分片 + 逐片抓取，支持 resume。"""

    def __init__(
        self,
        client: GitHubClient,
        star_min: int = DEFAULT_STAR_MIN,
        star_max: int = DEFAULT_STAR_MAX,
        db_path: Optional[str] = None,
        date_end: Optional[str] = None,
        date_field: str = "created",
        update_mode: bool = False,
    ):
        self.client = client
        self.star_min = star_min
        self.star_max = star_max
        self.date_end = date_end or DATE_END
        self.date_field = date_field          # "created"(全量) 或 "pushed"(增量)
        self.update_mode = update_mode        # True=检测变化更新, False=只插新(默认)
        self.db_path = db_path or os.environ.get("GH_SEARCH_DB")
        self._tl = threading.local()          # 线程本地连接(并发抓取用)
        self.conn = connect(self.db_path)
        self.rl = RateLimitHandler(client)
        self.regions: List[Dict[str, Any]] = []

    def _conn(self):
        """获取当前线程的 sqlite 连接（懒创建，避免跨线程共享）。"""
        if not hasattr(self._tl, "conn"):
            self._tl.conn = connect(self.db_path)
        return self._tl.conn

    # ══ 批量 count（一次调用查多段）═══════════════════════════════════

    def batch_count(self, slices: List[Tuple[str, str]]) -> List[int]:
        """批量查询多个 (star_fmt, date_fmt) 的 repositoryCount，返回并行列表。"""
        df_field = self.date_field
        aliases = []
        for i, (sf, df) in enumerate(slices):
            aliases.append(
                f'c{i}: search(query: "is:public stars:{sf} {df_field}:{df} fork:false", '
                f'type: REPOSITORY, first: 1) {{ repositoryCount }}'
            )
        gql = "query { " + " ".join(aliases) + " }"
        result = self._graphql_retry(gql)
        return [result.get(f"c{i}", {}).get("repositoryCount", 0) for i in range(len(slices))]

    # ══ 预分片（批量 count + 时间窗拆分）════════════════════════════════

    def plan_slices(self) -> List[Dict[str, Any]]:
        """
        生成全部 ≤ MAX_REGION_SIZE 的 region 列表（实测分布驱动，无需递归二分）：

          [star_min, LOW_SEG_MAX]   低段密集 → 逐星值 count；
                                    单值全历史 ≤1000 的贪心合并成 region，
                                    单值全历史 >1000 的按 TIME_WINDOWS 拆时间窗 region。
          [LOW_SEG_MAX+1, star_max] 高段稀疏 → 大宽块 count，绝大多数直接成 region，
                                    若某宽块仍 >1000 则减半细化重试。
        """
        full_window = f"{DATE_START}..{self.date_end}"
        regions: List[Dict[str, Any]] = []

        # —— 低段：逐星值 count ——
        counts: Dict[int, int] = {}
        end_low = min(LOW_SEG_MAX, self.star_max)
        cur = self.star_min
        while cur <= end_low:
            seg_end = min(cur + BATCH_COUNT - 1, end_low)
            slices = [(f"{s}..{s}", full_window) for s in range(cur, seg_end + 1)]
            cnts = self.batch_count(slices)
            for s, c in zip(range(cur, seg_end + 1), cnts):
                counts[s] = max(c, 1)   # 保底：count=0 的可能未被完全清空, 抓取阶段仍会跑
            cur = seg_end + 1

        # 低段：合并 ≤1000 的星值, 拆开 >1000 的星值（按时间窗）
        start, acc = None, 0
        for s in range(self.star_min, end_low + 1):
            c = counts[s]
            if c > MAX_REGION_SIZE:
                if start is not None:
                    regions.append({"star_fmt": f"{start}..{s-1}", "date_fmt": full_window, "count": acc})
                    start, acc = None, 0
                for ds, de in TIME_WINDOWS:
                    cw = self.batch_count([(f"{s}..{s}", f"{ds}..{de}")])[0]
                    regions.append({"star_fmt": f"{s}..{s}", "date_fmt": f"{ds}..{de}", "count": cw})
                continue
            if start is None:
                start, acc = s, c
            elif acc + c <= MAX_REGION_SIZE:
                acc += c
            else:
                regions.append({"star_fmt": f"{start}..{s-1}", "date_fmt": full_window, "count": acc})
                start, acc = s, c
        if start is not None:
            regions.append({"star_fmt": f"{start}..{end_low}", "date_fmt": full_window, "count": acc})

        # —— 高段：大宽块 count + 需细化时减半 ——
        if self.star_max > LOW_SEG_MAX:
            self._plan_high(regions, full_window, LOW_SEG_MAX + 1, self.star_max)

        self.regions = regions
        return regions

    def _plan_high(self, regions: List[Dict[str, Any]], full_window: str,
                   seg_start: int, seg_end: int) -> None:
        """高段（star 值大、单值稀疏）: 宽块试探, 超限减半细化。"""
        candidates = [(seg_start, seg_end, full_window, 200)]   # (a, b, date, width)
        while candidates:
            a, b, date_fmt, width = candidates.pop()
            c = self.batch_count([(f"{a}..{b}", date_fmt)])[0]
            if c <= MAX_REGION_SIZE:
                regions.append({"star_fmt": f"{a}..{b}", "date_fmt": date_fmt, "count": c})
                continue
            if b - a <= 0:
                # 单星值仍超限（理论不会在高段出现）：按时间窗拆
                for ds, de in TIME_WINDOWS:
                    cw = self.batch_count([(f"{a}..{b}", f"{ds}..{de}")])[0]
                    regions.append({"star_fmt": f"{a}..{b}", "date_fmt": f"{ds}..{de}", "count": cw})
                continue
            mid = (a + b) // 2
            candidates.append((a, mid, date_fmt, width))
            candidates.append((mid + 1, b, date_fmt, width))

    # ══ 单 region 抓取 ═════════════════════════════════════════════════

    def fetch_region(self, region: Dict[str, Any], date_field: str = None) -> int:
        """
        抓一个 region 全部 page，落 sqlite，返回写入条数。
        date_field: 覆盖 self.date_field 的日期字段（默认用 self.date_field）。
        """
        date_field = date_field or self.date_field
        star_fmt, date_fmt = region["star_fmt"], region["date_fmt"]
        q = (f'is:public stars:{star_fmt} {date_field}:{date_fmt} '
             f'fork:false sort:stars')
        written = 0
        cursor = None
        while True:
            pg_str = f"first: {PAGE_SIZE}"
            if cursor:
                pg_str += f', after: "{cursor}"'
            gql = f"""query {{
  search(query: "{q}", type: REPOSITORY, {pg_str}) {{
    repositoryCount
    pageInfo {{ hasNextPage endCursor }}
    edges {{
      cursor
      node {{
        __typename
        ... on Repository {{
          {SEARCH_FIELDS}
        }}
      }}
    }}
  }}
}}"""
            self.rl.wait_if_needed()
            try:
                result = self._graphql_retry(gql)
            except GitHubError:
                print(f"  [err] 区域 {star_fmt} × {date_fmt} 抓取失败, 跳过")
                break

            search = result["search"]
            nodes = [e["node"] for e in search.get("edges", []) if e.get("node")]
            for n in nodes:
                self._upsert_node(n)
                written += 1
            if not search.get("pageInfo", {}).get("hasNextPage"):
                break
            cursor = search["pageInfo"]["endCursor"]

        self._conn().commit()
        return written

    def _upsert_node(self, node: Dict[str, Any]) -> str:
        """
        把单个 GraphQL 节点写入 repos 表，返回状态标记：
          "inserted" (新插入), "updated" (文本变化更新), "unchanged", "skipped" (fork/arch)
        只存语义字段；动态字段(stars等)不落库。
        """
        repo = {
            "id": node.get("nameWithOwner", ""),
            "name": node.get("name", ""),
            "description": node.get("description"),
            "topics": [t["topic"]["name"] for t in (node.get("repositoryTopics") or {}).get("nodes", [])],
            "primary_language": (node.get("primaryLanguage") or {}).get("name"),
            "is_fork": 1 if node.get("isFork") else 0,
            "is_archived": 1 if node.get("isArchived") else 0,
        }
        if repo["is_fork"] or repo["is_archived"]:
            return "skipped"
        conn = self._conn()
        if self.update_mode:
            return "updated" if update_if_text_changed(conn, repo) else "unchanged"
        return "inserted" if insert_new_repo(conn, repo) else "unchanged"

    # ══ 批量抓取主流程 ═════════════════════════════════════════════════

    def run(self, regions: Optional[List[Dict[str, Any]]] = None, workers: int = 4) -> None:
        """
        并发抓取全部 region。
        每个 worker 独立线程 + 独立 sqlite 连接。
        resume 语义：已完成的 region 按 (star_fmt,date_fmt) 身份从待处理列表剔除，
        as_completed 返回顺序与提交顺序无关，故用身份匹配而非 pop(0)。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        self.regions = regions or self.regions
        total = len(self.regions)
        done = 0
        lock = threading.Lock()

        def _fetch_one(region: Dict[str, Any]) -> Dict[str, Any]:
            try:
                return {"region": region, "w": self.fetch_region(region)}
            except Exception as e:  # noqa: BLE001
                print(f"  [err] {region['star_fmt']} × {region['date_fmt']}: {e}")
                return {"region": region, "w": 0}

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_fetch_one, r) for r in self.regions]
            for fut in as_completed(futs):
                res = fut.result()
                done += 1
                with lock:
                    # 按身份剔除已完成的 region（as_completed 无序）
                    r = res["region"]
                    key = (r["star_fmt"], r["date_fmt"])
                    self.regions = [
                        x for x in self.regions
                        if (x["star_fmt"], x["date_fmt"]) != key
                    ]
                    self._save_resume(self.regions)
                print(f"  [{done}/{total}] 完成 {res['w']} 条 | 累计 {count_repos(self.conn)} 条")

    # ══ 工具 ════════════════════════════════════════════════════════

    def _graphql_retry(self, gql: str, retries: int = 5) -> Dict[str, Any]:
        for attempt in range(retries):
            try:
                return self.client.graphql(gql)
            except GitHubError as e:
                err = str(e)
                if "secondary rate" in err.lower() or "rate limit" in err.lower():
                    self.rl.wait_if_needed()
                    continue
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError("GraphQL 重试耗尽")

    def _save_resume(self, regions: List[Dict[str, Any]]) -> None:
        RESUME_FILE.write_bytes(pickle.dumps({
            "regions": regions,
            "config": {"star_min": self.star_min, "star_max": self.star_max},
        }))

    @staticmethod
    def load_resume(path: Path) -> Dict[str, Any]:
        return pickle.loads(path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description="全量抓取 GitHub 仓库元数据到本地 sqlite")
    parser.add_argument("--stars-min", type=int, default=DEFAULT_STAR_MIN)
    parser.add_argument("--stars-max", type=int, default=DEFAULT_STAR_MAX)
    parser.add_argument("--resume", default=None, help="regions.pkl 续抓")
    parser.add_argument("--db", default=None, help="sqlite 路径")
    parser.add_argument("--workers", type=int, default=4, help="并发抓取线程数(默认4)")
    parser.add_argument("--update", action="store_true",
                        help="变化检测模式（对比embed_text, 文本变了才更新；默认只插新）")
    args = parser.parse_args()

    client = GitHubClient()
    try:
        who = client.graphql("query { viewer { login } }")
        print(f"已认证用户: {who['viewer']['login']}")
    except GitHubError as e:
        sys.exit(f"gh 不可用: {e}")

    fetcher = RepoFetcher(client, args.stars_min, args.stars_max, db_path=args.db,
                          update_mode=args.update)
    def _persist_and_run(regions):
        fetcher.regions = regions
        fetcher._save_resume(regions)
        fetcher.run(workers=args.workers)

    if args.resume:
        data = RepoFetcher.load_resume(Path(args.resume))
        print(f"[resume] 续抓 {len(data['regions'])} 个剩余区域")
        _persist_and_run(data["regions"])
    else:
        print(f"[plan] 预分片 stars:{args.stars_min}..{args.stars_max} ...")
        regions = fetcher.plan_slices()
        print(f"[plan] 共 {len(regions)} 个区域, 合计仓储约 {sum(r['count'] for r in regions):,} 条")
        _persist_and_run(regions)


if __name__ == "__main__":
    main()