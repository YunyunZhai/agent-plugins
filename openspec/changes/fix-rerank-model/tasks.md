## 1. 代码修改

- [ ] 1.1 将 `scripts/search/rerank_results.py` 的 `DEFAULT_MODEL` 从 `qwen3-rerank` 改为 `qwen3.7-text-rerank`

## 2. 文档同步

- [ ] 2.1 更新 `SKILL.md` 中 rerank 模型名（`qwen3-rerank` → `qwen3.7-text-rerank`）
- [ ] 2.2 更新 `README.md` 中 rerank 模型名与端点说明
- [ ] 2.3 更新 `references/architecture.md` 中 rerank 模型名
- [ ] 2.4 更新 `references/error-handling.md` 中 rerank 相关说明（如涉及）
- [ ] 2.5 更新 `references/testing-evaluation.md` 中「qwen3-rerank 精排」未验证项，记录模型修正

## 3. 验证

- [ ] 3.1 配置 DASHSCOPE 凭据后运行 `pytest gh-search/tests/test_rest_e2e.py -v`，确认 rerank 真路径产出 `_rerank_score`
- [ ] 3.2 运行 `openspec validate fix-rerank-model --type change --strict`
