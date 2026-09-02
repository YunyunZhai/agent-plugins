# gh-search

GitHub 智能开源项目搜索插件。根据用户的自然语言检索意图（如"找成熟活跃的 Python 网络安全开源库"），通过 **4 步过滤管线**从 GitHub 召回并精选高赞开源项目，最后由大模型打分推荐。

> 本文件同时面向**最终用户**（下面「用户使用说明」）与**开发者**（「工作原理」及其后章节）。技能行为细节以 `SKILL.md` 为准。

## 用户使用说明

### 触发方式

在对话中直接表达你的检索需求即可触发，例如：

- "找开源项目"
- "推荐一个开源库"
- "GitHub 搜索 XX"
- "找成熟活跃的 X 语言库"
- "找高赞的 XX 项目"
- "哪个开源项目适合做 XX"
- "找一个启动快、资源占用低的编码智能体"

### 提问技巧

描述需求时，尽量带上这几个维度，召回会更精准：

| 维度 | 例子 |
|------|------|
| 目标语言 | "Python 安全扫描库"、"Rust 高性能 HTTP 框架" |
| 成熟度/活跃度 | "成熟活跃"、"高赞"、"维护中" |
| 领域/特质关键词 | "低延迟"、"资源占用低"、"多端同步"、"WebDAV" |
| 使用场景 | "适合做笔记"、"适合内网部署" |

好的提问："找一个成熟活跃的 Go 语言网盘/文件管理项目，支持 WebDAV。"
较差的提问："有什么好项目？"（信息太少，只能泛泛推荐）

### 三种搜索通道

| 通道 | 适用场景 | 说明 |
|------|----------|------|
| 关键词 | 有明确仓库名/语言/关键词 | 实时打 GitHub，结果精确 |
| 语义 | 意图是模糊的"功能/特质"描述 | 本地向量索引，能召回"描述不含关键词但语义相关"的项目 |
| 并行 | 两者都不确定，或想都试 | 关键词 + 语义 union，兼顾实时性与语义 |

通道选择由技能根据你的意图自动决定；语义/并行通道依赖本地索引，索引未构建时自动回退关键词通道，不影响基本使用。

### 常见示例

- "找一个启动快、资源占用低的编码智能体" → 语义/并行通道，匹配特质描述
- "找成熟活跃的 Python 安全漏洞扫描库" → 关键词/并行，语言 + 领域 + 成熟度都明确
- "GitHub 上有没有 alist 的替代品" → 关键词，含明确仓库名

### FAQ 与已知限制

- **需要联网和 GitHub 授权吗？** 基础功能需要已认证的 GitHub CLI（`gh auth login`）；语义通道还需本地向量索引。
- **结果为什么有时不精准？** 语义通道对 `name/description/topics` 做向量匹配，描述太短或太宽的项目可能被 star 先验或关键词兜底；README 双通道只覆盖约 7%（stars≥2000）的仓库。
- **通道选择是自动的吗？** 是，技能根据意图自动决定，但你也可以明确要求"都试一下"。
- **深度模式是什么？** 可选拉取候选项目 README 片段做语义增强，默认关闭以省 token 与网络开销，每次会话会询问是否开启。

> 以上通道选择与限制为经验性说明，非正式评测结论，详见 `references/testing-evaluation.md`。

## 工作原理（4 步过滤管线）

全程在线模式，无本地全量数据集，只对不断缩小的候选子集做查询：

| 步骤 | 名称 | 数据源 | 过滤条件 | 输出 |
|------|------|--------|----------|------|
| Step 1 | GraphQL 原始召回 | GitHub GraphQL Search | 见下方 [Step 1 查询条件] | 200-400 条 |
| Step 2 | 内存粗筛（元数据层，不读 README） | description + topics | 见下方 [Step 2 硬过滤] | 80-150 条 |
| Step 3 | 高阶成熟度指标过滤 | 单次 GraphQL 批量查询 | 见下方 [Step 3 过滤] | 20-60 条 |
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

每条候选拉取 2 个指标：**近 30 天 commit 数**（`history(since:)`）、**合并 PR 总数**——单次 GraphQL 批量查询拿回全部（≤100 条仅 1 次网络调用）。然后过滤：

| 条件 | 默认值 | 说明 |
|------|--------|------|
| 活跃度双条件（满足其一即保留） | — | 避免误杀稳定低变更的成熟项目 |
| ├─ 近 30 天 commit ≥ `--min-commits-30d` | 3 | 持续活跃 |
| └─ 或最后一次推送在 180 天内 | — | 改动少但未过时的稳定项目 |

过滤后按 star 降序输出。

### Step 4 README 截断

默认抓取 README 开头 1200 字符 + 末尾 300 字符拼接（共 ≤2000 字符），供 LLM 做语义深度匹配。默认关闭以省 token 与网络开销。

**关键设计**：
- 只用 `fork:false`，不用 `not:fork`（GraphQL 陷阱）
- description 为空**不丢弃**（很多正经项目不填描述）
- Step2 初筛与最终排序由 subagent 执行，候选大列表不占主会话上下文
- 30 天 commit 用 `history(since:)`（非 `until`），否则会统计全部 commit
- README 默认关闭，降低 token 与网络开销

## 召回通道

技能支持**三通道召回**（SKILL.md Step 0 决策）：

