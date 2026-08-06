#!/usr/bin/env python3
"""
usage-tracker.py — 写侧: UserPromptSubmit hook

在用户提交下一条提示(控制权交还)时触发, 读取当前会话 transcript 中
"上一回合"的 assistant usage, 按会话隔离 append-only 追加到 usage-logs。

并发安全: 每个进程/agent 写自己的 <session_id>.jsonl, 无共享累计文件,
多 claude code / 多 agent 互不冲突。

去重: 以 uuid 集合去重。先读本会话日志已有的 uuid 集合, 只追加 uuid
不在集合中的 assistant 记录。相比"单点高水位线", 对 transcript 被压缩/
uuid 变化更鲁棒, 不会静默停摆。每条 assistant 记录 = 一次 API 请求,
逐条保存(读侧再做窗口聚合), 保证 rpm 计数准确。
"""
import sys
import os
import json
import datetime

TOKEN_KEYS = ("input_tokens", "output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens")

LOG_DIR = os.path.expanduser("~/.claude/usage-logs")


def parse_ts(ts):
    """ISO8601 时间戳 -> epoch 秒。"""
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def iter_assistant(path):
    """逐行产出 (uuid, epoch, usage_dict)。"""
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
                u = (o.get("message") or {}).get("usage") or {}
                yield o.get("uuid"), parse_ts(o.get("timestamp") or ""), u
    except (FileNotFoundError, Exception):
        return


def load_logged_uuids(session_id):
    """读本会话日志已有的 uuid 集合。"""
    log_path = os.path.join(LOG_DIR, f"{session_id}.jsonl")
    uuids = set()
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    uuids.add(json.loads(line).get("uuid", ""))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return uuids


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception:
        return  # 非 JSON, 静默退出

    transcript_path = payload.get("transcript_path") or ""
    session_id = payload.get("session_id") or ""
    if not transcript_path or not session_id:
        return

    os.makedirs(LOG_DIR, exist_ok=True)

    logged = load_logged_uuids(session_id)
    log_path = os.path.join(LOG_DIR, f"{session_id}.jsonl")

    new_rows = []
    for uuid, epoch, usage in iter_assistant(transcript_path):
        if not uuid or uuid in logged:
            continue
        new_rows.append((uuid, epoch, usage))

    if not new_rows:
        return

    # append-only 追加 (O_APPEND), 逐条一行
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


if __name__ == "__main__":
    main()