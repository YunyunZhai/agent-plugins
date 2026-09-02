## Context

`gh-search/README.md` 现有内容以开发者视角为主（脚本命令、索引维护、配置），面向最终用户的内容只有触发短语，缺少「怎么提问」「选哪条通道」「常见限制」等使用说明。用户上手门槛高。

本次是纯文档变更，不涉及系统架构或行为，因此**不引入 C4 图**——README 面向用户与开发者，用章节分隔比架构图更直接。

## Goals / Non-Goals

**Goals:**
- 让最终用户读完 README 后知道：如何触发、如何描述需求、三通道怎么选、有哪些常见限制。
- 让开发者仍能快速找到脚本与索引维护命令。
- 保持单一 README（不拆新文档），用户与开发者章节清晰分隔。

**Non-Goals:**
- 不改动任何脚本、运行时逻辑、DB schema、REST 接口。
- 不新增独立 USAGE.md 或 docs 目录。
- 不重写 `architecture.md`、`testing-evaluation.md` 等 references 文档。

## Decisions

### D1 — 单一 README，顶部用户、底部开发者

README 结构重排为：

1. 简介 + 快速开始（用户）
2. 用户使用说明（触发、提问技巧、三通道选择、示例、FAQ/限制）
3. 工作原理（保留现有 4 步管线）
4. 脚本（开发者，补齐 hybrid/rerank）
5. 索引维护（开发者）
6. 配置（开发者）

理由：单一入口对最终用户和开发者都最省事；用 `## 用户使用说明` 与后续 `## 开发者指南` 分区即可，无需拆文件。

替代方案：拆 `USAGE.md` —— 被否决，README 是插件唯一对外的第一落点，拆出去反而难发现。

### D2 — 用户说明内容从现有 SKILL.md 与 README 提炼

触发短语、三通道、提问技巧、限制均来自 `SKILL.md` 与现有 README 中已确认的信息，不新增未经验证的说法。

理由：避免文档与实现/技能定义脱节；用户说明必须与 SKILL.md Step 0 决策一致。

### D3 — 补齐开发者命令缺口

README 现有「脚本」缺 `hybrid_search.py` 与 `rerank_results.py` 命令，本次补上；并保留已加入的 `sync_stars.py`。

理由：架构文档已记录这两个脚本，README 作为开发者入口不应缺它们。

### D4 — FAQ 标注「定性结论」边界

FAQ 中的通道选择建议、README 双通道覆盖率等属于定性/经验结论，标注「非正式评测」，与 `testing-evaluation.md` 口径一致。

## Risks / Trade-offs

- [文档与 SKILL.md 漂移] 用户说明一旦与 SKILL.md 行为不一致会误导 -> 用户说明内容以 SKILL.md Step 0 为准，README 顶部注明以 SKILL.md 为准。
- [README 变长] 同时面向用户和开发者会让文件变长 -> 用清晰章节与目录分隔，保留「工作原理」做折叠式概览。
- [通道选择建议过度承诺] -> FAQ 明确三通道适用场景是经验建议，并在已知限制中说明语义通道依赖本地索引、README 双通道覆盖有限。

## Migration Plan

无运行时迁移。落地步骤：

1. 重排 `gh-search/README.md` 结构并新增用户使用说明章节。
2. 补齐 `hybrid_search.py`、`rerank_results.py` 命令示例。
3. 运行 `openspec validate update-gh-search-readme --type change --strict`。

回滚：纯文档变更，`git revert` 即可。

## Open Questions

- 无需要 supersede 的 in-force ADR（`<repo>/adr/` 为空）。
- 是否需要同步更新 `SKILL.md` 以链接到 README 的用户说明章节？倾向「是」，但留给 apply 阶段确认。
