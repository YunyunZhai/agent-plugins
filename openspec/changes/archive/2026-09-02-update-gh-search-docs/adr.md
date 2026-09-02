# ADR Review Manifest

- Status: completed
- Review date: 2026-09-01

## Review Summary

ADR review completed for this change.

## In-Force ADRs Reviewed

- None - `<repo>/adr/` has no in-force ADRs.

## New Durable ADRs Created

- None - no major durable architectural decisions were introduced.

## Review Notes

本次变更是纯文档对齐 + 两处注释级代码清理，不引入新的持久架构决策。design.md 中的 D1-D6 均为文档组织方式与注释修正的实现指导，属于本次变更内的操作性选择，不构成需要仓库级 ADR 长期追踪的架构承诺。

- 三层架构（关键词/语义/REST service）本身已在代码与既有归档变更中确定，本次仅补文档，非新决策。
- `_common` 包结构、DB schema（`stars`、`readme_embed_text`、`repo_readme_vectors`）均为已存在的实现事实，文档只是如实记录。
- 测试文档的「结论优先 + 证据链接」结构是文档风格选择，不影响系统架构与未来演进契约。
