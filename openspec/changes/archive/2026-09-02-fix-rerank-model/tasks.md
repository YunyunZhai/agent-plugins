## 1. 代码修改

- [x] 1.1 将 `scripts/search/rerank_results.py` 的 `DEFAULT_MODEL` 从 `qwen3-rerank` 改为 `qwen3.7-text-rerank`
- [x] 1.2 修复 `rerank()` 未读取 `DASHSCOPE_API_KEY`/`DASHSCOPE_RERANK_URL` 环境变量的 bug（补 `import os` + 环境变量读取）
- [x] 1.3 在 `_build_document` 中截断超长 description（`MAX_DESC_CHARS=350`，与 `sqlite_store.py` 一致），避免单条 document 超 rerank 服务上限（实测 55700 字符触达 400）

## 2. 文档同步

- [x] 2.1 更新 `SKILL.md` 中 rerank 模型名（`qwen3-rerank` → `qwen3.7-text-rerank`）
- [x] 2.2 更新 `README.md` 中 rerank 模型名与端点说明
- [x] 2.3 更新 `references/architecture.md` 中 rerank 模型名
- [x] 2.4 更新 `references/error-handling.md` 中 rerank 相关说明（如涉及）
- [x] 2.5 更新 `references/testing-evaluation.md` 中「qwen3-rerank 精排」未验证项，记录模型修正

## 3. 验证

- [x] 3.1 配置 DASHSCOPE 凭据后运行 `pytest gh-search/tests/test_rest_e2e.py -v`，确认 rerank 真路径产出 `_rerank_score`
- [x] 3.2 运行 `openspec validate fix-rerank-model --type change --strict`
