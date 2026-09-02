## 1. 更新 README 结构与用户说明

- [x] 1.1 阅读 `gh-search/README.md` 与 `gh-search/SKILL.md`，核对触发短语、三通道与提问方式
- [x] 1.2 重排 `gh-search/README.md`：顶部简介 + 用户使用说明，底部开发者章节清晰分隔
- [x] 1.3 新增「用户使用说明」章节，包含触发方式、提问技巧、三通道选择、常见示例与 FAQ/限制

## 2. 补齐开发者命令

- [x] 2.1 在脚本章节补齐 `hybrid_search.py` 与 `rerank_results.py` 命令示例
- [x] 2.2 保留并核对 `sync_stars.py` 等现有索引维护命令
- [x] 2.3 新增「REST 服务」章节，说明启动方式、接口与 curl 使用示例

## 3. 验证

- [x] 3.1 运行 `openspec validate update-gh-search-readme --type change --strict` 确认 artifacts 与变更一致
- [x] 3.2 检查 README 中脚本名、命令、参数与 `gh-search/scripts/**/*.py` 一致
