# usage-stats

Claude Code 本地用量统计状态栏插件。在状态栏显示滚动窗口的 token 用量与请求数，数据全部来自本地会话记录，不调用外部 API。

## 状态栏显示

```
tp5m 12.4k | tp5h 1.2M | rp5m 8 | rp5h 320 | 今日 3.1M/860
```

| 指标 | 含义 |
|------|------|
| `tp5m` | 过去 5 分钟的 token 用量 |
| `tp5h` | 过去 5 小时的 token 用量 |
| `rp5m` | 过去 5 分钟的请求数 |
| `rp5h` | 过去 5 小时的请求数 |
| `今日` | 当天 0 点起的 token 用量 / 请求数 |

token 口径：`input + output + cache_read + cache_creation`。

## 工作原理

- **写侧**：`hooks/usage-tracker.py` 通过 **UserPromptSubmit hook**（插件原生声明，安装即生效）在每次用户提交下一条提示时，读取当前会话 transcript 的上一回合 token 用量，按会话隔离追加到 `~/.claude/usage-logs/<session_id>.jsonl`（append-only，多 claude code / 多 agent 并发安全）。
- **读侧**：`scripts/usage-reader.py` 聚合所有 usage-logs，计算滚动窗口指标并输出一行给状态栏（带 5 秒节流）。
- **数据目录**：`~/.claude/usage-logs/`（用户数据，非插件内）。

## 安装

1. 安装插件后，**UserPromptSubmit hook 自动生效**（无需配置）。
2. 配置主状态栏（Claude Code 插件**无法原生注入主 statusLine**，需运行一次 setup）：

```bash
python3 <插件安装路径>/scripts/setup.py
```

这会把插件绝对安装路径写入 `~/.claude/settings.json` 的 `statusLine.command`。

3. 重启 Claude Code，状态栏即显示用量。

### 冲突处理（已有自定义 statusLine 时）

若 `~/.claude/settings.json` 已有一个**非本插件**的 statusLine，运行 setup.py
会交互式询问，提供三个选项：

- **1) 覆盖** —— 先把现有 statusLine 备份到
  `~/.claude/.usage-stats-statusline-backup.json`，再写入本插件的。卸载时运行
  `--uninstall` 会**自动还原**备份的原 statusLine。
- **2) 不安装退出** —— 不改动任何配置。
- **3) 手动拼接** —— 打印拼接方法，由你把自己的 statusline 脚本与
  usage-reader 合并（见下）。

若没有 statusLine，或当前 statusLine 已是本插件的，则直接写入/刷新，不询问。

## 手动拼接（不覆盖你的 statusLine）

statusLine 只有一条 command。若你想在保留自己状态栏的同时显示用量，可把
usage-stats 读侧命令追加到你的 statusline 脚本末尾（用 echo 换行）：

```bash
echo "$( <你的脚本> )"
python3 <插件安装路径>/scripts/usage-reader.py
```

若你的 `statusLine.command` 直接是一条命令，可一行拼接：

```bash
bash -c "echo \"$(<你的命令>)\" && python3 <插件安装路径>/scripts/usage-reader.py"
```

## 插件更新后

插件更新后安装路径的版本目录会变化，需重跑一次 setup 刷新 statusLine：

```bash
python3 <新插件安装路径>/scripts/setup.py
```

## 卸载

卸载插件后，主 statusLine 不会自动清除（Claude Code 限制）。运行：

```bash
python3 <插件安装路径>/scripts/setup.py --uninstall
```

行为取决于是否曾备份：

- **有备份**（曾用选项 1 覆盖）→ 自动**还原**原 statusLine，并删除备份文件。
- **无备份** 且当前是本插件 → 移除 statusLine 条目。
- 其他情况 → 提示无需清理。

预览会执行什么：

```bash
python3 <插件安装路径>/scripts/setup.py --uninstall --dry-run
```

## 关于 subagentStatusLine

Claude Code 插件唯一原生支持的 statusLine 是 `subagentStatusLine`，但它只在
**子代理面板**里渲染，不在终端底部的主状态栏，不能满足常驻用量条的需求，
故本插件默认走主 statusLine + setup.py 的方案。

## 回填历史用量

首次使用想补齐当天 0 点起的历史用量（对账用）：

```bash
python3 <插件安装路径>/scripts/usage-backfill.py
```

## 数据并发安全

每个会话写独立的 `<session_id>.jsonl`，append-only，无共享累计文件，因此同时开多个 claude code / agent 不会互相覆盖。

## 依赖

无需额外依赖，仅 Python 3 标准库。