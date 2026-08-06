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
2. 配置状态栏（插件无法自动注入主状态栏，需运行一次 setup）：

```bash
python3 <插件安装路径>/scripts/setup.py
```

这会把插件绝对安装路径写入 `~/.claude/settings.json` 的 `statusLine.command`。

3. 重启 Claude Code，状态栏即显示用量。

## 插件更新后

插件更新后安装路径的版本目录会变化，需重跑一次 setup 刷新 statusLine：

```bash
python3 <新插件安装路径>/scripts/setup.py
```

## 回填历史用量

首次使用想补齐当天 0 点起的历史用量（对账用）：

```bash
python3 <插件安装路径>/scripts/usage-backfill.py
```

## 数据并发安全

每个会话写独立的 `<session_id>.jsonl`，append-only，无共享累计文件，因此同时开多个 claude code / agent 不会互相覆盖。

## 依赖

无需额外依赖，仅 Python 3 标准库。