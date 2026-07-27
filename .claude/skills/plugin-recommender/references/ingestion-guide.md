# Plugin Recommender - 数据导入指南

## 记录 Schema

每条插件记录包含以下字段：

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `_id` | string | 是 | 唯一标识：`{marketplace}::{plugin_name}` |
| `text` | string | 是 | 组合嵌入字段（fieldMap 目标） |
| `name` | string | 是 | 插件原始名称 |
| `category` | string | 是 | 插件分类（缺失时用 `uncategorized`） |
| `marketplace` | string | 是 | 来源市场名称 |
| `author` | string | 是 | 作者名称（缺失时用空字符串） |
| `homepage` | string | 是 | 插件主页 URL（缺失时用空字符串） |

### `text` 字段格式

```
Plugin: {name}. Category: {category}. Author: {author}. Description: {description}
```

示例：
```
Plugin: pinecone. Category: development. Author: Pinecone. Description: Pinecone is a vector database...
```

### `_id` 格式

`{marketplace-name}::{plugin-name}`

示例：`claude-plugins-official::pinecone`, `karpathy-skills::andrej-karpathy-skills`

如果 plugin_name 包含 `::`，替换为 `--`。

## 数据源路径

```
~/.claude/plugins/marketplaces/<market-name>/.claude-plugin/marketplace.json
```

当前市场：
- `claude-plugins-official`（273 个插件）
- `karpathy-skills`（1 个插件）

## marketplace.json 解析逻辑

### 顶层结构

```json
{
  "name": "marketplace-name",
  "plugins": [ ... ]
}
```

### 插件条目字段

**必需字段：**
- `name` (string) — 插件标识符
- `description` (string) — 插件描述
- `source` (string 或 object) — 源码位置（不用于嵌入，跳过）

**可选字段：**
- `author` (object with `name`) — 作者信息
- `category` (string) — 分类标签
- `homepage` (string) — 主页 URL
- `version` (string) — 版本号
- `tags` (array) — 标签
- `keywords` (array) — 关键词
- `displayName` (string) — 显示名称

### 缺失字段处理

| 字段 | 缺失时默认值 |
|------|-------------|
| `author` | `""` (空字符串) |
| `category` | `"uncategorized"` |
| `homepage` | `""` (空字符串) |
| `description` | 跳过该插件（不导入） |

### 无效插件过滤

跳过以下插件：
- `description` 为空或缺失
- `description` 为占位符文本（如 "TODO", "Coming soon"）

## 命名空间策略

| 命名空间 | 市场 | 预期记录数 |
|----------|------|-----------|
| `claude-plugins-official` | claude-plugins-official | ~273 |
| `karpathy-skills` | karpathy-skills | ~1 |

## 批量 Upsert 策略

使用 MCP `upsert-records` 工具：
- `name`: `claude-plugins-recommender`
- `namespace`: 目标市场命名空间
- `records`: 记录数组（每批 50 条）

274 条记录共需约 6 次 upsert 调用。

## 导入后验证

调用 `describe-index-stats` 检查记录数是否匹配。

## 索引配置

| 属性 | 值 |
|------|-----|
| 索引名 | `claude-plugins-recommender` |
| 云/区域 | `aws` / `us-east-1` |
| 嵌入模型 | `llama-text-embed-v2` |
| fieldMap | `{ "text": "text" }` |
