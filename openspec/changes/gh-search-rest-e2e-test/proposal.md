## Why

`gh-search` 已有完整的 REST 服务（`service/main.py`）与规范 `rest-search-api`，但仓库里只有定性的评测笔记（`references/testing-evaluation.md`），没有可重复执行的端到端测试。现在需要一套能真实启动 FastAPI 服务、逐接口断言结果并落盘测试记录的脚本，把「REST 服务端到端是否可用」从口头结论变成可复现证据。

## What Changes

- 新增可复用的端到端测试脚本 `gh-search/tests/test_rest_e2e.py`（pytest），真实启动 uvicorn 服务进程后驱动 HTTP 客户端执行断言。
- 测试覆盖 REST 服务全接口：
  - `GET /api/v1/health`：健康检查返回 `status=ok`、`db_connected=true`。
  - `POST /api/v1/search`（`channel=keyword`）：GraphQL 关键词召回，返回结构化 `candidates_list`。
  - `POST /api/v1/search`（`channel=semantic`，dashscope qwen 后端）：语义召回；在 `DASHSCOPE_API_KEY`/`DASHSCOPE_BASE_URL` 缺失时按 SKILL 契约回退或跳过，并显式记录原因。
  - `POST /api/v1/search`（`channel=hybrid`）：并行合并召回。
  - `POST /api/v1/search` 的 `enrich`/`readme`/`rerank` 参数：验证管线步骤出现在响应 `pipeline_steps` 中；`rerank` 在缺少百炼密钥时验证优雅降级。
  - `GET /api/v1/billing/summary`：搜索后按 `user_id`+`period` 汇总调用次数。
  - 非法 `channel` 参数返回 HTTP 422。
- 新增测试结果记录文档 `gh-search/references/e2e-test-report.md`，记录执行环境、命令、每条用例的通过/跳过/失败与关键产物（响应结构、计费落库、降级路径）。
- 补充 `gh-search/README.md` 的「测试」小节，说明如何安装测试依赖、如何运行，以及**完整的环境变量配置清单**（含 `DASHSCOPE_API_KEY`/`DASHSCOPE_BASE_URL`/`DASHSCOPE_RERANK_URL` 与 `GH_SEARCH_BACKEND`/`GH_SEARCH_DB` 等，及其必需条件与示例），让用户能照着配置跑通 keyword/hybrid/health/billing 与 dashscope 语义/rerank 两种场景。

## Capabilities

### New Capabilities

- `gh-search-rest-e2e-test`: 提供一套可复用的 REST 服务端到端测试，覆盖 `/health`、`/search`（keyword/semantic/hybrid 与 enrich/readme/rerank 参数）、`/billing/summary` 及非法参数校验，并将执行结果记录为可追溯的报告。

### Modified Capabilities

<!-- 本次仅新增测试与文档，不改变 REST 服务的运行时 spec 行为，无 spec 级修改 -->

## Impact

- 新增文件：`gh-search/tests/test_rest_e2e.py`、`gh-search/tests/conftest.py`（如需）、`gh-search/references/e2e-test-report.md`。
- 文档：`gh-search/README.md`（新增「测试」小节，含测试依赖、运行命令与环境变量配置清单）。
- 依赖：测试运行依赖 `pytest`、`httpx`、`uvicorn`、`fastapi`（服务已依赖 fastapi/uvicorn/pydantic；系统 Python 已安装 pytest/httpx）。
- 外部依赖：keyword/hybrid 通道依赖已认证 `gh` CLI；semantic/rerank 的 dashscope 路径依赖 `DASHSCOPE_API_KEY`/`DASHSCOPE_BASE_URL`/`DASHSCOPE_RERANK_URL`，缺失时测试记录为跳过或验证降级。
- 运行时行为：不修改任何 `service/` 或 `scripts/` 生产代码。
