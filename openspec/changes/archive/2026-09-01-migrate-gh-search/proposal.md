## Why

gh-search 项目当前位于 `plugins/gh-search/` 目录下，需要迁移到 `agent-plugins/gh-search/` 子目录，以便更好地组织和管理。迁移将按照之前设计文档中确定的目录结构进行，确保代码的可维护性和一致性。

## What Changes

- **目录迁移**：将 `plugins/gh-search/` 的所有内容复制到 `agent-plugins/gh-search/` 子目录
- **保留原目录**：保留原始的 `plugins/gh-search/` 目录不变
- **结构重组**：按照设计文档中的结构组织代码：`service/`、`scripts/`、`data/`、`config.yaml`、`SKILL.md`
- **路径更新**：更新所有代码中的路径引用（import语句、配置文件路径等）
- **功能验证**：全面验证所有功能是否正常工作

## Capabilities

### New Capabilities

- `migration-gh-search`: 项目迁移 — 将gh-search项目从plugins目录迁移到根目录下的子目录，保持功能完整性和代码可维护性

### Modified Capabilities

（无现有spec需修改）

## Impact

- **目录结构变化**：在 `agent-plugins/gh-search/` 创建新的项目副本
- **原目录保留**：`plugins/gh-search/` 目录保持不变
- **路径引用更新**：新目录中的所有代码路径引用需要更新
- **功能验证需求**：需要全面验证新目录中所有功能是否正常工作
- **兼容性保持**：保持所有现有功能不变，包括CLI脚本、REST API、数据库连接等