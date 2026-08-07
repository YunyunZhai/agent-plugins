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

statusLine 自动配置(规则C):
    每次 hook 触发时顺带检查主 statusLine 配置:
      - 无 statusLine 或已是本插件 → 自动写入 usage-reader 命令(即装即用)
      - 已有"非本插件" statusLine → 不覆盖, 仅当次返回一条 systemMessage
        提示用户(靠状态文件去重, 不重复刷屏)。
    stdout 契约: 仅在有提示要返回时输出 JSON(systemMessage), 且必须 exit 0。
    正常记录数据时 stdout 保持为空, 避免污染。
"""
import sys
import os
import json
import glob
import datetime

TOKEN_KEYS = ("input_tokens", "output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens")

LOG_DIR = os.path.expanduser("~/.claude/usage-logs")
SETTINGS = os.path.expanduser("~/.claude/settings.json")
BACKUP = os.path.expanduser("~/.claude/.usage-stats-statusline-backup.json")
CONFLICT_FLAG = os.path.expanduser("~/.claude/.usage-stats-conflict-notified")
MARKER = "usage-reader.py"


def plugin_root():
    """定位插件实际安装路径(含版本目录)。优先 CLAUDE_PLUGIN_ROOT 环境变量,
    其次 glob cache 下的版本目录。"""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root and os.path.isdir(root):
        return root
    base = os.path.expanduser("~/.claude/plugins/cache/agent-plugins/usage-stats")
    dirs = sorted(glob.glob(os.path.join(base, "*")), reverse=True)
    for d in dirs:
        if os.path.isfile(os.path.join(d, "scripts", "usage-reader.py")):
            return d
    return base


def read_settings():
    if not os.path.isfile(SETTINGS):
        return {}
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def write_settings(settings):
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS)  # 原子替换, 避免与 setup.py 并发写坏文件


def existing_statusline(settings):
    sl = settings.get("statusLine")
    return sl if isinstance(sl, dict) else None


def is_ours(sl):
    return MARKER in (sl.get("command") or "")


def ensure_statusline():
    """规则C: 检查并自动配置 statusLine。返回 (handled, message)。"""
    settings = read_settings()
    sl = existing_statusline(settings)

    # 已是本插件 → 无需处理
    if sl is not None and is_ours(sl):
        return True, None

    # 无 statusLine → 自动写入, 不提示
    if sl is None:
        settings["statusLine"] = {
            "type": "command",
            "command": f"python3 {plugin_root()}/scripts/usage-reader.py",
        }
        write_settings(settings)
        return True, None

    # 已有非本插件 statusLine → 不覆盖, 仅提示一次
    if os.path.isfile(CONFLICT_FLAG):
        return True, None  # 已提示过, 跳过
    msg = (
        "usage-stats: 检测到你已配置自定义 statusLine, 为不覆盖它, 状态栏未自动启用。"
        "如想同时显示用量, 请手动运行: python3 "
        f"{plugin_root()}/scripts/setup.py"
    )
    return False, msg


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

    if new_rows:
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

    # 规则C: statusLine 自动配置
    handled, message = ensure_statusline()
    if not handled and message:
        # 记录已提示, 防止重复刷屏
        try:
            with open(CONFLICT_FLAG, "w") as fh:
                fh.write("1")
        except Exception:
            pass
        # stdout 输出 JSON(systemMessage), 必须 exit 0
        print(json.dumps({"systemMessage": message}))
        sys.exit(0)


if __name__ == "__main__":
    main()