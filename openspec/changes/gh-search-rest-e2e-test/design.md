## Context

`gh-search` 已经有一个可运行的 REST 服务（`service/main.py`，FastAPI），暴露三个接口：

- `GET /api/v1/health`
- `POST /api/v1/search`（`channel=keyword|semantic|hybrid` + `enrich`/`readme`/`rerank` 参数）
- `GET /api/v1/billing/summary`

对应行为规范已在 `openspec/specs/rest-search-api/spec.md`、`billing/spec.md`、`config-management/spec.md` 中记录。但仓库目前只有定性的测试评测笔记（`references/testing-evaluation.md`），没有可重复执行的端到端测试来证明 REST 服务全接口可用。

本次改动只新增测试与结果记录，不改动 `service/` 或 `scripts/` 的生产代码。

### 运行时依赖与凭据现状

- keyword/hybrid 通道依赖已认证的 `gh` CLI（本机 `gh auth status` 已通过）。
- semantic 通道（dashscope qwen 后端）依赖环境变量 `DASHSCOPE_API_KEY` 与 `DASHSCOPE_BASE_URL`；rerank 依赖 `DASHSCOPE_API_KEY` 与 `DASHSCOPE_RERANK_URL`。
- 这些 DASHSCOPE 凭据**不在 `config.yaml` 中**，`service/config.py` 也没有对应的环境变量覆盖映射；它们由 `scripts/search/semantic_search.py` 与 `scripts/search/rerank_results.py` 直接 `os.environ.get(...)` 读取。
- 当前环境只探测到 `PINECONE_API_KEY`，未设置任何 DASHSCOPE 相关变量，因此 semantic/rerank 的 dashscope 路径需要按「缺失凭据 → 跳过或断言降级」处理。

### 环境变量清单（如何配置）

DASHSCOPE 相关变量**不经过 `config.yaml`/`service/config.py`**，由 `semantic_search.py` 与 `rerank_results.py` 直接 `os.environ.get(...)` 读取，必须在启动 uvicorn 的 shell 里 export。其余变量可由 `config.yaml` 或环境变量覆盖。

