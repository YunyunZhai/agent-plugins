#!/usr/bin/env python3
"""README 抓取管线（双通道之一）。

按 stars 快照降序抓取仓库 README，清理为纯文本后写入
repos.readme_embed_text（嵌入文本快照），供嵌入与变化检测使用。

清理规则（实验验证版）：去 HTML 标签 / md 图片 / 徽章墙 / 裸 URL / 代码块，
保留纯文字。头部截断默认 1000 字符。

用法:
    python3 fetch_readmes.py --sample 5 --min-stars 40000   # 抽样看清洗效果(不落库)
    python3 fetch_readmes.py --min-stars 2000               # 全量后台跑(断点续传)
    python3 fetch_readmes.py --max-count 5000 --db <v3库>    # 限量

限速: GitHub core REST 5000 次/小时, 脚本按 ~4200/h 节奏请求。
"""

import argparse
import base64
import html
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from github_client import GitHubClient  # noqa: E402


def clean_readme(text: str) -> str:
    """README → 干净纯文本（去 HTML 标签/图片/徽章/裸URL/代码块 + 解码实体）。"""
    text = re.sub(r"<[^>]+>", " ", text)                    # HTML 标签
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)        # md 图片
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)    # 链接→文字
    text = re.sub(r"https?://\S+", "", text)                # 裸 URL
    text = re.sub(r"```[\s\S]*?```", "", text)              # 代码块
    text = html.unescape(text)                              # &lt; &nbsp; &#160; 等
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and "[![" not in l]
    return " ".join(" ".join(lines).split())


def fetch_one(client: GitHubClient, full_name: str,
              max_chars: int = 1000) -> Optional[str]:
    """抓取并清洗单个仓库 README；无 README 或失败返回 None。"""
    try:
        r = client.rest(f"/repos/{full_name}/readme")
        raw = base64.b64decode(r.get("content", "")).decode("utf-8", errors="ignore")
    except Exception:
        return None
    txt = clean_readme(raw)[:max_chars]
    return txt or None


def main():
    ap = argparse.ArgumentParser(description="README 抓取（双通道数据源）")
    ap.add_argument("--db", default=None, help="sqlite 路径")
    ap.add_argument("--min-stars", type=int, default=2000,
                    help="只抓 stars 快照 ≥ 该值的仓库（默认 2000）")
    ap.add_argument("--max-count", type=int, default=0,
                    help="最多处理条数（0=不限）")
    ap.add_argument("--sample", type=int, default=0,
                    help="抽样模式：只抓 N 条并打印清洗结果，不落库")
    ap.add_argument("--max-chars", type=int, default=1000,
                    help="清洗后保留头部字符数（默认 1000）")
    args = ap.parse_args()

    import sqlite_store as ss
    conn = ss.connect(args.db)

    if args.sample:
        rows = conn.execute(
            "SELECT id FROM repos WHERE stars >= ? ORDER BY stars DESC LIMIT ?",
            (args.min_stars, args.sample)).fetchall()
        client = GitHubClient()
        for r in rows:
            rid = r["id"]
            head = fetch_one(client, rid, args.max_chars)
            print(f"════ {rid} ════")
            print((head or "(抓取失败/无README)")[:600])
            print()
            time.sleep(0.8)
        return

    # 全量模式：stars 降序，跳过已抓取的
    rows = conn.execute(
        """SELECT id, stars FROM repos
           WHERE stars >= ? AND readme_embed_text = ''
           ORDER BY stars DESC""",
        (args.min_stars,)).fetchall()
    todo = [(r["id"], r["stars"]) for r in rows]
    if args.max_count:
        todo = todo[:args.max_count]
    total = len(todo)
    print(f"[readme] 待抓取 {total} 条（stars≥{args.min_stars}，降序）")

    client = GitHubClient()
    ok = fail = 0
    pending: list[tuple] = []
    t0 = time.time()

    def flush():
        nonlocal pending
        if pending:
            conn.executemany(
                "UPDATE repos SET readme_embed_text=? WHERE id=?",
                [(t, rid) for rid, t in pending])
            conn.commit()
            pending.clear()

    for i, (rid, stars) in enumerate(todo):
        head = fetch_one(client, rid, args.max_chars)
        time.sleep(0.85)          # ~4200 req/h < 5000/h 核心限额
        if head:
            pending.append((rid, head))
            ok += 1
        else:
            fail += 1
        if len(pending) >= 20:
            flush()
        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            print(f"[readme] {i + 1}/{total} 成功{ok} 失败{fail} "
                  f"({elapsed / max(i + 1, 1):.2f}s/条, "
                  f"ETA {(total - i - 1) * elapsed / (i + 1) / 3600:.1f}h)",
                  flush=True)
    flush()
    print(f"\n[readme] 完成: 成功 {ok}, 失败/无 {fail}")


if __name__ == "__main__":
    main()
