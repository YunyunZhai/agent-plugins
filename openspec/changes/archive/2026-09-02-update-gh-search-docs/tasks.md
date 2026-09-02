## 1. 重写架构文档

- [x] 1.1 读取 `plugins/gh-search/skills/gh-search/references/architecture.md` 与全部 `scripts/**/*.py`、`service/*.py`、`config.yaml.example` 核对当前实现
- [x] 1.2 用轻量 C4（Mermaid flowchart）重写 `architecture.md`，覆盖关键词管线、语义索引管线、REST service 三层
- [x] 1.3 补齐 `_common` 包结构、DB schema（`stars`、`readme_embed_text`、`repo_readme_vectors`）与当前有效的运维命令（含 `maintenance/sync_stars.py`、`hybrid_search.py`、`rerank_results.py`）

## 2. 新建测试评测文档

- [x] 2.1 核对 `data/compare_report*.json`、`data/debug_*.json`、各 `*.log` 与 `references/embedding-engineering-notes.md` 的验证证据
- [x] 2.2 新建 `plugins/gh-search/skills/gh-search/references/testing-evaluation.md`，汇总模型对比、嵌入性能基准、搜索质量探针、已知未验证项，并为每条结论标注证据来源

## 3. 代码清理

- [x] 3.1 修正 `scripts/pipeline/build_index.py` 中 local 后端已加载 fp32 ONNX 却标注 int8 的标签与 docstring
- [x] 3.2 修正 `README.md` 中已失效的 `fetch_repos.py --sync-stars` 命令为 `maintenance/sync_stars.py`

## 4. 验证

- [x] 4.1 运行 `openspec validate update-gh-search-docs --type change --strict` 确认 artifacts 与变更一致
- [x] 4.2 检查 `architecture.md` 与 `testing-evaluation.md` 中所有脚本名、命令、schema 字段与源码一致
