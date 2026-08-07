#!/usr/bin/env python3
"""
setup.py — 用量统计插件安装配置

插件无法原生声明主 statusLine, 因此安装/更新后需运行本脚本, 把插件的
绝对安装路径写入用户 ~/.claude/settings.json 的 statusLine.command。

用法:
    python3 setup.py                      # 解析插件路径并写入 statusLine
    python3 setup.py --plugin-root /path  # 显式指定插件根目录
    python3 setup.py --dry-run            # 预览将写入的配置, 不实际修改
    python3 setup.py --uninstall          # 卸载: 移除指向本插件的 statusLine

冲突处理:
    若检测到用户已有"非本插件"的 statusLine, 本脚本会交互式给出三个选项:
      1) 覆盖: 先备份原 statusLine, 卸载时可还原;
      2) 退出: 不修改任何配置;
      3) 手动拼接: 打印拼接方法, 由用户在自己的 statusline 脚本里合并。

说明:
    插件 hook 已由 plugin.json 原生声明(用 ${CLAUDE_PLUGIN_ROOT}), 无需配置。
    本脚本只处理 statusLine。插件更新后路径的版本目录会变化, 需重跑本脚本刷新。
    主 statusLine 无法由插件自动注入/清理(Claude Code 限制)。
"""
import os
import sys
import json
import glob
import argparse

SETTINGS = os.path.expanduser("~/.claude/settings.json")
CACHE_BASE = os.path.expanduser("~/.claude/plugins/cache/agent-plugins/usage-stats")
BACKUP = os.path.expanduser("~/.claude/.usage-stats-statusline-backup.json")
MARKER = "usage-reader.py"


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


def read_settings():
    if not os.path.isfile(SETTINGS):
        return {}
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        print(f"警告: 无法读取 {SETTINGS}: {e}", file=sys.stderr)
        return {}


def write_settings(settings):
    with open(SETTINGS, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2, ensure_ascii=False)


def existing_statusline(settings):
    """返回现有 statusLine 对象; 无则 None。"""
    sl = settings.get("statusLine")
    return sl if isinstance(sl, dict) else None


def is_ours(sl):
    """statusLine 是否指向本插件(usage-reader.py)。"""
    return MARKER in (sl.get("command") or "")


def save_backup(sl):
    """备份原 statusLine, 供卸载时还原。"""
    with open(BACKUP, "w", encoding="utf-8") as fh:
        json.dump(sl, fh, indent=2, ensure_ascii=False)


def load_backup():
    try:
        with open(BACKUP, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def print_merge_guide(root):
    """选项 3: 打印手动拼接方法。"""
    reader = os.path.join(root, "scripts", "usage-reader.py")
    print("\n=== 手动拼接方法(不修改你的 statusLine) ===")
    print("statusLine 只能有一条 command。若你已有自己的 statusline 脚本,")
    print("请把 usage-stats 的读侧命令\"追加\"到那个脚本的最后一行, 用 echo 换行:")
    print()
    print(f'  echo "$( <你的脚本> )"')  # noqa
    print(f"  python3 {reader}")
    print()
    print("若你的 statusLine.command 直接是一条命令(而非脚本), 可这样一行拼接:")
    print()
    print(f'  bash -c "echo \\"$(<你的命令>)\\" && python3 {reader}"')
    print()
    print("拼接后重启 Claude Code 即可同时看到你自己的状态栏与用量统计。")
    print("本次未写入任何配置。")


def do_uninstall(dry_run):
    settings = read_settings()
    sl = existing_statusline(settings)
    backup = load_backup()

    if backup is not None:
        # 有备份 → 还原原 statusLine
        if dry_run:
            print("将还原原 statusLine:")
            print(json.dumps(backup, indent=2, ensure_ascii=False))
            return
        settings["statusLine"] = backup
        write_settings(settings)
        os.remove(BACKUP)
        print("已还原原 statusLine。")
        return

    if sl is not None and is_ours(sl):
        if dry_run:
            print("将移除 statusLine 条目:")
            print(json.dumps(sl, indent=2, ensure_ascii=False))
            return
        del settings["statusLine"]
        write_settings(settings)
        print("已移除 statusLine。")
        return

    print("未发现指向本插件的 statusLine, 无需清理。")


def do_install(root, dry_run):
    reader = os.path.join(root, "scripts", "usage-reader.py")
    command = f"python3 {reader}"

    settings = read_settings()
    sl = existing_statusline(settings)

    if dry_run:
        print(f"插件根目录: {root}")
        print(f"将写入 statusLine.command: {command}")
        if sl is not None and not is_ours(sl):
            print("检测到已有非本插件 statusLine, 冲突处理见 --dry-run 说明。")
        return

    # 已有非本插件 statusLine → 冲突, 交互式三选项
    if sl is not None and not is_ours(sl):
        print("检测到 `~/.claude/settings.json` 已有自定义 statusLine:")
        print(json.dumps(sl, indent=2, ensure_ascii=False))
        print()
        print("请选择如何处理:")
        print("  1) 覆盖 —— 备份现有 statusLine, 安装后可用 --uninstall 还原")
        print("  2) 不安装退出 —— 不改动任何配置")
        print("  3) 手动拼接 —— 打印拼接方法, 由你自己合并")
        choice = input("请输入 1/2/3: ").strip()
        if choice == "1":
            save_backup(sl)
            settings["statusLine"] = {"type": "command", "command": command}
            write_settings(settings)
            print(f"已写入 {SETTINGS} (原 statusLine 已备份)。")
            print(f"statusLine.command: {command}")
        elif choice == "3":
            print_merge_guide(root)
        else:
            print("未修改任何配置, 退出。")
        return

    # 无 statusLine, 或已是本插件(刷新路径) → 直接写入
    settings["statusLine"] = {"type": "command", "command": command}
    write_settings(settings)
    print(f"已写入 {SETTINGS}")
    print(f"statusLine.command: {command}")


def main():
    ap = argparse.ArgumentParser(description="usage-stats 插件 statusLine 配置")
    ap.add_argument("--plugin-root", help="显式指定插件根目录")
    ap.add_argument("--dry-run", action="store_true", help="只预览不修改")
    ap.add_argument("--uninstall", action="store_true", help="移除指向本插件的 statusLine")
    args = ap.parse_args()

    if args.uninstall:
        do_uninstall(args.dry_run)
        return

    root = args.plugin_root or find_plugin_root()
    if not root:
        print("错误: 未找到插件安装路径。请用 --plugin-root 显式指定。", file=sys.stderr)
        sys.exit(1)

    do_install(root, args.dry_run)


if __name__ == "__main__":
    main()