# agent-plugins

这是一个 Claude Code 技能/插件仓库，当前包含以下插件：

- `plugin-recommender`（插件推荐）
- `usage-stats`（本地用量统计状态栏）
- `gh-search`（GitHub 智能开源项目搜索）

## gh-search（GitHub 智能开源项目搜索）

根据用户语义检索意图，通过 4 步过滤管线（GraphQL 召回 → 元数据粗筛 → 成熟度指标过滤 → 可选 README 深度增强）从 GitHub 召回并精选高赞开源项目，由大模型打分推荐。

唯一的运行时依赖是已认证的 GitHub CLI（`gh`）。详见 `plugins/gh-search/README.md`。

## plugin-recommender（插件推荐）

## plugin-recommender（插件推荐）

该技能会把本地已安装插件市场的数据同步到 Pinecone 向量索引，然后根据用户自然语言查询做语义检索，再由 AI 从候选插件中选择最合适的推荐结果。

### 依赖安装

```bash
pip install pinecone
```

### 环境变量

- `PINECONE_API_KEY`：Pinecone API 密钥（必需）
- `PINECONE_INDEX`：索引名称（可选，默认 `claude-plugins-recommender`）

### 同步/刷新数据

```bash
python3 .claude/skills/plugin-recommender/scripts/sync_to_pinecone.py
```

只同步某个市场（示例：community）：

```bash
python3 .claude/skills/plugin-recommender/scripts/sync_to_pinecone.py --marketplaces community
```

### 状态检查

```bash
python3 .claude/skills/plugin-recommender/scripts/search_plugins.py status
python3 .claude/skills/plugin-recommender/scripts/check_status.py --json
```

### 查询

技能由 Claude agent 侧触发；你也可以直接用本地脚本做检索探测：

```bash
python3 .claude/skills/plugin-recommender/scripts/search_plugins.py search "security scanning vulnerability detection"
```

