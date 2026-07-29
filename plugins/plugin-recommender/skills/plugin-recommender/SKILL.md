---
name: plugin-recommender
description: |
  从已安装的插件市场中根据用户查询推荐最合适的 Claude Code 插件。使用 Pinecone 向量搜索从所有市场中查找最相关的插件，然后由 AI 分析并推荐。当用户询问"有什么插件可以"、"推荐一个插件"、"帮我找插件"、"搜索插件"、"哪个插件适合"时触发。也用于"刷新插件"、"同步插件"重新导入市场数据。
allowed-tools: Bash, Read
argument-hint: query [你的查询]
---

# Plugin Recommender

根据用户的自然语言查询，从已安装的 Claude Code 插件市场中推荐最合适的插件。使用本地 Python 脚本通过 Pinecone Python SDK 做语义搜索粗筛，然后由 AI 进行精选推荐。

## 前置检查

每次使用前，执行以下检查：

### 0. 检查 API Key 是否已配置

在调用任何 Pinecone 相关脚本之前，先检查 `PINECONE_API_KEY` 是否已设置：

如果未设置 → 通过 **AskUserQuestion** 弹窗交互，优先向用户展示以下配置信息：

```
📋 需要配置 Pinecone API Key
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
用途:      Pinecone 向量搜索，检索匹配的插件
索引名称:  claude-plugins-recommender
目标命名空间:
  claude-plugins-official  — Claude Code 官方插件
  claude-community         — 社区插件
  ecc                      — ECC 插件
  karpathy-skills          — Karpathy 技能
  mattpocock               — Matt Pocock 技能
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

提供两个选项：

**选项 A — 输入 API Key**
- 用户输入 Pinecone API Key
- 调用 Edit 工具将 `"PINECONE_API_KEY": "<用户输入的key>"` 写入 `~/.claude/settings.json` 的 `env` 字段中
- 告知用户：配置已保存到 `~/.claude/settings.json`，后续新会话会自动加载

**选项 B — 退出技能**
- 告知用户：Plugin Recommender 需要配置 `PINECONE_API_KEY` 才能使用
- 结束流程，不再执行后续任何步骤

如果 API Key 已设置 → 继续后续步骤。

### 1. 检查 Pinecone 索引状态

运行检查脚本获取索引状态：
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search_plugins.py status
```

解析 JSON 输出，向用户展示状态摘要：

```
📊 Pinecone 索引状态
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
索引名称: <index_name>
索引状态: ✅ 已创建 / ❌ 不存在
就绪状态: ✅ 数据充足 / ⚠ 数据不足

命名空间:
  claude-plugins-official: <N> 条记录
  karpathy-skills:         <N> 条记录
  ...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**情况 A — 索引正常且数据充足**
- `index_exists: true` 且 `ready: true` → 索引就绪，继续下一步

**情况 B — 索引异常（不存在 / 数据不足）**
- `index_exists: false` 或 `ready: false` → 展示状态摘要后，通过 **AskUserQuestion** 让用户决策：

**选项 A — 执行同步**
- 告知用户将执行的操作：
  - 索引不存在 → 创建索引 `claude-plugins-recommender`（model: `llama-text-embed-v2`，region: `aws/us-east-1`）
  - 上传各命名空间的插件数据（展示各市场预期记录数）
  - 同步完成后继续查询
- 确认后转到「导入/刷新数据流程」

**选项 B — 取消**
- 告知用户：无法查询，需要索引就绪才能使用
- 结束流程

### 2. 检查数据新鲜度

调用状态检查脚本：
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_status.py --json
```

解析 JSON 输出，比较索引记录数与本地插件数：

**情况 A — 数据基本同步（差值 ≤ 10）**
- 说明数据基本同步，继续下一步

**情况 B — 数据可能过期（差值 > 10）**
- 向用户展示对比数据：

