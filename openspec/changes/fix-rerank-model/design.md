## Context

Step 4.5 rerank 由 `scripts/search/rerank_results.py` 实现，调用百炼 rerank 服务。当前代码硬编码 `DEFAULT_MODEL = "qwen3-rerank"`，端点通过 `DASHSCOPE_RERANK_URL` + 代码 append `/compatible-api/v1/reranks` 拼接。

实测（本机账号，2026-09-02）：

- `qwen3-rerank`：`403 AllocationQuota.FreeTierOnly`（免费额度耗尽）。
- `qwen3.7-text-rerank`：`200`，正确返回 `results[].relevance_score`。

因此 rerank 真路径无法工作，原因是模型名过时/错误，而非端点格式问题。

## Goals / Non-Goals

**Goals:**

- 让 rerank 在现有端点下真正产出 `_rerank_score`。
- 同步修正所有文档中的 rerank 模型名，避免误导。

**Non-Goals:**

- 不改变端点拼接逻辑、请求 body 结构、重试/降级策略。
- 不引入模型名可配置化（除非有必要；本次仅修正默认值）。
- 不改动 REST 服务接口契约。

## Decisions

### D1: 将默认模型名改为 `qwen3.7-text-rerank`

**选择**：`DEFAULT_MODEL = "qwen3.7-text-rerank"`。

**备选方案**：
- A) 改为 `gte-rerank-v2` —— 实测也可用，但需要改 body 格式（原生 text-rerank），改动面更大。
- B) 保持 `qwen3-rerank` 并让用户解决配额 —— 不解决代码本身的错误模型名问题。

**理由**：`qwen3.7-text-rerank` 与现有 OpenAI-compatible `/compatible-api/v1/reranks` 路径直接兼容（实测 200），改动最小、风险最低。

### D2: `DASHSCOPE_RERANK_URL` 语义保持为工作空间基地址

**选择**：继续要求 `DASHSCOPE_RERANK_URL` 填 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com`，代码 append `/compatible-api/v1/reranks`。

**理由**：实测该组合对 `qwen3.7-text-rerank` 返回 200，无需改端点逻辑。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| `qwen3.7-text-rerank` 在其它账号/工作空间不存在或未开通 | 文档保留模型名说明；rerank 已有优雅降级，失败不中断流程 |
| 文档遗漏旧模型名 | 全仓库 grep 并同步更新 |

## Migration Plan

1. 修改 `rerank_results.py` 的 `DEFAULT_MODEL`。
2. 全仓库替换文档中的 `qwen3-rerank` 为 `qwen3.7-text-rerank`。
3. 重跑 `pytest tests/test_rest_e2e.py`（配置 DASHSCOPE 凭据）确认 rerank 真路径产出 `_rerank_score`。
4. 更新 `testing-evaluation.md` 未验证项状态。

## Open Questions

- 无。模型名已通过端点实测确认。
