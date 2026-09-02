## ADDED Requirements

### Requirement: README 提供用户使用说明

`gh-search/README.md` SHALL 提供面向最终用户的使用说明，包括触发方式、提问技巧、三通道选择建议、常见示例与 FAQ，并 SHALL 与面向开发者的章节清晰分隔。

#### Scenario: 用户能找到触发方式

- **GIVEN** 一个不熟悉 gh-search 的最终用户
- **WHEN** 阅读 `gh-search/README.md`
- **THEN** 文档 SHALL 列出可触发搜索的短语或场景

#### Scenario: 用户能学会提问技巧

- **GIVEN** 用户想要获得更精准的搜索结果
- **WHEN** 阅读用户使用说明章节
- **THEN** 文档 SHALL 说明如何在自然语言需求中表达目标语言、成熟度与领域关键词

#### Scenario: 用户能选择搜索通道

- **GIVEN** 系统支持关键词、语义、并行三种召回通道
- **WHEN** 阅读用户使用说明章节
- **THEN** 文档 SHALL 说明各通道的适用场景与选择建议

### Requirement: README 保留并完善开发者章节

`gh-search/README.md` SHALL 保留脚本、索引维护与配置等开发者内容，并 SHALL 补齐 `hybrid_search.py` 与 `rerank_results.py` 的命令示例。

#### Scenario: 开发者能找到全部脚本命令

- **GIVEN** 开发者需要手动运行 gh-search 脚本
- **WHEN** 阅读 `gh-search/README.md` 的脚本章节
- **THEN** 文档 SHALL 包含关键词、语义、并行、成熟度过滤、README 片段与 rerank 的命令示例

#### Scenario: 用户与开发者章节清晰分隔

- **GIVEN** README 同时面向最终用户与开发者
- **WHEN** 阅读文档结构
- **THEN** 用户使用说明与开发者内容 SHALL 分别归入不同章节，避免相互混杂

### Requirement: README 说明 REST 服务的启动与使用

`gh-search/README.md` SHALL 提供 REST 服务的启动方式、接口清单与使用示例，使开发者能通过 HTTP 接口调用搜索能力。

#### Scenario: 开发者能启动 REST 服务

- **GIVEN** 一个开发者准备使用 gh-search 的 REST 服务
- **WHEN** 阅读 `gh-search/README.md` 的 REST 服务章节
- **THEN** 文档 SHALL 说明依赖安装、配置准备与 `uvicorn service.main:app` 启动命令

#### Scenario: 开发者能调用 REST 接口

- **GIVEN** REST 服务已启动
- **WHEN** 阅读 REST 服务章节
- **THEN** 文档 SHALL 列出 `/api/v1/health`、`/api/v1/search`、`/api/v1/billing/summary` 三个端点，并 SHALL 给出 curl 使用示例与请求体字段说明
