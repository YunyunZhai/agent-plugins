## Context

`gh-search` 由三部分组成：

- **REST 服务**（`service/`）：FastAPI 入口 `main.py`，通过 `config.py` 加载 `config.yaml`，再调用 `pipeline.py` 编排搜索流程。
- **搜索/步骤脚本**（`scripts/search/`、`scripts/pipeline/`、`scripts/maintenance/`）：作为独立 CLI 运行，或由 REST 服务 import 复用。
- **公共模块**（`scripts/_common/`）：`logsetup.py` 提供共享日志初始化，`github_client.py`、`sqlite_store.py` 等提供基础设施。

当前日志状态（审计 `gh-search/` 后确认，不含 `plugins/`）：

- `search_repos.py`、`semantic_search.py`、`hybrid_search.py`、`rerank_results.py` 通过 `_common/logsetup.setup()` 常驻落盘 DEBUG 日志，仅 `--debug` 时输出 stderr。
- `enrich_metrics.py`、`fetch_readme.py` 用 `print(..., file=sys.stderr)` 打进度，未接日志。
- `service/main.py` 完全没有日志初始化。
- `build_index.py`、`fetch_repos.py`、`sync_stars.py`、`incremental_update.py`、`compare_models.py` 用 `print` 打进度（这些属于数据/维护脚本，本次保持现状，仅审计不改动）。

目标是把日志级别、文件落盘、console 输出统一成 `config.yaml` 的一个 `logging` 段，覆盖 REST 服务与搜索/步骤脚本。

## Goals / Non-Goals

**Goals:**

- 统一日志配置来源：`config.yaml` 的 `logging` 段 + 环境变量覆盖。
- 让 REST 服务接日志。
- 让 `enrich_metrics.py`、`fetch_readme.py` 接日志。
- 保留 `--debug` 的向后兼容语义。

**Non-Goals:**

- 不改动数据抓取/索引维护脚本（`fetch_repos.py`、`build_index.py`、`sync_stars.py`、`incremental_update.py`、`import_gpu_vectors.py`）的 `print` 进度输出。
- 不引入结构化日志（JSON logging）或第三方日志库。
- 不改变现有日志文件路径与轮转策略（`data/<logger>.log`，5MB×3）。
- 不改变搜索/计费/健康检查等接口契约。

## Decisions

### D1: 使用 `logging` 配置段，而非环境变量专属或全局开关

**选择**：`config.yaml` 增加 `logging.level/file/console`，由 `service/config.py` 解析，脚本通过同一份配置初始化。

**备选**：
- 只用环境变量，不写进 config.yaml —— 违背「config 文件增加日志开关」的核心诉求。
- 只加 `logging.enabled` 布尔开关 —— 无法控制级别与 console 去向，粒度不足。

**理由**：级别、文件、console 三个维度覆盖了「排障 vs 生产」的真实需求；`level` 控制噪音，`file` 控制磁盘占用，`console` 控制 stderr 输出。

### D2: `logsetup.setup()` 改为接收解析后的配置

**选择**：`setup(log, *, level, file, console)` 取代当前的 `stderr_debug` 布尔参数；新增 `configure_root_logging(config)` 辅助函数供服务端与脚本复用。

**理由**：把「读配置 + 应用配置」集中到一处，脚本无需各自重复 `if args.debug` 的 `basicConfig` 逻辑。`--debug` 仍映射为 `level=debug, console=true`。

### D3: 服务端启动时初始化根 logger

**选择**：`service/main.py` 在 `app = FastAPI(...)` 前调用日志初始化，使用 `config.py` 解析出的 `logging` 段。

**理由**：FastAPI/uvicorn 默认有自身日志，但业务代码需要统一的 `logging.getLogger(...)` 行为；在服务端初始化根 logger 可让 `pipeline`/`billing` 等模块的日志按配置输出。

### D4: `enrich_metrics.py` / `fetch_readme.py` 的最小日志接入

**选择**：为这两个脚本添加 `log = logging.getLogger(...)`，`main()` 中调用 `setup()`，把关键进度 `print(..., file=sys.stderr)` 改为 `log.info(...)`，保留用户可见的错误/警告走 stderr。

**理由**：这两个脚本是 REST 管线的一部分，接日志后与其它步骤脚本行为一致；只改关键进度，避免大范围重写。

## Risks / Trade-offs

- [默认 `level=info` 改变现有落盘粒度] -> 现有脚本常驻 DEBUG 落盘；改成 info 后调试信息减少。通过 `logging.level=debug` 或 `--debug` 恢复。
- [脚本同时 import 服务配置可能引入路径耦合] -> `logsetup.py` 保持独立，配置解析逻辑放 `service/config.py`，脚本只依赖 `logsetup`，由调用方传入解析结果或自行读环境变量。
- [`print` 改 `log.info` 可能丢用户可见进度] -> 仅把纯进度信息转日志；错误/警告继续走 stderr；`--debug` 时 console 开启可看到日志。
- [服务端 uvicorn 日志级别与业务日志级别不一致] -> 本次只管理业务 logger；uvicorn 自身 `--log-level` 保持现状（e2e 测试仍传 `--log-level warning`）。

## Migration Plan

1. 修改 `config.yaml` 与 `config.yaml.example`，增加 `logging` 段。
2. 修改 `service/config.py`，解析 `logging` 段并支持环境变量覆盖。
3. 修改 `scripts/_common/logsetup.py`，增加 `configure_root_logging` 与新 `setup` 签名。
4. 修改 `service/main.py`，启动时初始化日志。
5. 修改 `scripts/search/enrich_metrics.py`、`scripts/search/fetch_readme.py`，接入日志。
6. 更新 `README.md` 配置说明。
7. 运行 `pytest` 与 `openspec validate` 验证。

回滚策略：删除 `logging` 段即回退到默认 `info/file/console`；`logsetup` 的旧调用方若未同步更新会报错，需在实施时同步更新三个通道脚本与 `rerank_results.py` 的调用点。

## Open Questions

- 无。本次不触碰任何 in-force ADR（`<repo>/adr/` 目录尚不存在）。
