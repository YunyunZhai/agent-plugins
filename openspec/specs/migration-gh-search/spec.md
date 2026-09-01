# migration-gh-search Specification

## Purpose

将 gh-search 项目从 `plugins/gh-search/` 迁移到 `gh-search/` 子目录，并保持功能完整性、代码可维护性与原有外部接口不变。

## Requirements

### Requirement: 项目迁移
系统 SHALL 将gh-search项目从 `plugins/gh-search/` 目录迁移到 `agent-plugins/gh-search/` 子目录，同时保留原始目录不变。

#### Scenario: 目录复制
- **GIVEN** 原始项目位于 `plugins/gh-search/` 目录
- **WHEN** 执行迁移操作
- **THEN** 系统将所有文件和目录复制到 `agent-plugins/gh-search/` 子目录

#### Scenario: 保留原目录
- **GIVEN** 原始项目位于 `plugins/gh-search/` 目录
- **WHEN** 迁移操作完成
- **THEN** 原始 `plugins/gh-search/` 目录保持不变

### Requirement: 结构重组
系统 SHALL 按照设计文档中的结构组织迁移后的代码：`service/`、`scripts/`、`data/`、`config.yaml`、`SKILL.md`。

#### Scenario: 目录结构创建
- **GIVEN** 需要按照设计文档重组代码结构
- **WHEN** 执行结构重组
- **THEN** 系统创建 `service/`、`scripts/`、`data/` 目录，并移动相应文件

#### Scenario: 配置文件组织
- **GIVEN** 项目包含配置文件 `config.yaml`
- **WHEN** 执行结构重组
- **THEN** 配置文件放置在项目根目录

### Requirement: 路径更新
系统 SHALL 更新所有代码中的路径引用，包括import语句、配置文件路径等，确保在新位置能正常工作。

#### Scenario: Import语句更新
- **GITHUB** 代码中包含相对于旧目录的import语句
- **WHEN** 执行路径更新
- **THEN** 所有import语句更新为相对于新目录的路径

#### Scenario: 配置路径更新
- **GIVEN** 配置文件中包含数据库文件路径等引用
- **WHEN** 执行路径更新
- **THEN** 所有路径引用更新为相对于新目录的路径

### Requirement: 功能验证
系统 SHALL 全面验证所有功能是否正常工作，包括CLI脚本、REST API、数据库连接等。

#### Scenario: CLI脚本验证
- **GIVEN** 迁移后的项目包含CLI脚本
- **WHEN** 执行功能验证
- **THEN** 所有CLI脚本能够独立正常运行

#### Scenario: REST API验证
- **GIVEN** 迁移后的项目包含REST API服务
- **WHEN** 执行功能验证
- **THEN** REST API服务能够正常启动和响应请求

#### Scenario: 数据库连接验证
- **GIVEN** 迁移后的项目包含数据库文件
- **WHEN** 执行功能验证
- **THEN** 数据库连接正常，数据可正常读写