| 环境变量 | 用途 | 是否必需 | 取值/示例 | 被谁读取 |
|---------|------|---------|----------|---------|
| `DASHSCOPE_API_KEY` | 百炼 API Key | 语义通道（dashscope）与 rerank 真路径必需；缺失时测试 skip | `export DASHSCOPE_API_KEY="sk-..."` | `semantic_search.py`、`rerank_results.py` |
| `DASHSCOPE_BASE_URL` | 百炼嵌入 API 端点（代码会追加 `/embeddings`） | 语义通道（dashscope）必需 | 百炼 OpenAI 兼容嵌入端点 | `semantic_search.py` |
| `DASHSCOPE_RERANK_URL` | rerank API 端点（代码会追加 `/compatible-api/v1/reranks`） | rerank 真路径必需 | 格式 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com` | `rerank_results.py` |
| `DASHSCOPE_MODEL` | 百炼嵌入模型 | 可选 | 默认 `qwen3.7-text-embedding` | `semantic_search.py` |
| `GH_SEARCH_BACKEND` | 查询嵌入后端 | 语义通道用 dashscope 时必需 | `export GH_SEARCH_BACKEND=dashscope` | `service/config.py`、`semantic_search.py` |
| `GH_SEARCH_DB` | 语义 sqlite 库路径 | 语义通道用 qwen 库时必需 | `export GH_SEARCH_DB=gh-search/data/gh_search_qwen.db` | `service/config.py`、`sqlite_store.py` |
| `GH_SEARCH_EMBED_DIM` | 向量维度 | 可选（qwen 默认 1024 无需改） | `1024` | `service/config.py`、`sqlite_store.py` |
| `GH_TOKEN` / `GITHUB_TOKEN` | 显式 GitHub token | 可选；不设则用 `gh` CLI 已认证凭据 | `export GH_TOKEN="ghp_..."` | `github_client.py`、`service/config.py` |
| `GH_SEARCH_TIMEOUT` | GitHub API 单次超时（秒） | 可选 | 默认 `60` | `github_client.py`、`service/config.py` |
| `PINECONE_API_KEY` | Pinecone 嵌入 | 仅 pinecone 后端需要，dashscope 路径不需要 | — | `semantic_search.py` |

**跑通 keyword/hybrid/health/billing（无需任何 DASHSCOPE 变量）**：

```bash
gh auth login                      # 一次性，确保 GitHub 认证可用
cd gh-search
pytest tests/test_rest_e2e.py -v
```

**跑通 dashscope 语义 + rerank 真路径（在当前 shell 一次性 export）**：

```bash
cd gh-search
export DASHSCOPE_API_KEY="sk-..."
export DASHSCOPE_BASE_URL="https://<embedding-base>"          # 代码会追加 /embeddings
export DASHSCOPE_RERANK_URL="https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com"
export GH_SEARCH_BACKEND=dashscope
export GH_SEARCH_DB=gh-search/data/gh_search_qwen.db
pytest tests/test_rest_e2e.py -v
```

> 注意：`DASHSCOPE_BASE_URL` 与 `DASHSCOPE_RERANK_URL` 是**不同端点**——前者用于 query 嵌入（`/embeddings`），后者用于 rerank（`/compatible-api/v1/reranks`），不要混用。这些值不落盘到仓库，测试报告只记录「已配置/未配置」，不回显密钥明文。

## Goals / Non-Goals

**Goals:**

- 新增一套可复用的 pytest 端到端测试，真实启动 uvicorn 服务进程，通过 HTTP 客户端驱动断言。
- 覆盖 REST 服务全部公开接口与关键管线参数。
- 生成可追溯的测试结果记录文档。
- 在 README 中补充「测试」运行说明。

**Non-Goals:**

- 不修改任何生产代码（`service/`、`scripts/`）。
- 不新增正式的标注评测集（query→gold-repo）或 nDCG/MRR 指标。
- 不引入 CI 工作流（除非后续明确要求）。
- 不要求 DASHSCOPE 凭据在本次执行中必须可用；缺失时走跳过/降级并记录原因。

## Decisions

### D1: 使用 pytest + httpx，通过真实服务进程做端到端测试

**选择**：用 `pytest` 作为测试框架，`httpx` 作为 HTTP 客户端，在 session 级 fixture 中用 `uvicorn` 子进程启动 `service.main:app`，测试结束后终止进程。

**备选方案**：
- A) FastAPI `TestClient`（`starlette.testclient`）——进程内 ASGI，不需要真实端口。但它是进程内测试，无法覆盖「服务进程真实启动 + 真实 socket」这一端到端语义。
- B) 纯 shell 脚本 + `curl`——可读性好但断言与报告结构弱。

**理由**：真实子进程启动更接近「端到端」定义；pytest 提供清晰的通过/跳过/失败语义和 fixture 生命周期管理；httpx 同步 API 简单稳定。

### D2: 用 session 级 fixture 启动服务，复用单一服务实例

**选择**：`scope="session"` 的 fixture 启动一个 uvicorn 子进程（`--port` 选一个非冲突端口），yield 一个 base URL，session 结束后 terminate。

**理由**：避免每个测试用例反复启动/停止服务（模型加载、SQLite 连接等开销大），同时保证测试共享同一计费库，便于 `/billing/summary` 断言。

### D3: 语义/rerank 凭据缺失时用 `pytest.skip` 或断言降级，而非失败

**选择**：对 dashscope 语义通道与 rerank 真路径，若检测到缺少 `DASHSCOPE_API_KEY`/`DASHSCOPE_BASE_URL`/`DASHSCOPE_RERANK_URL`，测试标记为 `skip`（并附带原因）；对 rerank 的「优雅降级」路径做确定性断言。

**备选方案**：
- A) 硬性要求凭据存在 → 当前环境不可运行，测试整体无法通过。
- B) 忽略缺失凭据直接通过 → 掩盖真实不可用。

**理由**：端到端测试要能在无密钥环境下可复现跑通，同时不隐瞒未验证的路径。skip 显式记录「未覆盖原因」，符合 SKILL.md 的降级契约。

### D4: 计费断言使用独立测试用户与可预测 period

**选择**：测试使用固定的 `X-User-Id`（如 `e2e-test`）与动态当前月份 `period`（`YYYY-MM`），在搜索用例执行后断言 `total_calls > 0`。

**理由**：`billing.py` 的 `get_summary` 按 `strftime('%Y-%m', timestamp)` 聚合，动态 period 可避免跨月执行时断言失败；固定 user_id 便于区分测试数据。

### D5: 测试结果记录文档作为独立产物，而非测试运行时自动生成

**选择**：手动维护 `references/e2e-test-report.md`，记录执行环境、命令、逐用例结果与关键产物；测试脚本本身不强制写报告文件。

**理由**：报告包含环境上下文（gh 认证状态、凭据缺失原因、语义库路径）与解释性说明，这些不适合由脚本机械生成；保持脚本单一职责（断言），报告单独人工/半自动维护。

## C4 图（container 级，轻量）

```text
┌────────────────────────────┐
│  测试执行者 (developer)      │
└─────────────┬──────────────┘
              │ 运行 pytest
              ▼
