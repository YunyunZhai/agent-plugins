# Plugin Recommender

从已安装的 Claude Code 插件市场中，根据自然语言查询推荐最合适的插件。

## 触发短语

以下场景会触发插件推荐技能：

- "有什么插件可以..."
- "推荐一个插件"
- "帮我找插件"
- "搜索插件"
- "哪个插件适合..."
- "找安全扫描插件"
- "代码审查插件推荐"

也可用于"刷新插件"、"同步插件"重新导入市场数据。

## 工作原理

1. **数据同步**：`scripts/sync_to_pinecone.py` 将本地市场插件数据上传到 Pinecone 向量索引
2. **语义搜索**：`scripts/search_plugins.py` 通过 Pinecone 语义搜索 + BGE 重排序粗筛候选
3. **AI 精选**：由 Claude 分析候选结果，精选 3-5 个最匹配的插件推荐给用户

## 依赖

```bash
pip install pinecone
```

## 使用方法

在 Claude Code 中直接提问即可触发：

- "有什么插件可以做代码审查？"
- "推荐一个安全扫描插件"
- "帮我找数据库管理的插件"

也支持手动操作：

```bash
# 同步数据到 Pinecone
PINECONE_API_KEY=<key> python3 scripts/sync_to_pinecone.py

# 搜索插件
PINECONE_API_KEY=<key> python3 scripts/search_plugins.py search "代码审查"

# 检查状态
PINECONE_API_KEY=<key> python3 scripts/check_status.py
```

## 配置

需要在 `~/.claude/settings.json` 中设置 `PINECONE_API_KEY` 环境变量，或首次使用时按提示交互配置。
