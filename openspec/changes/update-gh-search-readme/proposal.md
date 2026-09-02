## Why

`gh-search/README.md` 当前偏开发者视角（脚本调用、索引维护、配置），缺少面向最终用户的使用说明。用户不知道如何触发搜索、如何描述需求以获得更好结果，也不知道三通道的适用场景与常见限制。

## What Changes

- 更新 `gh-search/README.md`，同时面向**最终用户**与**开发者**，章节清晰分隔。
- 新增「用户使用说明」章节，包含：
  - 触发短语（现有内容整理）
  - 提问技巧：如何用自然语言描述需求（目标语言、成熟度、领域关键词）
  - 三通道（关键词/语义/并行）的适用场景与选择建议
  - 常见示例（含典型查询）
  - FAQ 与已知限制
- 保留并小幅整理现有的「工作原理」「脚本」「索引维护」「配置」等开发者章节，补上遗漏的 `hybrid_search.py` 与 `rerank_results.py` 命令示例。

## Capabilities

### New Capabilities

- `gh-search-user-guide`: `gh-search/README.md` 必须提供面向最终用户的使用说明（触发方式、提问技巧、通道选择、示例与 FAQ），并与面向开发者的章节清晰分隔。

### Modified Capabilities

<!-- 本次为文档变更，无 spec 级行为变化 -->

## Impact

- 文档：`gh-search/README.md`（更新并新增用户使用说明章节）
- 行为：无变化（不改脚本、不新增/修改任何运行时逻辑或 spec 契约）
