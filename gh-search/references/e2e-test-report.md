# gh-search REST 服务端到端测试报告

> 记录 `tests/test_rest_e2e.py` 的一次实际执行结果。测试通过真实 uvicorn 子进程启动 `service.main:app`，用 `httpx` 驱动 HTTP 断言。

## 执行环境

| 项 | 值 |
|----|----|
| 日期 | 2026-09-02 |
| Python | 3.12.3（系统 `/usr/bin/python3`） |
| pytest | 9.1.1 |
| httpx | 0.28.1 |
| GitHub CLI 认证 | 已登录（`gh auth status` 通过） |
| `DASHSCOPE_API_KEY` | 已配置（不回显明文） |
| `DASHSCOPE_BASE_URL` | 已配置为 `.../compatible-mode/v1`（embedding 端点，实测 200） |
| `DASHSCOPE_RERANK_URL` | 已配置工作空间基地址（代码 append `/compatible-api/v1/reranks`） |
| 语义库 | `data/gh_search_qwen.db`（fixture 通过 `GH_SEARCH_DB` 注入） |
| 语义后端 | `dashscope`（fixture 通过 `GH_SEARCH_BACKEND` 注入） |

## 运行命令

```bash
cd gh-search
python3 -m pytest tests/test_rest_e2e.py -v
```

## 结果总览

| 状态 | 数量 |
|------|------|
| 通过 | 8 |
| 跳过 | 0 |
| 失败 | 0 |

**执行时长**：约 89.54s（配置 DASHSCOPE 凭据后的完整执行，rerank 真路径已修复）。

## 逐用例结果

| 用例 | 状态 | 说明 |
|------|------|------|
| `test_health` | PASSED | `/api/v1/health` 返回 200，`status=ok`、`db_connected=true` |
| `test_keyword_search` | PASSED | `channel=keyword` 返回 200，含 `candidates_list` |
| `test_semantic_search` | PASSED | `channel=semantic` 返回 200，含 `candidates_list`（embedding 真实调用，`compatible-mode/v1/embeddings` 返回 200） |
| `test_hybrid_search` | PASSED | `channel=hybrid` 返回 200，含 `candidates_list`、`channel=hybrid` |
| `test_full_pipeline` | PASSED | `enrich/readme/rerank=true` 时 `pipeline_steps` 含 `recall(keyword)`、`enrich`、`readme`、`rerank`，且候选含 `_rerank_score`（真路径） |
| `test_default_pipeline` | PASSED | 默认参数 `pipeline_steps` 仅含 `recall(keyword)` |
| `test_billing_summary` | PASSED | 搜索后按 `user_id`+`period` 汇总 `total_calls > 0` |
| `test_invalid_channel` | PASSED | `channel=invalid` 返回 422 |

## 关键产物

### 响应结构（健康检查）

`GET /api/v1/health` 返回 200，响应体形如：

```json
{
  "status": "ok",
  "db_connected": true,
  "repo_count": 432586,
  "vector_count": 432586
}
```

### 响应结构（搜索）

`POST /api/v1/search` 成功时返回 200，包含 `query`、`channel`、`candidates`、`candidates_list`、`pipeline_steps`、`elapsed`。`candidates_list` 为数组，元素含 `full_name`、`description`、`topics`、`stars` 等元数据。

### 计费落库

搜索后 `GET /api/v1/billing/summary` 返回 200，对应 `user_id` 的 `total_calls > 0`，证明 `service/billing.py` 的 `record_call` 正确写入 `data/billing.db`。

### 真路径与降级

- **语义通道**：配置 `DASHSCOPE_BASE_URL=.../compatible-mode/v1` 后真实跑通（embedding 端点返回 200）。
- **rerank**：`test_full_pipeline` 中 `rerank=true`，使用 `qwen3.7-text-rerank` 模型，候选真实产出 `_rerank_score`（真路径验证通过）。

## 端点与修复结论

| 项 | 结果 |
|------|------|
| `DASHSCOPE_BASE_URL=.../compatible-mode/v1` + `/embeddings` | ✅ 200，语义通道真实可用 |
| `DASHSCOPE_RERANK_URL=<workspace>` + `/compatible-api/v1/reranks`（代码 append） | ✅ 200，`qwen3.7-text-rerank` 模型可用 |
| 修复项 | rerank 模型名 `qwen3-rerank` → `qwen3.7-text-rerank` |
| 修复项 | `rerank()` 补充读取 `DASHSCOPE_API_KEY`/`DASHSCOPE_RERANK_URL` 环境变量（此前不读，导致永远走降级） |
| 修复项 | `_build_document` 截断超长 description（`MAX_DESC_CHARS=350`），避免单条 document 超 50000 触发 `InvalidParameter` |

## 待补测项

- rerank 数值基准（nDCG/MRR）仍未做；当前仅验证端点可达、`_rerank_score` 正常产出。
