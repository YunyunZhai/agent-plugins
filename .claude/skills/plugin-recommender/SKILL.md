---
name: plugin-recommender
description: |
  从已安装的插件市场中根据用户查询推荐最合适的 Claude Code 插件。使用 Pinecone 向量搜索从所有市场中查找最相关的插件，然后由 AI 分析并推荐。当用户询问"有什么插件可以"、"推荐一个插件"、"帮我找插件"、"搜索插件"、"哪个插件适合"时触发。也用于"刷新插件"、"同步插件"重新导入市场数据。
allowed-tools: Bash, Read
argument-hint: query [你的查询]
---

# Plugin Recommender

根据用户的自然语言查询，从已安装的 Claude Code 插件市场中推荐最合适的插件。使用 Pinecone 语义搜索进行粗筛，然后由 AI 进行精选推荐。

## 前置检查

每次使用前，执行以下检查：

### 1. 检查 Pinecone 索引是否存在

调用 `list-indexes` MCP 工具，检查返回的索引列表中是否包含名为 `claude-plugins-recommender` 的索引。

- **如果索引不存在** → 转到「导入数据」流程
- **如果索引存在** → 继续下一步

### 2. 检查索引是否有数据

调用 `describe-index-stats` MCP 工具，参数 `name: "claude-plugins-recommender"`。

检查返回的 `namespaces` 中：
- `claude-plugins-official` 命名空间的记录数应 ≥ 270
- `karpathy-skills` 命名空间的记录数应 ≥ 1

- **如果记录数为 0 或不足** → 转到「导入数据」流程
- **如果记录数正常** → 继续下一步

### 3. 检查数据新鲜度

读取本地市场文件，计算实际插件数量：
- 读取 `~/.claude/plugins/marketplaces/claude-plugins-official/.claude-plugin/marketplace.json`
- 读取 `~/.claude/plugins/marketplaces/karpathy-skills/.claude-plugin/marketplace.json`
- 统计每个市场中 `description` 非空的插件数量（与导入规则一致）

将统计结果与索引中的记录数对比：
- 如果索引记录数与本地插件数差距 ≤ 10 → 数据基本同步，正常查询
- 如果索引记录数与本地插件数差距 > 10 → 告知用户：

```
⚠️ 插件市场数据可能已更新（索引有 {N} 条，本地有 {M} 条）。
建议先运行「刷新插件」获取最新数据，或直接查询（使用当前索引数据）。
```

等待用户决定是否先刷新再查询。

## 查询推荐流程

### 步骤 1：构造搜索查询

从用户的自然语言请求中提取核心意图，构造搜索查询文本。查询应包含：
- 用户想要的功能或技术领域
- 相关的关键词和概念

示例：
- 用户："帮我找一个安全扫描插件" → 查询："security scanning vulnerability detection"
- 用户："推荐代码质量相关插件" → 查询："code quality review linting static analysis"
- 用户："有什么插件可以帮我管理数据库" → 查询："database management SQL operations"

### 步骤 2：调用 Pinecone 搜索

**默认搜索（跨所有市场）：**

调用 `cascading-search` MCP 工具：
```json
{
  "indexes": [
    { "name": "claude-plugins-recommender", "namespace": "claude-plugins-official" },
    { "name": "claude-plugins-recommender", "namespace": "karpathy-skills" }
  ],
  "query": {
    "topK": 50,
    "inputs": { "text": "<构造的搜索查询>" }
  },
  "rerank": {
    "model": "bge-reranker-v2-m3",
    "rankFields": ["text"],
    "topN": 15
  }
}
```

**指定市场搜索：**

如果用户明确指定某个市场，调用 `search-records`：
```json
{
  "name": "claude-plugins-recommender",
  "namespace": "<市场名称>",
  "query": {
    "topK": 50,
    "inputs": { "text": "<构造的搜索查询>" }
  },
  "rerank": {
    "model": "bge-reranker-v2-m3",
    "rankFields": ["text"],
    "topN": 15
  }
}
```

**指定分类过滤：**

如果用户指定了分类（如"安全类插件"），添加过滤条件：
```json
{
  "query": {
    "topK": 50,
    "inputs": { "text": "<查询>" },
    "filter": { "category": { "$eq": "security" } }
  }
}
```

