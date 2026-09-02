## Why

`scripts/search/rerank_results.py` 硬编码的 rerank 模型 `qwen3-rerank` 在本账号下返回 `403 AllocationQuota.FreeTierOnly`（免费额度耗尽），导致 Step 4.5 始终走优雅降级、无法产出 `_rerank_score`。实测确认正确的模型名是 `qwen3.7-text-rerank`，在现有端点 `<workspace>/compatible-api/v1/reranks` 下返回 200 并产出 relevance_score。

## What Changes

- 将 `scripts/search/rerank_results.py` 的 `DEFAULT_MODEL` 从 `qwen3-rerank` 改为 `qwen3.7-text-rerank`。
- 同步更新 `SKILL.md`、`README.md`、`references/architecture.md`、`references/error-handling.md` 中提到的 rerank 模型名与端点说明（保持 `DASHSCOPE_RERANK_URL` 语义：填工作空间基地址，代码 append `/compatible-api/v1/reranks`）。
- 更新 `references/testing-evaluation.md` 中「qwen3-rerank 精排」未验证项，记录本次模型修正与实测结论。

## Capabilities

### New Capabilities

<!-- 本次为缺陷修复，不引入新行为 spec -->

### Modified Capabilities

<!-- rerank 模型名属于实现细节，不改变 rest-search-api 的 spec 级行为契约 -->

## Impact

- 生产代码：`gh-search/scripts/search/rerank_results.py`（一行 `DEFAULT_MODEL` 修改）。
- 文档：`gh-search/SKILL.md`、`gh-search/README.md`、`gh-search/references/architecture.md`、`gh-search/references/error-handling.md`、`gh-search/references/testing-evaluation.md`。
- 行为：rerank 真路径在配置 `DASHSCOPE_API_KEY` + `DASHSCOPE_RERANK_URL`（工作空间基地址）后，可真正产出 `_rerank_score`。
- 无破坏性变更：请求端点与 body 结构不变，仅模型名变更。
