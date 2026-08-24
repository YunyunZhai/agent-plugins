#!/usr/bin/env python3
"""服务器端 README 抓取（纯标准库）。

输入: /root/readme_targets.jsonl.gz  {"i": "owner/name", "s": stars}
输出: /root/readme_texts.jsonl       {"i": id, "t": 清洁文本}（增量追加，断点续传）
认证: /root/.gh_token (Bearer, 5000 req/h)
限速: ~0.78s/请求 ≈ 4600 次/小时
"""
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

TOKEN = open("/root/.gh_token").read().strip()
TARGETS = "/root/readme_targets.jsonl.gz"
OUTPUT = "/root/readme_texts.jsonl"
MAX_CHARS = 1000


def clean_readme(text):
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = html.unescape(text)
    lines = [l.strip() for l in text.splitlines() if l.strip() and "[![" not in l]
    return " ".join(" ".join(lines).split())


def fetch_readme(full_name):
    url = f"https://api.github.com/repos/{full_name}/readme"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "gh-search-readme-fetcher",
    })
    try:
        r = urllib.request.urlopen(req, timeout=30)
        data = json.load(r)
        raw = base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
        return clean_readme(raw)[:MAX_CHARS] or None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None          # 无 README，正常情况
        if e.code == 403 and "rate limit" in e.read().decode(errors="ignore").lower():
            print("[rate-limit] 睡眠 300s", flush=True)
            time.sleep(300)
            raise RuntimeError("retry-after-ratelimit")
        return None
    except Exception as e:
        print(f"[net-retry] {full_name}: {str(e)[:80]}", flush=True)
        time.sleep(10)
        raise RuntimeError("retry-network")


# 断点续传：读取已完成集合
done = set()
if os.path.exists(OUTPUT):
    with open(OUTPUT) as f:
        for line in f:
            try:
                done.add(json.loads(line)["i"])
            except Exception:
                pass
print(f"断点续传: 已完成 {len(done)} 条", flush=True)

targets = []
with gzip.open(TARGETS, "rt", encoding="utf-8") as f:
    for line in f:
        d = json.loads(line)
        if d["i"] not in done:
            targets.append(d["i"])
print(f"待抓取: {len(targets)} 条", flush=True)

out = open(OUTPUT, "a", encoding="utf-8")
ok = fail = 0
t0 = time.time()
for i, rid in enumerate(targets):
    wait = 3.0
    for attempt in range(5):
        try:
            head = fetch_readme(rid)
            break
        except RuntimeError:
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