### 步骤 3：分析并推荐

从返回的 15 个重排序结果中，分析用户的具体需求，选择最合适的 3-5 个插件。考虑因素：
- 分类是否匹配用户需求
- 描述内容是否解决用户的具体问题
- 作者是否为官方/可信来源
- 插件的功能范围是否合适

### 步骤 4：格式化输出

以清晰的列表形式展示推荐结果：

```markdown
## 推荐插件

根据您的需求，推荐以下插件：

### 1. **{plugin_name}**
- **分类**: {category}
- **作者**: {author}
- **简介**: {description 截断到 200 字}
- **推荐理由**: {1-2 句话说明为什么适合用户的需求}
- **主页**: {homepage}

### 2. **{plugin_name}**
...
```

如果没有找到合适的插件，告知用户并建议：
- 使用更宽泛的查询词
- 检查是否有特定的分类需求
- 手动浏览插件市场

## 导入数据流程

当索引不存在或数据不足时执行。

### 步骤 1：创建索引

调用 `create-index-for-model` MCP 工具：
```json
{
  "name": "claude-plugins-recommender",
  "cloud": "aws",
  "region": "us-east-1",
  "embed": {
    "model": "llama-text-embed-v2",
    "fieldMap": { "text": "text" }
  }
}
```

等待索引创建完成（通常需要几秒）。调用 `describe-index-stats` 确认索引状态为 Ready。

### 步骤 2：读取市场数据

读取以下文件：
- `~/.claude/plugins/marketplaces/claude-plugins-official/.claude-plugin/marketplace.json`
- `~/.claude/plugins/marketplaces/karpathy-skills/.claude-plugin/marketplace.json`

详细解析逻辑参见 `references/ingestion-guide.md`。

### 步骤 3：构造记录

对每个市场中的每个插件，构造 Pinecone 记录：

```json
{
  "_id": "{marketplace}::{plugin_name}",
  "text": "Plugin: {name}. Category: {category}. Author: {author}. Description: {description}",
  "name": "{plugin_name}",
  "category": "{category}",
  "marketplace": "{marketplace}",
  "author": "{author_name}",
  "homepage": "{homepage}"
}
```

缺失字段处理：
- `author` 缺失 → 用空字符串 `""`
- `category` 缺失 → 用 `"uncategorized"`
- `homepage` 缺失 → 用空字符串 `""`
- `description` 缺失 → 跳过该插件

### 步骤 4：批量 Upsert

对每个市场，将记录按每批 50 条分组，调用 `upsert-records`：

```json
{
  "name": "claude-plugins-recommender",
  "namespace": "<marketplace-name>",
  "records": [ ... 50 records ... ]
}
```

处理顺序：
1. `claude-plugins-official`（~273 条，约 6 批）
2. `karpathy-skills`（1 条，1 批）

### 步骤 5：验证

调用 `describe-index-stats` 确认：
- `claude-plugins-official` 命名空间记录数 ≥ 270
- `karpathy-skills` 命名空间记录数 ≥ 1

验证通过后，告知用户数据导入完成，可以开始查询。

## 刷新数据流程

当用户说"刷新插件"、"同步插件"、"更新插件索引"时执行。

刷新流程与导入流程相同：
1. 读取最新的 marketplace.json 文件
2. 使用相同的 `_id` 格式，upsert 会自动更新已有记录
3. 新插件会被插入，已移除的插件会保留在索引中（不影响搜索质量）

刷新完成后，告知用户更新了多少条记录。

## 错误处理

| 错误场景 | 处理方式 |
|----------|---------|
| 索引不存在 | 执行导入数据流程 |
| MCP 工具不可用 | 提示用户安装 Pinecone 插件并设置 `PINECONE_API_KEY` |
| 市场文件不存在 | 提示用户运行市场更新命令获取最新数据 |
| 搜索无结果 | 建议使用更宽泛的查询词，或移除分类过滤 |
| upsert 失败 | 记录错误信息，继续处理下一批 |
| 索引创建失败 | 检查 Pinecone API 配额，提示用户 |
