#!/usr/bin/env python3
"""
usage-backfill.py — 一次性回填存量用量

首次安装时, 对 ~/.claude/projects/*/*.jsonl 全量扫描, 把历史 assistant 的
usage 追加进各会话日志(~/.claude/usage-logs/<session_id>.jsonl), 保证
"今日累计"从当天 0 点起完整, 可与网关对账。

与 usage-tracker.py 复用同一去重逻辑(uuid 集合), 幂等: 已写入的不重复。
"""
import os
import glob
import json
import datetime

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
LOG_DIR = os.path.expanduser("~/.claude/usage-logs")


def parse_ts(ts):
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def session_id_from_path(path):
    """transcript 路径 -> 会话日志文件名(不含扩展名)。"""
    return os.path.splitext(os.path.basename(path))[0]


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    added = 0
    scanned = 0
    for path in glob.glob(os.path.join(PROJECTS_DIR, "*", "*.jsonl")):
        sid = session_id_from_path(path)
        log_path = os.path.join(LOG_DIR, f"{sid}.jsonl")

        # 已有日志含 uuid 集合
        logged = set()
        try:
            with open(log_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        logged.add(json.loads(line).get("uuid", ""))
                    except Exception:
                        continue
        except FileNotFoundError:
            pass
        except Exception:
            pass

        new_rows = []
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if '"type":"assistant"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    if o.get("type") != "assistant":
                        continue
                    uuid = o.get("uuid")
                    if not uuid or uuid in logged:
                        continue
                    u = (o.get("message") or {}).get("usage") or {}
                    new_rows.append((uuid, parse_ts(o.get("timestamp") or ""), u))
        except Exception:
            continue

        if not new_rows:
            continue
        with open(log_path, "a", encoding="utf-8") as fh:
            for uuid, epoch, usage in new_rows:
                record = {
                    "ts": epoch,
                    "uuid": uuid,
                    "in": usage.get("input_tokens", 0),
                    "out": usage.get("output_tokens", 0),
                    "cr": usage.get("cache_read_input_tokens", 0),
                    "cc": usage.get("cache_creation_input_tokens", 0),
                }
                fh.write(json.dumps(record) + "\n")
                added += 1
                scanned += 1
    print(f"backfill done: {added} records appended across {LOG_DIR}")


if __name__ == "__main__":
    main()