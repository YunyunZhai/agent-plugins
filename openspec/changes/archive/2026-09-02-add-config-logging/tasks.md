## 1. 配置与解析

- [x] 1.1 在 `gh-search/config.yaml` 与 `gh-search/config.yaml.example` 增加 `logging` 段（`level`、`file`、`console`），默认 `info/true/false`
- [x] 1.2 扩展 `gh-search/service/config.py` 解析 `logging` 段，并支持 `GH_SEARCH_LOG_LEVEL`、`GH_SEARCH_LOG_FILE`、`GH_SEARCH_LOG_CONSOLE` 环境变量覆盖
- [x] 1.3 为 `config.py` 增加日志配置的默认值回退与非法 `level` 回退到 `info`

## 2. 日志基础设施

- [x] 2.1 修改 `gh-search/scripts/_common/logsetup.py`，新增 `configure_root_logging(config)` 并把 `setup()` 签名改为接收 `level`/`file`/`console`
- [x] 2.2 保持 `--debug` 向后兼容语义：等价 `level=debug` 且 `console=true`
- [x] 2.3 更新 `search_repos.py`、`semantic_search.py`、`hybrid_search.py`、`rerank_results.py` 对 `setup()` 的调用点

## 3. REST 服务接入

- [x] 3.1 在 `gh-search/service/main.py` 启动时初始化日志，使用解析后的 `logging` 配置
- [x] 3.2 确认服务端日志遵循 `level`/`file`/`console` 配置

## 4. 步骤脚本接入

- [x] 4.1 为 `gh-search/scripts/search/enrich_metrics.py` 接入 `logsetup`，把关键进度 `print(..., file=sys.stderr)` 改为日志
- [x] 4.2 为 `gh-search/scripts/search/fetch_readme.py` 接入 `logsetup`，把关键进度 `print(..., file=sys.stderr)` 改为日志

## 5. 文档与验证

- [x] 5.1 更新 `gh-search/README.md` 的配置说明，补充 `logging` 段与对应环境变量
- [x] 5.2 运行 `python3 -m pytest gh-search/tests/test_rest_e2e.py -v` 验证服务未回归
- [x] 5.3 运行 `openspec validate add-config-logging --type change --strict`
