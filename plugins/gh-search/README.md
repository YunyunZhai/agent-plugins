# gh-search

GitHub 智能开源项目搜索插件。根据用户的语义检索意图（如"找成熟活跃的 Python 网络安全开源库"），通过 **4 步过滤管线**从 GitHub 召回并精选高赞开源项目，最后由大模型打分推荐。

## 触发短语

以下场景会触发 gh-search 技能：

- "找开源项目"
- "推荐一个开源库"
- "GitHub 搜索 XX"
- "找成熟活跃的 X 语言库"
- "找高赞的 XX 项目"
- "哪个开源项目适合做 XX"
- "找一个启动快、资源占用低的编码智能体"

## 工作原理（4 步过滤管线）

全程在线模式，无本地全量数据集，只对不断缩小的候选子集做查询：

| 步骤 | 名称 | 数据源 | 过滤条件 | 输出 |
|------|------|--------|----------|------|
| Step 1 | GraphQL 原始召回 | GitHub GraphQL Search | 见下方 [Step 1 查询条件] | 200-400 条 |
| Step 2 | 内存粗筛（元数据层，不读 README） | description + topics | 见下方 [Step 2 硬过滤] | 80-150 条 |
| Step 3 | 高阶成熟度指标过滤 | GraphQL 批量 + REST 贡献者 | 见下方 [Step 3 过滤] | 20-60 条 |
| Step 4 | 可选深度增强（默认关闭） | README 截断片段 | 拼接开头 1200 字 + 末尾 300 字（共 ≤2000） | 20-60 条 + 片段 |

### Step 1 查询条件（构建到 GraphQL search query 中）

```text
is:public fork:false archived:false stars:>200 pushed:>=<最近180天>  [language:<目标语言>]
```

- `stars:>200`：默认最小 star 阈值，可用 `--min-stars` 调整
- `pushed:>=<180 天前>`：只召回最近 6 个月内推送过的活跃仓库
- `language:<语言>`：仅当检索意图能推断出语言时追加
- 用 `fork:false` 而非 `not:fork`（GraphQL 陷阱，后者静默返回 0）

### Step 2 硬过滤（内存中，逐条丢弃）

| 条件 | 说明 |
|------|------|
| 丢弃 fork | `isFork == true` |
| 丢弃 archived | `isArchived == true` |
| 丢弃僵尸仓库 | 超过默认 180 天（`--stale-days`）未推送 |
| **保留** description 为空 | 不丢弃！很多正经项目不填描述，语义匹配交给 LLM |

### Step 3 过滤（成熟度指标，默认阈值）

每条候选拉取 3 个指标：**贡献者数**（REST 精确计数，含匿名）、**近 30 天 commit 数**（`history(since:)`）、**合并 PR 总数**。然后过滤：

| 条件 | 默认值 | 说明 |
|------|--------|------|
| 独立贡献者 ≥ `--min-contributors` | 8 | 过滤单人维护项目 |
| 活跃度双条件（满足其一即保留） | — | 避免误杀稳定低变更的成熟项目 |
| ├─ 近 30 天 commit ≥ `--min-commits-30d` | 3 | 持续活跃 |
| └─ 或最后一次推送在 180 天内 | — | 改动少但未过时的稳定项目 |

过滤后按 star 降序输出。

### Step 4 README 截断

默认抓取 README 开头 1200 字符 + 末尾 300 字符拼接（共 ≤2000 字符），供 LLM 做语义深度匹配。默认关闭以省 token 与网络开销。

**关键设计**：
- 只用 `fork:false`，不用 `not:fork`（GraphQL 陷阱）
- description 为空**不丢弃**（很多正经项目不填描述）
- 贡献者数用 REST 精确计数（含匿名），避免年轻高产仓库被低估
- 30 天 commit 用 `history(since:)`（非 `until`），否则会统计全部 commit
- README 默认关闭，降低 token 与网络开销

## 召回通道

技能支持**三通道召回**（SKILL.md Step 0 决策）：

| 通道 | 数据源 | 脚本 |
|------|--------|------|
| 1. 关键词 | GitHub GraphQL search（在线实时） | `search_repos.py` |
| 2. 语义 | 本地 sqlite-vec 索引（name/desc/topics 向量） | `semantic_search.py` |
| 3. 并行 | 通道1 + 通道2 union | 两者各跑后合并 |

通道2/3 依赖本地索引（见"索引维护"）。索引未构建时自动回退通道1。

## 依赖

基础运行时依赖是 **GitHub CLI（`gh`）**，需已认证：

```bash
# 安装见 https://cli.github.com/
gh auth login
```

**语义通道额外依赖**（可选，仅通道2/3需要）：

```bash
pip install --user sqlite-vec      # 本地向量库
# Pinecone SDK 已含于 plugin-recommender 环境; 嵌入需 PINECONE_API_KEY
```

## 脚本

```bash
# 通道1 关键词召回
python3 skills/gh-search/scripts/search_repos.py \
  --query "网络安全 安全扫描" --language python --json

# 通道2 语义召回（需已构建索引）
python3 skills/gh-search/scripts/semantic_search.py \
  --query "启动快的编码智能体" --top-k 50 --json

# Step3：成熟度指标过滤
python3 skills/gh-search/scripts/enrich_metrics.py \
  --input step2.json --json

# Step4：深度模式 README 片段
python3 skills/gh-search/scripts/fetch_readme.py \
  --input step3.json --json
```

## 索引维护（语义通道数据源）

```bash
# 首次全量抓取（stars:>100 约47万仓库, 后台数小时, 中断可 --resume）
python3 skills/gh-search/scripts/fetch_repos.py --stars-min 100

# 嵌入向量（Pinecone 托管嵌入, 按 token 计费, 断点续传）
PINECONE_API_KEY=<key> python3 skills/gh-search/scripts/build_index.py

# 每周增量：只插新仓库（近7天新活跃）
PINECONE_API_KEY=<key> python3 skills/gh-search/scripts/incremental_update.py --mode week --since 7

# 每月增量：变化检测重嵌（抓近30天活跃, 只对描述/topics变了的重嵌）
PINECONE_API_KEY=<key> python3 skills/gh-search/scripts/incremental_update.py --mode month --since 30
```

- 数据库：`plugins/gh-search/data/gh_search_index.db`（`GH_SEARCH_DB` 可覆盖）
- 嵌入模型：`llama-text-embed-v2`（1024 维）
- **索引只存语义字段**（name/desc/topics/lang），不存 stars 等动态字段；`semantic_search` 会在线拉取最新 stars 过滤

## 配置

- **GitHub 认证**：复用 `gh` CLI 凭据（`~/.config/gh/hosts.yml`）
- **深度模式**：每次会话由 AskUserQuestion 询问是否开启
- **过滤阈值**：可通过脚本参数调整（`--min-stars`、`--min-contributors`、`--min-commits-30d` 等）
- **超时**：`GH_SEARCH_TIMEOUT` 环境变量（秒，默认 60）
- **嵌入**：`PINECONE_API_KEY`（必填）+ `PINECONE_MODEL`（可选，默认 `llama-text-embed-v2`）