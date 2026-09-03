#!/usr/bin/env python3
"""README 抓取管线（双通道之一）。

按 stars 降序抓取仓库 README，清理为纯文本后写入数据库或 jsonl。

支持两种模式：
  --source sqlite   从 sqlite repos 表读取，写回 readme_embed_text 列（默认）
  --source jsonl    从 jsonl.gz 文件读取，输出到 jsonl 文件

抓取策略：
  优先 GraphQL（大 README / 非默认分支均可命中），失败回退 REST。
  GraphQL 404 = 无 README；REST 空内容 = 尝试 GraphQL。

清理规则：去 HTML 标签 / md 图片 / 徽章墙 / 裸 URL / 代码块，保留纯文字。

用法:
    # sqlite 模式（默认）
    python3 fetch_readmes.py --sample 5 --min-stars 40000
    python3 fetch_readmes.py --min-stars 200 --db <库>
    python3 fetch_readmes.py --max-count 5000 --db <库>

    # jsonl 模式（服务器批量）
    python3 fetch_readmes.py --source jsonl --input targets.jsonl.gz --output texts.jsonl

限速: GitHub GraphQL 5000 次/小时，脚本按 ~4200/h 节奏请求。
"""

import argparse
import base64
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from _common.github_client import GitHubClient  # noqa: E402


# ══ 清理 ═══════════════════════════════════════════════════════════════

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


# ══ GraphQL 抓取（优先）═════════════════════════════════════════════════

def _graphql_readme(client: GitHubClient, full_name: str) -> Optional[str]:
    """用 GraphQL 拉取 README 原文；无 README 或失败返回 None。"""
    owner, _, name = full_name.partition("/")
    query = (
        'query($o: String!, $n: String!) {'
        '  r: repository(owner: $o, name: $n) {'
        '    object(expression: "HEAD:README.md") {'
        '      ... on Blob { text }'
        '    }'
        '  }'
        '}'
    )
    try:
        data = client.graphql(query, {"o": owner, "n": name})
        blob = data.get("r", {}).get("object")
        if blob and blob.get("text"):
            return blob["text"]
    except Exception:
        pass
    return None


# ══ REST 抓取（回退）════════════════════════════════════════════════════

def _rest_readme(client: GitHubClient, full_name: str) -> Optional[str]:
    """用 REST API 拉取 README 原文；无 README 或失败返回 None。"""
    try:
        r = client.rest(f"/repos/{full_name}/readme")
        raw = base64.b64decode(r.get("content", "")).decode("utf-8", errors="ignore")
        return raw or None
    except Exception:
        return None


# ══ 统一抓取入口 ═══════════════════════════════════════════════════════

def fetch_one(client: GitHubClient, full_name: str,
              max_chars: int = 1000) -> Optional[str]:
    """抓取并清洗单个仓库 README；GraphQL 优先，REST 回退。"""
    # 1. GraphQL（大 README / 非默认分支均可命中）
    raw = _graphql_readme(client, full_name)
    # 2. GraphQL 没拿到 → REST 回退
    if not raw:
        raw = _rest_readme(client, full_name)
    if not raw:
        return None
    txt = clean_readme(raw)[:max_chars]
    return txt or None


# ══ HTTP 直接抓取（jsonl 模式用，不依赖 gh CLI）═══════════════════════

def _http_readme(full_name: str, token: str, max_chars: int = 1000) -> Optional[str]:
    """用 urllib 直接调 GitHub API，支持断点续传。"""
    url = f"https://api.github.com/repos/{full_name}/readme"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "gh-search-readme-fetcher",
    })
    try:
        r = urllib.request.urlopen(req, timeout=30)
        data = json.load(r)
        raw = base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
        return clean_readme(raw)[:max_chars] or None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 403 and "rate limit" in e.read().decode(errors="ignore").lower():
            raise RuntimeError("rate-limit")
        return None
    except Exception:
        raise RuntimeError("network")


# ══ sqlite 模式 ════════════════════════════════════════════════════════

