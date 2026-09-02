# gh-search-docs Specification

## Purpose

TBD

## Requirements

### Requirement: 架构文档覆盖三层实现

`references/architecture.md` SHALL 准确描述 gh-search 的当前三层实现：关键词在线管线、语义索引管线、REST service，并 SHALL 覆盖共享层 `_common` 包、数据库 schema（含 `stars`、`readme_embed_text`、`repo_readme_vectors`）与当前有效的运维命令。

#### Scenario: 架构文档包含全部脚本与数据流

- **GIVEN** 当前 gh-search 代码包含 `search/*`、`data/*`、`pipeline/*`、`maintenance/*`、`_common/*`、`service/*` 等脚本与模块
- **WHEN** 阅读 `references/architecture.md`
- **THEN** 文档 SHALL 覆盖关键词管线、语义索引管线与 REST service 三层，并列出每个脚本的职责与数据流

#### Scenario: 运维命令与代码一致

- **GIVEN** 星数快照同步已从 `fetch_repos.py --sync-stars` 迁移到 `maintenance/sync_stars.py`
- **WHEN** 维护者按 `references/architecture.md` 的运维命令执行星数刷新
- **THEN** 文档 SHALL 使用 `maintenance/sync_stars.py` 而非已失效的 `fetch_repos.py --sync-stars`

### Requirement: 测试评测文档汇总既有验证结论

`references/testing-evaluation.md` SHALL 汇总此前测试验证情况，包括模型对比（bge-m3 vs doubao/llama）、嵌入性能基准（CPU/GPU/API）、搜索质量探针与已知未验证项，并 SHALL 为每项结论标注证据来源（文件路径或数据文件）。

#### Scenario: 测试文档可追溯到证据

- **GIVEN** 仓库中存在 `data/compare_report*.json`、`data/debug_*.json` 及各类 `*.log` 等验证产物
- **WHEN** 阅读 `references/testing-evaluation.md` 的任一条测试结论
- **THEN** 文档 SHALL 指明支撑该结论的证据文件路径或数据来源

#### Scenario: 测试文档标注已知未验证项

- **GIVEN** qwen3-rerank 仅验证了缺省降级路径、且无正式标注评测集
- **WHEN** 阅读 `references/testing-evaluation.md`
- **THEN** 文档 SHALL 显式列出这些已知未验证项，避免将定性结论误读为正式评测