| 通道 | 数据源 | 脚本 |
|------|--------|------|
| 1. 关键词 | GitHub GraphQL search（在线实时） | `search_repos.py` |
| 2. 语义 | 本地 sqlite-vec 索引（name/desc/topics 向量） | `semantic_search.py` |
| 3. 并行 | 通道1 + 通道2 union | `hybrid_search.py` |

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
pip install --user sentence-transformers  # local 后端（bge-m3）
# local 后端不需要 Pinecone；仅 pinecone 后端需要 PINECONE_API_KEY
```

## 脚本

```bash
# 通道1 关键词召回（先转写成 3~5 组关键词，--group 可重复）
python3 scripts/search/search_repos.py \
  --query "网络安全 安全扫描" \
  --group "security scanner" --group "vulnerability scan" --group "安全扫描" \
  --language python --json

# 通道2 语义召回（需已构建索引）
python3 scripts/search/semantic_search.py \
  --query "启动快的编码智能体" --top-k 50 --json

# 通道3 并行召回（关键词 + 语义 union）
python3 scripts/search/hybrid_search.py \
  --query "启动快的编码智能体" --top-k 50 --backend local --json

# Step3：成熟度指标过滤
python3 scripts/search/enrich_metrics.py \
  --input step2.json --json

# Step4：深度模式 README 片段
python3 scripts/search/fetch_readme.py \
  --input step3.json --json

# Step4.5：Rerank 精排（需 DASHSCOPE_API_KEY + DASHSCOPE_RERANK_URL）
python3 scripts/search/rerank_results.py \
  --input step3.json --query "启动快的编码智能体" --top-n 30 --json
```

## REST 服务

插件还提供一个 FastAPI 封装，把上面的脚本以 HTTP 接口暴露（无 LLM 环节，只跑确定性脚本）。

### 启动

```bash
# 1. 准备配置（可省略，环境变量可覆盖）
cp config.yaml.example config.yaml

# 2. 安装依赖
pip install fastapi uvicorn pyyaml pydantic sqlite-vec

# 3. 启动（在 gh-search/ 目录下）
uvicorn service.main:app --host 0.0.0.0 --port 8000
```

配置项见 `config.yaml.example`：`github.token`、`embedding.backend`/`db_path`、`server.host`/`port`、`billing.db_path`。也支持环境变量覆盖：`GH_TOKEN`、`GH_SEARCH_BACKEND`、`GH_SEARCH_DB`、`GH_SEARCH_TIMEOUT`、`GH_SEARCH_EMBED_DIM`。

### 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查：DB 连通性 + repo/vector 计数 |
| POST | `/api/v1/search` | 搜索：按 channel 执行管线，可选 enrich/readme/rerank |
| GET | `/api/v1/billing/summary` | 用量汇总：`user_id` + `period`(YYYY-MM) |

### 使用示例

```bash
# 关键词搜索
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -H "X-User-Id: alice" \
  -d '{"query": "python 安全扫描", "channel": "keyword"}'

# 语义搜索（依赖本地索引与 embedding.backend 配置）
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "启动快的编码智能体", "channel": "semantic", "top_k": 20}'

# 并行搜索 + 全流程（成熟度过滤 + README + rerank）
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "多端同步网盘", "channel": "hybrid", "enrich": true, "readme": true, "rerank": true}'

# 健康检查
curl http://localhost:8000/api/v1/health

# 用量查询
curl "http://localhost:8000/api/v1/billing/summary?user_id=alice&period=2026-09"
```

搜索请求体字段：`query`（必填）、`channel`（keyword/semantic/hybrid，默认 keyword）、`language`、`min_stars`（默认 200）、`top_k`（默认 50）、`star_weight`（默认 0.03）、`enrich`、`readme`、`rerank`。

## 索引维护（语义通道数据源）

```bash
# 首次全量抓取（stars:>100 约47万仓库, 后台数小时, 中断可 --resume）
python3 scripts/data/fetch_repos.py --stars-min 100

# 嵌入向量（本地 bge-m3, 当前生产路径）
python3 scripts/pipeline/build_index.py --backend local --db gh-search/data/gh_search_index_v3.db

# 星数快照刷新（排序先验数据源，每周一次，覆盖 ≥2000★）
python3 scripts/maintenance/sync_stars.py --db gh-search/data/gh_search_index_v3.db

# 每周增量：只插新仓库（近7天新活跃）
python3 scripts/maintenance/incremental_update.py --mode week --since 7 \
  --db gh-search/data/gh_search_index_v3.db

# 每月增量：变化检测重嵌（抓近30天活跃, 只对描述/topics变了的重嵌）
python3 scripts/maintenance/incremental_update.py --mode month --since 30 \
  --db gh-search/data/gh_search_index_v3.db
```

- 数据库：`gh-search/data/gh_search_index_v3.db`（`GH_SEARCH_DB` 可覆盖）
- 嵌入模型：`bge-m3` fp32 ONNX（1024 维，当前生产）；备选 `llama-text-embed-v2`（Pinecone 后端）
- **索引只存语义字段**（name/desc/topics/lang），不存 stars 等动态字段；`semantic_search` 会在线拉取最新 stars 过滤

## 配置

- **GitHub 认证**：复用 `gh` CLI 凭据（`~/.config/gh/hosts.yml`）
- **深度模式**：每次会话由 AskUserQuestion 询问是否开启
- **过滤阈值**：可通过脚本参数调整（`--min-stars`、`--min-commits-30d` 等）
- **超时**：`GH_SEARCH_TIMEOUT` 环境变量（秒，默认 60）
- **嵌入**：`--backend local`（默认生产路径，无需密钥）；`--backend pinecone` 需 `PINECONE_API_KEY`
