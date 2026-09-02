## Why

`gh-search` 的日志行为目前是硬编码且不一致的：三个搜索通道脚本通过 `_common/logsetup.py` 常驻落盘 DEBUG 日志，只有传 `--debug` 才输出到 stderr；REST 服务（`service/main.py`）完全不接日志；`enrich_metrics.py`、`fetch_readme.py` 等步骤脚本只用 `print(..., file=sys.stderr)` 打进度，没有结构化日志与落盘。调试排障时无法统一控制日志级别与输出去向，也无法在生产环境按需关停文件日志。

本次为 `config.yaml` 增加日志开关，统一驱动 REST 服务与搜索脚本的日志级别、文件落盘与 console 输出，补齐缺失脚本的日志接入。

## What Changes

- 在 `config.yaml` / `config.yaml.example` 增加 `logging` 配置段：`logging.level`（debug/info/warning/error，默认 info）、`logging.file`（bool，默认 true）、`logging.console`（bool，默认 false）。
- 扩展 `service/config.py`：加载 `logging` 段并提供环境变量覆盖（`GH_SEARCH_LOG_LEVEL`、`GH_SEARCH_LOG_FILE`、`GH_SEARCH_LOG_CONSOLE`）。
- 调整 `_common/logsetup.py`：支持从配置读取级别与 file/console 开关，替代当前「DEBUG 常驻落盘 + `--debug` 才开 stderr」的固定行为；保留 `--debug` 覆盖为 console=true 的向后兼容语义。
- 在 `service/main.py` 启动时初始化日志，让 REST 服务也遵循 `logging` 配置。
- 为 `scripts/search/enrich_metrics.py`、`scripts/search/fetch_readme.py` 接入 `logsetup`，把关键 `print(..., file=sys.stderr)` 进度信息改为日志输出。
- 更新 `gh-search/README.md` 的配置说明，补充 `logging` 段与对应环境变量。

## Capabilities

### New Capabilities

- `config-logging`: 通过 `config.yaml` 的 `logging` 段统一控制 gh-search 的日志级别、文件落盘与 console 输出，覆盖 REST 服务与搜索/步骤脚本。

### Modified Capabilities

（无现有 spec 需要修改；`config-management` 的「加载 config.yaml」行为不变，仅新增一个配置键组，属新增行为而非既有行为变更。）

## Impact

- 生产代码：`gh-search/service/config.py`、`gh-search/service/main.py`、`gh-search/scripts/_common/logsetup.py`、`gh-search/scripts/search/enrich_metrics.py`、`gh-search/scripts/search/fetch_readme.py`。
- 配置：`gh-search/config.yaml`、`gh-search/config.yaml.example`。
- 文档：`gh-search/README.md`。
- 行为：日志级别与去向改为可配置；默认 `info` 级别下不再无差别落盘 DEBUG 日志，减小磁盘占用与噪音。
- 向后兼容：脚本的 `--debug` 参数继续有效（等价于 console=true + level=debug）；未配置 `logging` 段时回退到合理默认值。