def run_sqlite(args):
    """从 sqlite 读取仓库列表，抓取 README，写回 sqlite。"""
    import _common.sqlite_store as ss
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
    print(f"[readme] 待抓取 {total} 条（stars≥{args.min_stars}，降序）", flush=True)

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
        time.sleep(0.85)
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


# ══ jsonl 模式 ════════════════════════════════════════════════════════

def run_jsonl(args):
    """从 jsonl.gz 读取仓库列表，抓取 README，输出到 jsonl。"""
    token = _get_token()
    max_chars = args.max_chars
    max_chars = max_chars

    # 断点续传：读取已完成集合
    done = set()
    if os.path.exists(args.output):
        with open(args.output) as f:
            for line in f:
                try:
                    done.add(json.loads(line)["i"])
                except Exception:
                    pass
    print(f"断点续传: 已完成 {len(done)} 条", flush=True)

    targets = []
    opener = gzip.open if args.input.endswith(".gz") else open
    with opener(args.input, "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d["i"] not in done:
                targets.append(d["i"])
    print(f"待抓取: {len(targets)} 条", flush=True)

    out = open(args.output, "a", encoding="utf-8")
    ok = fail = 0
    t0 = time.time()
    for i, rid in enumerate(targets):
        wait = 3.0
        for attempt in range(5):
            try:
                head = _http_readme(rid, token, max_chars)
                break
            except RuntimeError as e:
                if "rate-limit" in str(e):
                    print("[rate-limit] 睡眠 300s", flush=True)
                    time.sleep(300)
                else:
                    time.sleep(wait)
                    wait = min(wait * 2, 120)
        else:
            continue
        if head:
            out.write(json.dumps({"i": rid, "t": head}, ensure_ascii=False) + "\n")
            out.flush()
            ok += 1
        else:
            fail += 1
        time.sleep(0.78)
        if (i + 1) % 100 == 0:
            el = time.time() - t0
            eta_h = (len(targets) - i - 1) * el / (i + 1) / 3600
            print(f"{i+1}/{len(targets)} 成功{ok} 失败{fail} "
                  f"({el/max(i+1,1):.2f}s/条 ETA {eta_h:.1f}h)", flush=True)

    out.close()
    print(f"完成: 成功 {ok}, 失败/无 {fail}", flush=True)


def _get_token() -> str:
    """获取 GitHub token（环境变量 > .gh_token 文件）。"""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token
    token_file = Path("/root/.gh_token")
    if token_file.exists():
        return token_file.read_text().strip()
    raise SystemExit("未找到 GitHub token，请设置 GH_TOKEN 环境变量或创建 /root/.gh_token")


# ══ CLI ════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="README 抓取（GraphQL 优先 + REST 回退）")
    ap.add_argument("--source", choices=["sqlite", "jsonl"], default="sqlite",
                    help="数据源：sqlite（默认）或 jsonl")
    ap.add_argument("--db", default=None, help="sqlite 路径（sqlite 模式）")
    ap.add_argument("--input", default=None, help="输入 jsonl.gz 路径（jsonl 模式）")
    ap.add_argument("--output", default=None, help="输出 jsonl 路径（jsonl 模式）")
    ap.add_argument("--min-stars", type=int, default=200,
                    help="只抓 stars 快照 ≥ 该值的仓库（默认 200，仅 sqlite 模式）")
    ap.add_argument("--max-count", type=int, default=0,
                    help="最多处理条数（0=不限）")
    ap.add_argument("--sample", type=int, default=0,
                    help="抽样模式：只抓 N 条并打印清洗结果，不落库")
    ap.add_argument("--max-chars", type=int, default=1000,
                    help="清洗后保留头部字符数（默认 1000）")
    args = ap.parse_args()

    if args.source == "jsonl":
        if not args.input or not args.output:
            ap.error("jsonl 模式需要 --input 和 --output 参数")
        run_jsonl(args)
    else:
        run_sqlite(args)


if __name__ == "__main__":
    main()
