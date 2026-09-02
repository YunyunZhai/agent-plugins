## Context

`gh-search` 从最初的语义索引管线，逐步演化为三层结构：

1. **关键词管线**（在线，无索引）：`search_repos` → `enrich_metrics` → `fetch_readme` → `rerank_results`，通过 JSON 文件/进程间传递候选。
2. **语义索引管线**（离线建库 + 在线查询）：`fetch_repos` / `fetch_readmes` / `build_index` / `import_gpu_vectors` / `sync_stars` / `incremental_update`，查询侧 `semantic_search` / `hybrid_search`。
3. **REST service**：FastAPI 封装同一批脚本，暴露 `/api/v1/*` 端点，含 config 与 billing。

当前 `references/architecture.md` 只覆盖第 2 层语义路径，且缺漏 `_common` 包结构、DB schema 变更、运维命令。测试验证（模型 A/B、CPU/GPU 基准、搜索探针）散落在 `data/*.json` 与日志中，无统一文档。

本变更是纯文档对齐 + 两处注释级代码清理，不改变任何运行时行为。

## Goals / Non-Goals

**Goals:**
- 让 `architecture.md` 准确反映当前三层结构与脚本职责。
- 新建 `testing-evaluation.md`，把既有验证证据整理成可复用、可追溯的测试文档。
- 修正代码中已过时但会误导维护者的标签/注释。

**Non-Goals:**
- 不新增/修改任何 spec 级行为契约。
- 不改动脚本的运行逻辑、CLI 参数、DB schema。
- 不新增自动化测试框架或标注评测集。
- 不重写 `embedding-engineering-notes.md`、`error-handling.md`、`colab_gpu_embedding.md`（仅在新文档中交叉引用）。

## Decisions

### D1 — architecture.md 用「三层 + 两图 + 表」结构

用 C4 轻量分层组织，只画能解答「数据从哪来、经过哪些脚本、去向哪里」的层级：

- **系统上下文图（Mermaid flowchart）**：说明 actor（LLM agent / REST 客户端 / 运维脚本）与外部系统（GitHub GraphQL/REST、sqlite-vec DB、DashScope/Ark/Pinecone 嵌入与重排、Kaggle T4）。
- **容器图（Mermaid flowchart）**：区分三个可运行单元——SKILL 脚本（CLI）、REST service（FastAPI）、本地数据/索引（sqlite + vec0），以及它们与外部系统的依赖。

理由：当前文档只有一页 ASCII 管线图，无法区分「关键词在线管线」与「语义索引管线」的边界；Mermaid 便于在 README/references 中渲染，且轻量 C4 比严格四级更省维护成本（本仓库不是大型分布式系统）。

替代方案：严格 C4 四级展开——被否决，代码级 diagram 对纯文档变更无增量价值，且维护成本高。

### D2 — 文件清单改为按三层分组 + 每脚本一行

`architecture.md` 现有「文件清单」只有 8 行且只列语义管线。改为三张表：

1. 关键词管线（`search/*` 6 个脚本）
2. 语义管线（`data/*`、`pipeline/*`、`maintenance/*`）
3. 共享层（`_common/*` 4 模块）+ REST service（`service/*` 5 文件）

理由：探索确认实际有 17+ 脚本和 5 个 service 文件，扁平表会失焦；按职责分组让维护者能快速定位。

### D3 — 新增 DB schema 小节

在 `architecture.md` 增加 schema 表：`repos`（含 `stars`、`readme_embed_text` 两个迁移新增列）、`embed_status`、`repo_vectors`、`repo_readme_vectors`。

理由：探索发现文档只字未提 `readme_embed_text`、`stars` 列和 `repo_readme_vectors` 表，但这些是语义查询与 README 双通道的关键。

### D4 — 修正运维命令

将已失效的 `fetch_repos.py --sync-stars` 命令替换为 `maintenance/sync_stars.py`，并补齐 `hybrid_search`、`rerank_results` 的查询命令示例。

理由：`fetch_repos.py` 的 argparse 已无 `--sync-stars`，文档命令会直接失败。

### D5 — testing-evaluation.md 采用「结论优先 + 证据链接」结构

新建文档分四大块：

1. **模型对比**（结论：bge-m3 与 doubao 跨语言质量相当，优于 llama；证据：`compare_report.json`、`compare_report_en.json`）
2. **嵌入性能基准**（CPU int8/fp32/ONNX、GPU Kaggle T4、Ark 限速；证据：各 log + `embedding-engineering-notes.md`）
3. **搜索质量探针**（star 先验 λ=0.03、深窗口 k=4000、README 双通道、hybrid 回退；证据：`debug_*.json`、各 search log）
4. **已知未验证项**（rerank 仅验证优雅降级、无正式标注评测集、hybrid 大样本未测）

理由：测试文档的价值在「能快速知道验证过什么、结论是什么、证据在哪」，而非复述原始日志。详细原始实验仍留在 `embedding-engineering-notes.md`，避免两处重复。

### D6 — 代码清理只做两处过时标签修正

1. `build_index.py`：`local` 后端已加载 fp32 ONNX（`onnx/model.onnx`），但模型默认标签仍写 `BAAI/bge-m3(int8-onnx)`，docstring 也残留「int8」。修正为 fp32，消除「int8 禁止混入在线链路」与标签自相矛盾的误导。
2. `README.md`：`--sync-stars` 命令从 `fetch_repos.py` 迁移到 `maintenance/sync_stars.py`，修正命令示例。

理由：探索确认这两处与实际实现矛盾；属注释/文档级修正，零行为风险。

## Risks / Trade-offs

- [文档再次漂移] 代码继续演化而文档未同步 -> 在 testing-evaluation.md 与 architecture.md 顶部标注「更新日期」并说明以脚本 `--help`/源码为准的核对路径。
- [测试文档引用失效] 日志/JSON 路径变化 -> 使用相对 `data/` 路径并附文件名，不硬编码绝对路径。
- [过度解读测试结论] 无正式标注评测集，模型对比只有 top-20 重叠率与已知正样本 -> 文档显式标注「结论为定性/相对比较，非 nDCG/Recall@k 正式评测」，避免误导。

## Migration Plan

无运行时迁移。落地步骤：

1. 重写 `references/architecture.md`（覆盖三层 + C4 图 + schema + 命令速查）。
2. 新建 `references/testing-evaluation.md`。
3. 修正 `build_index.py` 的 int8 标签与 docstring。
4. 修正 `README.md` 的 `--sync-stars` 命令。
5. 运行 `openspec verify` 确认变更与 artifacts 一致。

回滚：纯文档 + 注释变更，`git revert` 即可。

## Open Questions

- 无需要 supersede 的 in-force ADR（`<repo>/adr/` 为空）。
- 是否需要在 `SKILL.md` 中新增对 `testing-evaluation.md` 的引用链接？倾向「是」，但留给 apply 阶段确认。
