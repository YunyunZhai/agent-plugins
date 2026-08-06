#!/usr/bin/env python3
"""
usage-reader.py — 读侧: 状态栏用量聚合

聚合 ~/.claude/usage-logs/*.jsonl(只读, 无锁), 计算滚动窗口指标并输出
一行文本给状态栏。带节流: 缓存上次结果, 节流窗口内复用, 避免每次 UI 刷新重算。

输出示例:
  tpm 12.4k | tp5h 1.2M | rpm 8 | rp5h 320 | 今日 3.1M/860
"""
import os
import sys
import json
import time

LOG_DIR = os.path.expanduser("~/.claude/usage-logs")
CACHE_FILE = os.path.expanduser("~/.claude/usage-logs/.reader-cache")
THROTTLE_SEC = 5          # 节流窗口: 5 秒内复用上次结果
MINUTES = {"5m": 5 * 60, "5h": 300 * 60}


def load_rows():
    """聚合所有会话日志, 产出 (ts, in, out, cr, cc) 列表。"""
    rows = []
    try:
        for name in os.listdir(LOG_DIR):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(LOG_DIR, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                            rows.append((d.get("ts", 0), d.get("in", 0),
                                         d.get("out", 0), d.get("cr", 0),
                                         d.get("cc", 0)))
                        except Exception:
                            continue
            except Exception:
                continue
    except Exception:
        pass
    return rows


def day_start(now):
    """当天 0 点(本地时区) epoch。"""
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def fmt_tokens(n):
    """token 数格式化: 12.4k / 1.2M。"""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(int(n))


def compute(now):
    rows = load_rows()
    tokens = {}
    reqs = {}
    for label, window in MINUTES.items():
        cutoff = now - window
        tokens[label] = 0
        reqs[label] = 0
        for ts, tin, tout, tcr, tcc in rows:
            if ts >= cutoff:
                tokens[label] += tin + tout + tcr + tcc
                reqs[label] += 1
    # 今日累计
    ds = day_start(now)
    today_tok = 0
    today_req = 0
    for ts, tin, tout, tcr, tcc in rows:
        if ts >= ds:
            today_tok += tin + tout + tcr + tcc
            today_req += 1
    return tokens, reqs, today_tok, today_req


def load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
            return d.get("t"), d.get("text")
    except Exception:
        return 0, ""


def save_cache(t, text):
    tmp = CACHE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"t": t, "text": text}, fh)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


def main():
    now = time.time()
    cached_t, cached_text = load_cache()
    if cached_t and now - cached_t < THROTTLE_SEC:
        print(cached_text)
        return

    tokens, reqs, today_tok, today_req = compute(now)
    text = (
        f"tp5m {fmt_tokens(tokens['5m'])} | tp5h {fmt_tokens(tokens['5h'])} "
        f"| rp5m {reqs['5m']} | rp5h {reqs['5h']} "
        f"| 今日 {fmt_tokens(today_tok)}/{today_req}"
    )
    save_cache(now, text)
    print(text)


if __name__ == "__main__":
    main()