```
⚠️ 插件市场数据可能已更新
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
索引记录数:  <N> 条
本地插件数:  <M> 条
差值:        <diff> 条
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

- 通过 **AskUserQuestion** 让用户决定：

**选项 A — 先同步再查询**
- 执行同步流程，完成后继续查询

**选项 B — 直接使用当前数据查询**
- 跳过同步，直接进入查询推荐流程

### 3. 检查通过 — 状态摘要与操作指引

当前置检查全部通过时，在进入查询前向用户展示一段简短的状态摘要：

```
✅ 插件推荐器准备就绪
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
索引:      claude-plugins-recommender ✓
命名空间:  <N> 个（共 <M> 条记录）
状态:      数据已同步
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

简要告知用户：

如需手动同步/增量更新，可以随时使用：
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sync_to_pinecone.py
```
同步是增量的——仅上传新增或更新的插件，自动清理已移除的插件，已有数据不受影响。

如需仅同步特定市场：
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sync_to_pinecone.py --marketplaces official
```

然后继续进入「查询推荐流程」。

## 查询推荐流程

### 步骤 1：构造搜索查询

从用户的自然语言请求中提取核心意图，构造搜索查询文本。使用用户原文（中文）作为查询词，嵌入模型支持多语言，无需翻译成英文。可适当补充相关的关键词增强召回。

示例：
- 用户："帮我找一个安全扫描插件" → 查询："安全扫描 漏洞检测 安全审计"
- 用户："推荐代码质量相关插件" → 查询："代码质量 代码审查 linting 静态分析"
- 用户："有什么插件可以帮我管理数据库" → 查询："数据库管理 SQL 数据库操作"

### 步骤 2：调用 Pinecone 搜索

**默认搜索（跨所有市场）：**

调用本地搜索脚本：
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search_plugins.py search "<构造的搜索查询>"
```

**指定市场搜索：**

如果用户明确指定某个市场，传入 `--namespace`：
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search_plugins.py search "<构造的搜索查询>" --namespace <市场名称>
```

**指定分类过滤：**

如果用户指定了分类（如"安全类插件"），添加过滤条件：
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search_plugins.py search "<查询>" --filter category=security
```

脚本会默认搜索所有已知命名空间，按相关度合并排序，并返回最多 15 个候选结果。

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

## 导入/刷新数据流程

当索引不存在、数据不足、或用户说"刷新插件"、"同步插件"时，执行脚本完成数据同步。

### 步骤 1：检查当前状态

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_status.py
```

### 步骤 2：确认同步计划

根据检查结果，向用户展示将要执行的操作摘要：

```
📋 同步计划
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
索引名称:  claude-plugins-recommender
操作:      [创建索引（如不存在）] → [清理过期记录] → [上传数据]

涉及命名空间（预期记录数）:
  claude-plugins-official  — <N> 条
  claude-community         — <N> 条
  ecc                      — <N> 条
  karpathy-skills          — <N> 条
  mattpocock               — <N> 条
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

通过 **AskUserQuestion** 让用户确认：

**选项 A — 执行同步**
- API Key 已在环境中，直接执行同步：
  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sync_to_pinecone.py
  ```
- 同步完成后继续「步骤 3」

**选项 B — 先预览变更**
- 使用 `--dry-run` 预览将要上传的数据：
  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sync_to_pinecone.py --dry-run
  ```
- 预览后再次询问是否执行同步

**选项 C — 取消**
- 告知用户：已取消同步，不会对索引做任何修改
- 结束流程

常用选项说明（同步或预览均可使用）：
- `--dry-run`：预览模式，不实际上传
- `--marketplaces official`：只同步指定市场
- `--batch-size 50`：自定义批大小

### 步骤 3：确认结果

同步完成后，脚本会自动输出各命名空间的验证结果。也可再次运行 `check_status.py` 确认。

## 错误处理

| 错误场景 | 处理方式 |
|----------|---------|
| 索引不存在 | 脚本会自动创建索引 |
| `PINECONE_API_KEY` 未设置 | 弹窗交互：用户输入 Key 后自动写入 `~/.claude/settings.json`，或取消退出 |
| 市场文件不存在 | 提示用户运行市场更新命令获取最新数据 |
| 搜索无结果 | 建议使用更宽泛的查询词，或移除分类过滤 |
| upsert 失败 | 脚本会记录错误并继续处理下一批 |
| 索引创建失败 | 检查 Pinecone API 配额，提示用户 |
