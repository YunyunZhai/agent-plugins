# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

这是一个 Claude Code 技能/插件仓库，包含 `plugin-recommender` 技能。该技能使用 Pinecone 向量搜索从已安装的插件市场中推荐 Claude Code 插件。所有文档和 UI 字符串使用中文。

## 常用命令

### 依赖安装
```bash
pip install requests
```

### 数据同步（上传本地插件数据到 Pinecone）
```bash
PINECONE_API_KEY=<key> python3 .claude/skills/plugin-recommender/scripts/sync_to_pinecone.py
```

常用选项：
- `--dry-run`：预览模式，不实际上传
- `--marketplaces official`：只同步指定市场
- `--batch-size 50`：自定义批大小
- `--api-key <key>`：也可通过参数传递 API 密钥

### 状态检查
```bash
python3 .claude/skills/plugin-recommender/scripts/check_status.py
```

选项：`--local-only`（仅检查本地）、`--json`（JSON 输出）

## 架构

### 核心设计
- **SKILL.md** 是技能入口和工作流定义，采用声明式 agent 编程模式
- **两阶段流程**：数据导入（sync_to_pinecone.py）→ 查询推荐（SKILL.md 工作流）
- Python 脚本处理文件系统操作，Pinecone MCP 工具处理向量搜索

### 数据流
1. 数据源：`~/.claude/plugins/marketplaces/<market>/.claude-plugin/marketplace.json`
2. 过滤占位符/空描述插件后，构造嵌入文本：`"Plugin: {name}. Category: {category}. Author: {author}. Description: {description}"`
3. 批量 upsert 到 Pinecone 索引 `claude-plugins-recommender`（`llama-text-embed-v2` 嵌入模型）
4. 查询时使用 `cascading-search` MCP 工具 + `bge-reranker-v2-m3` 重排序

### 命名空间策略
每个市场对应一个 Pinecone 命名空间（如 `claude-plugins-official`、`karpathy-skills`）

## 开发注意事项

- 项目无构建步骤、无测试框架、无 lint 配置，Python 脚本直接运行
- Pinecone REST API 版本：`2025-04`
- 占位符过滤逻辑（`PLACEHOLDER_PATTERNS`）在 `sync_to_pinecone.py` 和 `check_status.py` 中重复定义
- 环境变量：`PINECONE_API_KEY`（必需）、`PINECONE_INDEX`（可选）、`PINECONE_HOST`（可选）
