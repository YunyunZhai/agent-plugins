## ADDED Requirements

### Requirement: 可复用端到端测试脚本

系统 SHALL 提供一套可重复执行的端到端测试脚本（pytest），真实启动 FastAPI 服务进程并通过 HTTP 客户端驱动断言，覆盖 REST 服务的全部公开接口。

#### Scenario: 测试脚本可重复执行

- **GIVEN** 已安装 `pytest`、`httpx`、`uvicorn`、`fastapi` 依赖，且 `gh` CLI 已认证
- **WHEN** 运行 `pytest gh-search/tests/test_rest_e2e.py`
- **THEN** 测试脚本启动一次服务进程，依次执行健康检查、搜索、计费与参数校验断言，并在结束时停止服务进程

#### Scenario: 测试结果可追溯

- **GIVEN** 端到端测试执行完成
- **WHEN** 阅读测试结果记录文档 `gh-search/references/e2e-test-report.md`
- **THEN** 文档 SHALL 记录执行环境、运行命令、每条用例的通过/跳过/失败状态与关键产物（响应结构、计费落库、降级路径）

### Requirement: 健康检查接口测试

端到端测试 SHALL 覆盖 `GET /api/v1/health` 并断言服务健康状态。

#### Scenario: 健康服务返回 ok

- **GIVEN** FastAPI 服务已启动且可连接数据库
- **WHEN** 测试向 `/api/v1/health` 发起 GET 请求
- **THEN** 响应状态码为 200，且响应体包含 `status=ok`、`db_connected=true`

### Requirement: 关键词搜索接口测试

端到端测试 SHALL 覆盖 `POST /api/v1/search` 的 `channel=keyword` 通道。

#### Scenario: 关键词通道返回候选列表

- **GIVEN** `gh` CLI 已认证，服务已启动
- **WHEN** 测试以 `channel=keyword` 和有效 `query` 请求 `/api/v1/search`
- **THEN** 响应状态码为 200，且响应体包含 `candidates_list` 数组与 `candidates`（数量）

### Requirement: 语义搜索接口测试

端到端测试 SHALL 覆盖 `POST /api/v1/search` 的 `channel=semantic` 通道（dashscope qwen 后端）。

#### Scenario: 语义通道在凭据可用时返回候选

- **GIVEN** 已设置 `DASHSCOPE_API_KEY` 与 `DASHSCOPE_BASE_URL`，且目标 qwen 索引库存在
- **WHEN** 测试以 `channel=semantic` 请求 `/api/v1/search`
- **THEN** 响应状态码为 200，且响应体包含 `candidates_list` 数组

#### Scenario: 语义通道在凭据缺失时显式降级

- **GIVEN** 未设置 `DASHSCOPE_API_KEY` 或 `DASHSCOPE_BASE_URL`
- **WHEN** 测试以 `channel=semantic` 请求 `/api/v1/search`
- **THEN** 测试 SHALL 将该用例标记为跳过（skip）或断言服务返回明确的错误，并记录缺少凭据的原因，而非静默通过

### Requirement: 并行搜索接口测试

端到端测试 SHALL 覆盖 `POST /api/v1/search` 的 `channel=hybrid` 通道。

#### Scenario: 并行通道合并召回

- **GIVEN** `gh` CLI 已认证，服务已启动
- **WHEN** 测试以 `channel=hybrid` 请求 `/api/v1/search`
- **THEN** 响应状态码为 200，且响应体包含 `candidates_list` 数组与 `channel=hybrid`

### Requirement: 管线步骤参数测试

端到端测试 SHALL 覆盖 `enrich`、`readme`、`rerank` 参数对管线步骤的控制。

#### Scenario: 完整管线执行步骤

- **GIVEN** `gh` CLI 已认证，服务已启动
- **WHEN** 测试以 `enrich=true`、`readme=true`、`rerank=true` 请求 `/api/v1/search`
- **THEN** 响应体 `pipeline_steps` SHALL 包含 `recall`、`enrich`、`readme`、`rerank`（rerank 在缺少百炼密钥时允许以优雅降级跳过并记录原因）

#### Scenario: 默认管线仅执行召回

- **GIVEN** 服务已启动
- **WHEN** 测试以默认参数（无 enrich/readme/rerank）请求 `/api/v1/search`
- **THEN** 响应体 `pipeline_steps` SHALL 仅包含召回步骤

### Requirement: 计费汇总接口测试

端到端测试 SHALL 覆盖 `GET /api/v1/billing/summary`，并验证搜索调用已被计费落库。

#### Scenario: 搜索后按用户汇总

- **GIVEN** 已通过 `/api/v1/search` 完成至少一次带 `X-User-Id` 的搜索
- **WHEN** 测试请求 `/api/v1/billing/summary`，参数为对应的 `user_id` 与 `period`
- **THEN** 响应状态码为 200，且 `total_calls` 大于 0

### Requirement: 测试运行说明与环境变量配置文档

`gh-search/README.md` SHALL 提供端到端测试的完整运行说明，包括测试依赖安装、运行命令，以及跑通各场景所需的环境变量配置清单。

#### Scenario: 用户能配置基础场景

- **GIVEN** 用户只想跑 keyword/hybrid/health/billing 端到端测试
- **WHEN** 阅读 `gh-search/README.md` 的「测试」小节
- **THEN** 文档 SHALL 说明无需 DASHSCOPE 变量即可运行，并给出 `gh auth login` 与 `pytest` 命令

#### Scenario: 用户能配置 dashscope 语义与 rerank 场景

- **GIVEN** 用户想跑通 dashscope 语义通道与 rerank 真路径
- **WHEN** 阅读「测试」小节的环境变量配置清单
- **THEN** 文档 SHALL 列出 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`DASHSCOPE_RERANK_URL`、`GH_SEARCH_BACKEND`、`GH_SEARCH_DB` 等变量的用途、是否必需与示例，并 SHALL 区分嵌入端点与 rerank 端点

### Requirement: 参数校验测试

端到端测试 SHALL 覆盖非法参数的校验行为。

#### Scenario: 非法通道参数返回 422

- **GIVEN** 服务已启动
- **WHEN** 测试以 `channel=invalid` 请求 `/api/v1/search`
- **THEN** 响应状态码为 422，且响应体包含校验错误信息