┌────────────────────────────┐
│  pytest e2e test suite      │
│  (tests/test_rest_e2e.py)   │
└─────────────┬──────────────┘
              │ HTTP (httpx)
              ▼
┌────────────────────────────┐        ┌──────────────────────────┐
│  gh-search REST 服务        │───────▶│  GitHub GraphQL API       │
│  (FastAPI, uvicorn 子进程)  │        │  (keyword/hybrid 通道)     │
└──────┬──────────┬──────────┘        └──────────────────────────┘
       │          │
       │          │ sqlite-vec kNN + dashscope embedding (需 DASHSCOPE_*)
       │          ▼
       │        ┌──────────────────────────┐
       │        │ 语义索引库                 │
       │        │ gh_search_qwen.db (vec0)  │
       │        └──────────────────────────┘
       ▼
┌────────────────────────────┐
│  billing.db (SQLite)        │
│  (计费记录 / summary)        │
└────────────────────────────┘
```

**图例说明**：

- 边界：测试套件与 REST 服务是两个独立进程，通过 HTTP 交互。
- 责任：测试套件负责启动/停止服务并断言；服务负责执行搜索管线与计费落库。
- 关键关系：keyword/hybrid 依赖 GitHub GraphQL；semantic 依赖本地 sqlite-vec 索引 + dashscope 嵌入；计费写本地 `billing.db`。
- 假设：服务子进程与测试套件运行在同一主机，网络可达 GitHub。
- 未决：dashscope 凭据当前缺失，语义真路径的端到端断言未覆盖（记为 skip）。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| dashscope 凭据缺失导致 semantic/rerank 无法真端到端 | 测试显式 skip 并记录原因；README 说明设置 `DASHSCOPE_API_KEY`/`DASHSCOPE_BASE_URL`/`DASHSCOPE_RERANK_URL` 后可完整跑通 |
| keyword/hybrid 依赖实时 GitHub，网络抖动导致 flaky | 断言聚焦响应结构（`candidates_list` 存在）而非具体 repo；服务侧已有重试；报告记录失败场景 |
| 语义库（`gh_search_qwen.db`）体积大、模型加载慢 | 仅在语义用例中加载，且凭据缺失时直接 skip，避免无谓开销 |
| 端口冲突 | 测试选用非默认端口或动态空闲端口启动 uvicorn |
| 计费库被测试污染 | 使用独立 `X-User-Id`；必要时在测试前重置/使用临时 billing db 路径（通过 `config.yaml` 或 env 覆盖） |
| `config.yaml` 是 gitignored 的，CI/他机可能没有 | 测试 fixture 通过 env 覆盖 db 路径或使用默认路径，报告记录实际配置 |

## Migration Plan

1. 创建 `gh-search/tests/` 目录与 `__init__.py`（如需 pytest 收集）。
2. 编写 `test_rest_e2e.py` 的 session fixture：选择端口、设置必要的 env（`GH_SEARCH_DB`、`GH_SEARCH_BACKEND` 等）、启动 uvicorn、等待 `/health` 就绪。
3. 编写各接口测试用例，语义/rerank 凭据缺失时 skip。
4. 本地运行 `pytest gh-search/tests/test_rest_e2e.py -v`，确认全部通过/跳过符合预期。
5. 根据实际执行结果撰写 `references/e2e-test-report.md`。
6. 在 `README.md` 增加「测试」小节。
7. 回滚策略：本改动不触碰生产代码；如需回滚，删除新增测试与文档即可。

## Open Questions

- 无现有 in-force ADR 需要 supersede（`<repo>/adr/` 不存在）。
- 是否需要将 DASHSCOPE 凭据也纳入 `config.yaml`/`config.py` 的环境变量覆盖？当前实现直接从脚本读环境变量，本次测试不修改该行为，仅在报告中标注。
