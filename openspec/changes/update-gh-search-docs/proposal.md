## Why

`gh-search` 的代码已经从单一语义索引管线演化为「关键词管线 + 语义索引管线 + REST service」三层结构，但 `references/architecture.md` 仍只描述语义索引路径，且缺漏了大量新脚本、`_common` 包结构、DB schema 变更与运维命令。同时，此前做过的模型 A/B、GPU/CPU 嵌入基准、搜索质量探针等测试验证散落在日志和 JSON 中，没有一份可复用的测试文档。文档与代码已明显脱节，需要对齐。

## What Changes

- 重写 `plugins/gh-search/skills/gh-search/references/architecture.md`，覆盖三层：
  - 语义索引管线（fetch/build/import/sync-stars/incremental-update + semantic_search）
  - 关键词管线（search_repos → enrich_metrics → fetch_readme → rerank_results）
  - REST service（FastAPI 端点、pipeline 编排、config、billing）
  - `_common` 包结构、DB schema（含 `stars`、`readme_embed_text`、`repo_readme_vectors`）、当前运维命令速查
- 新建 `plugins/gh-search/skills/gh-search/references/testing-evaluation.md`，整理此前测试验证：
  - 模型对比（bge-m3 vs doubao/llama，A/B 重叠率与已知正样本命中）
  - 嵌入性能基准（CPU int8/fp32、ONNX、GPU Kaggle T4、Ark 限速）
  - 搜索质量探针（semantic/hybrid/keyword、star 先验 λ、深窗口 k、README 双通道）
  - 已知未验证项（rerank 仅验证优雅降级、无正式标注评测集等）
- 小幅代码清理（修正过时标注，不改变行为）：
  - `build_index.py`：`local` 后端已加载 fp32 ONNX，却仍标注 `int8`，修正标签/注释
  - `README.md`：`fetch_repos.py --sync-stars` 命令已迁移，修正为 `maintenance/sync_stars.py`

## Capabilities

### New Capabilities

- `gh-search-docs`: gh-search 架构文档与测试评测文档必须准确反映当前三层实现（关键词管线、语义索引管线、REST service）与既有测试验证结论。

### Modified Capabilities

<!-- 无 spec 级行为变化，仅文档与注释对齐 -->

## Impact

- 文档：`references/architecture.md`、新增 `references/testing-evaluation.md`
- 代码注释/标签：`scripts/pipeline/build_index.py`、`README.md`
- 行为：无变化（不新增、不修改任何运行时逻辑或 spec 契约）
- 相关产物：`data/*.log`、`data/compare_report*.json`、`data/debug_*.json` 作为测试文档依据，不修改
