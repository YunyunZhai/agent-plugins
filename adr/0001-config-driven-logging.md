# ADR-0001: Config-driven logging for gh-search

- Status: accepted
- Date: 2026-09-02

## Context

`gh-search` 的日志行为此前是硬编码且不一致的：搜索通道脚本通过 `_common/logsetup.py` 常驻落盘 DEBUG 日志并仅在 `--debug` 时输出 stderr；REST 服务不接日志；部分步骤脚本用 `print` 打进度。排障时无法统一控制日志级别与去向。

## Decision

`gh-search` 的日志配置以 `config.yaml` 的 `logging` 段为单一来源，字段为 `level`（debug/info/warning/error）、`file`（bool）、`console`（bool），并通过 `GH_SEARCH_LOG_LEVEL` / `GH_SEARCH_LOG_FILE` / `GH_SEARCH_LOG_CONSOLE` 环境变量覆盖。`_common/logsetup.py` 是应用该配置的统一入口，REST 服务与搜索/步骤脚本都遵循同一套配置。`--debug` 等价于 `level=debug` 且 `console=true`，保留向后兼容。

## Consequences

- Positive: 日志级别与去向可集中配置；服务与脚本行为一致；生产可关停文件日志以降低磁盘占用。
- Negative: 默认级别从 DEBUG 降为 info，调试信息减少，需显式开启；logsetup 调用方需同步更新签名。
- Follow-up: 后续新增日志输出点应复用 `_common/logsetup.py`，不另起 `print` 或独立的 logging 配置。
