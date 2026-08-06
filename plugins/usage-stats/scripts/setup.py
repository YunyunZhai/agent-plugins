#!/usr/bin/env python3
"""
setup.py — 用量统计插件安装配置

插件无法原生声明主 statusLine, 因此安装/更新后需运行本脚本, 把插件的
绝对安装路径写入用户 ~/.claude/settings.json 的 statusLine.command。

用法:
    python3 setup.py                      # 解析插件路径并写入 statusLine
    python3 setup.py --plugin-root /path  # 显式指定插件根目录
    python3 setup.py --dry-run            # 预览将写入的配置, 不实际修改

说明:
    插件 hook 已由 plugin.json 原生声明(用 ${CLAUDE_PLUGIN_ROOT}), 无需配置。
    本脚本只处理 statusLine。插件更新后路径的版本目录会变化, 需重跑本脚本刷新。
"""
import os
import sys
import json
import glob
import argparse

SETTINGS = os.path.expanduser("~/.claude/settings.json")
CACHE_BASE = os.path.expanduser("~/.claude/plugins/cache/agent-plugins/usage-stats")


def find_plugin_root():
    """定位插件实际安装路径。优先启用时注入的环境变量, 其次 glob cache 目录。"""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root and os.path.isdir(root):
        return root
    # glob 版本目录: cache/agent-plugins/usage-stats/<version>/
    dirs = sorted(glob.glob(os.path.join(CACHE_BASE, "*")), reverse=True)
    for d in dirs:
        reader = os.path.join(d, "scripts", "usage-reader.py")
        if os.path.isfile(reader):
            return d
    return None


def main():
    ap = argparse.ArgumentParser(description="usage-stats 插件 statusLine 配置")
    ap.add_argument("--plugin-root", help="显式指定插件根目录")
    ap.add_argument("--dry-run", action="store_true", help="只预览不修改")
    args = ap.parse_args()

    root = args.plugin_root or find_plugin_root()
    if not root:
        print("错误: 未找到插件安装路径。请用 --plugin-root 显式指定。", file=sys.stderr)
        sys.exit(1)

    reader = os.path.join(root, "scripts", "usage-reader.py")
    command = f"python3 {reader}"

    if args.dry_run:
        print(f"插件根目录: {root}")
        print(f"将写入 statusLine.command: {command}")
        return

    # 读现有 settings.json
    settings = {}
    if os.path.isfile(SETTINGS):
        try:
            with open(SETTINGS, encoding="utf-8") as fh:
                settings = json.load(fh)
        except Exception as e:
            print(f"警告: 无法读取 {SETTINGS}: {e}", file=sys.stderr)
            settings = {}

    settings["statusLine"] = {"type": "command", "command": command}

    with open(SETTINGS, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2, ensure_ascii=False)
    print(f"已写入 {SETTINGS}")
    print(f"statusLine.command: {command}")


if __name__ == "__main__":
    main()