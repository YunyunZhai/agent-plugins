---
name: gh-search
description: |
  当用户询问"找开源项目"、"推荐开源库"、"GitHub 搜索"、"找成熟活跃的 X 库"、"找高赞的 X 项目"、"哪个开源项目适合"、"找启动快的编码智能体"、"找 Python 安全扫描库"等开源项目检索与推荐问题时触发。通过 4 步过滤管线（GraphQL 召回 → 元数据粗筛 → 成熟度指标过滤 → 可选 README 深度增强）从 GitHub 召回并精选高赞开源项目，交给大模型打分推荐。
---

# GitHub 智能开源项目搜索

根据用户的自然语言检索意图，从 GitHub 召回并精选最匹配的高赞开源项目。核心是 **4 步过滤管线**（在线模式，无本地全量数据集），全程只对不断缩小的候选子集做查询，控制 token 与网络开销。

## 前置检查

开始前，检查 `gh` CLI 是否可用并已认证：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/_common/github_client.py
```

- 输出"已认证用户: <name>" → 继续
- 报错（未安装 / 未认证）→ 通过 **AskUserQuestion** 引导：告知需要安装 GitHub CLI 并运行 `gh auth login`（或选择退出）

### 询问【深度语义匹配】开关

每次会话运行时，用 **AskUserQuestion** 询问用户是否开启深度模式：

- **开启（深度）** → 在 Step 4 拉取并截断 README，做语义增强
- **关闭（简易）** → 跳过 Step 4，仅输出元数据

## 4 步过滤管线

### 召回通道选择（Step 0）

技能提供三种召回通道，由检索意图与本地索引状态决定：

| 通道 | 数据源 | 适用 | 脚本 |
|------|--------|------|------|
| **1. 关键词** | GitHub GraphQL search | 默认、数据实时 | `search_repos.py` |
| **2. 语义** | 本地 sqlite-vec 索引（全量抓取 + 嵌入） | 意图是"语义描述"（如"启动快的编码智能体"）| `semantic_search.py` |
| **3. 并行** | 通道1 + 通道2 union | 兼顾实时性与语义 | 两者各跑后合并 |

**决策规则**：
- 若意图含明确的仓库名/语言/关键词 → **通道1**（关键词精确）
- 若意图是模糊的"功能/特质"描述（非关键词）→ **通道2**（语义）
- 若两者都不确定 / 用户要求"都试" → **通道3**（并行合并，按语义相关性加权排序）
- 通道2/3 依赖**本地索引已构建**（见下方"索引维护"）；索引缺失时自动回退通道1

### Step 1 — 原始召回

**意图转写（你负责）**：脚本不做任何 LLM 调用。先把用户意图改写成 **3~5 组关键词**再传入：
- 每组 ≤4 个词、表达一个**具体**语义切面；英文组为主（GitHub 检索英文友好），中文意图补 1~2 组中文词
- 用可重复的 `--group` 传多组；`--query` 仍传原始意图（用于结果元数据）

**⚠ 关键词组必须具体，禁止万金油词组**——「open source solution」「self hosted tool」「free software」「developer tools」「machine learning」「web application」等几乎匹配所有开源项目，会引入大量噪音。每组词必须能区分「这个项目是 X」和「这个项目不是 X」。

**反面示例（禁止）**：
- `open source solution` — 几乎所有开源项目都匹配
- `self hosted tool` — 自托管工具 ≠ 网盘聚合
- `free software aggregator` — "free"+"aggregator" 太宽

**正面示例（好的转写）**：
- 意图「编程智能体开源项目，低延迟高性能」→
  `coding agent terminal rust`、`llm code generation fast`、`编程智能体 低延迟`、`高性能 代码生成`
- 意图「免费聚合网盘开源项目」→
  `multi cloud storage aggregator`、`self hosted file sync golang`、`rclone alternative multi drive`、`聚合网盘 多端同步`

**脚本内置行为**（无需干预；stderr 的 `[variant]` 行逐组输出召回统计）：
- 每组先按 **AND**（全部词命中，精准小池）搜索；命中 <20 条自动同词降级 **OR** 补池
- 多组结果合并去重后输出；**无相关性排序**（初筛与排序是你的职责，见文末）

**说明**：
- 查询串使用 `fork:false`（**不是** `not:fork`，后者在 GraphQL 会静默返回 0 结果）
- 活跃窗口动态计算：`pushed:>=<6个月前>`，过滤僵尸仓库
- 输出含 `nameWithOwner, description, topics, stars, forks, pushedAt, createdAt, license`

**Step1 输出**：无序候选池（存在大量语义不匹配、description 为空的项目属正常）。

**通道2 语义召回**（若选定）：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search/semantic_search.py \
  --query "<用户检索意图>" \
  --top-k 50 \
  --min-stars 100 \
  --json
```

**说明**：
- 语义通道对 `name/description/topics` 做向量匹配，能召回"描述不含关键词但语义相关"的项目
- 输出 `candidates_list` 结构与关键词通道一致，便于通道3 union
- 按混合分排序（语义距离 − 0.03·log10(1+stars快照)），兼顾语义相关性与项目成熟度；`--pure-semantic` 可回退纯距离排序

### Step 2 — 语义初筛（subagent 执行，保护主会话上下文）

`search_repos.py` 已自动完成硬过滤（丢弃 fork/archived/超6个月未推送），并**保留 description 为空的项目**。

候选池可能有数百条，直读会占满主会话上下文——用 **Task 子代理**执行初筛，大列表不回流主会话：

- **子代理 prompt 必须自包含**（它没有会话历史）：用户意图原文、Step1 的关键词组、step1.json 路径、输出路径
- **任务**：读 step1.json，按意图裁剪——优先看 topics 是否匹配用户领域；description 非空者轻量判断语义相关性；**description 为空直接保留**
- **输出契约**：裁剪集写成 step2.json（`candidates_list` 结构不变），只向主会话返回一行统计（如「283→96 条，剔除爬虫/CTF/教程类噪音」）

**Step2 输出**：约 80-150 条候选。

### Step 3 — 高阶成熟度指标过滤（仅对小集合调用）

对 Step 2 的小集合调用富化脚本，批量获取成熟度指标并过滤：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search/enrich_metrics.py \
  --input <step2.json> \
  --min-commits-30d 3 \
  --json
```

**获取指标**（单次 GraphQL 批量查询拿回全部；≤100 条仅 1 次网络调用）：
- 近 30 天 commit 数（`history(since:)` — 注意不是 `until`）
- 累计合并 PR 数（`pullRequests(states: MERGED)`）

**过滤条件**（可配置）：活跃度双条件（满足其一即保留，避免误杀稳定低变更的成熟项目）：
- 近 30 天 commit ≥ 3；**或**
- 最后推送在 6 个月内

单人维护的玩具项目由 star 阈值与最终排序把关。

**Step3 输出**：20-60 条高质量候选。到此为止**没有拉取任何 README**。

### Step 4 — 可选增强：深度模式

仅当用户开启【深度语义匹配】开关时执行：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search/fetch_readme.py \
  --input <step3.json> \
  --max-chars 2000 \
  --json
```

批量拉取 Step 3 小集合的 README 并**截断**（不拿全文）。简易模式直接跳过此步。

### Step 4.5 — Rerank 精排（可选，需 DASHSCOPE_API_KEY + DASHSCOPE_RERANK_URL）

对 Step 3 或 Step 4 的候选集调用百炼 `qwen3.7-text-rerank` 模型，按 query-document 相关性做精细化二次排序：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search/rerank_results.py \
  --input <step3.json 或 step4.json> \
  --query "<用户检索意图>" \
  --top-n 30 \
  --json
```

- 需要环境变量 `DASHSCOPE_API_KEY` + `DASHSCOPE_RERANK_URL`（格式 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com`）
- 失败时自动降级到原始顺序，不中断流程
- 输出附加 `_rerank_score` 字段（0-1，越高越相关），按分数降序
- `--top-n` 控制输出条数（默认 50），LLM 拿到更精简的候选集

## 交给大模型的输入与最终输出

### LLM 输入

用户原始查询意图 + Step 3/4.5 的候选项目数组。每条项目携带：
`repo 名称、star、fork、topics、description、createdAt、pushedAt、近30天commit、合并PR、【可选 README 片段】、【可选 _rerank_score】`

若 Step 4.5 已执行，候选已按 rerank 分数排序，LLM 可优先使用 `_rerank_score` 作为排序参考。

### 最终排序与输出（subagent 执行，主会话只收 Top-N 文案）

排序数据体量大（富化指标 + 可选 README 片段），同样派 **Task 子代理**完成：

- **prompt 自包含**：用户意图原文、step3.json（及深度模式的 readme 增强文件 / rerank 结果）路径
- **任务**：
  1. 剔除语义不匹配、玩具 Demo 项目；区分【成熟高置信项目】/【潜力新项目】两组
  2. 每个项目一句话能力说明 + 成熟度风险提示（单人维护、版本状态等）
  3. 若有 `_rerank_score`，优先按此分数排序；否则按匹配度 & 社区权威度排序；给出对比摘要（如资源、适用场景）
- **输出契约**：完整排序写入 final.json 备查；向主会话只返回 Top-10 推荐文案（直接展示给用户）

## 关键约束（务必遵守）

1. GitHub Search API **不能在查询侧直接过滤 contributors、PR**；只能召回后对小集合二次查询。
2. description 大量为空，**不能作为丢弃条件**。
3. 不把 commit>X 作为硬必选条件，保护改动少的稳定成熟库。
4. README 作为可选增强，默认关闭，降低 token、网络开销。
5. 脚本保持确定性 CLI、零 LLM 调用：意图转写（Step1 前）、语义初筛（Step2）、
   相关性排序（最终输出）均由大模型完成；reranker API（Step 4.5）不算 LLM 调用，
   用于预排序减轻 LLM 负担；Step2 与最终排序通过 subagent 执行，候选大列表不进入主会话上下文。

## 错误处理

- `gh` 未认证 → 引导 `gh auth login`，不崩溃
- 单仓库无权限/不存在 → 跳过该仓库，继续处理其余
- 网络抖动 → 脚本内置重试；仍失败则提示用户重试
- 语义通道需本地索引（qwen3.7, `--backend dashscope`，需 DASHSCOPE_API_KEY）；索引缺失时自动回退关键词通道
- 详见 `references/error-handling.md`

## 索引维护（语义通道的数据源）

语义通道依赖本地 sqlite-vec 索引。**当前生产库为 `gh_search_qwen.db`**
（qwen3.7-text-embedding，1024 维，43 万仓库全量 + 3 万仓库 README 双通道，
百炼 Batch 批量产出）。架构细节与决策依据见 `references/architecture.md`。

### 环境变量

| 变量 | 用途 | 必需场景 |
|------|------|---------|
| `DASHSCOPE_API_KEY` | 百炼 API Key | 语义通道（`--backend dashscope`）、Rerank |
| `DASHSCOPE_BASE_URL` | 百炼嵌入 API 端点 | 语义通道 `--backend dashscope` |
| `DASHSCOPE_RERANK_URL` | 百炼 rerank API 端点（格式 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com`） | Step 4.5 Rerank |
| `GH_SEARCH_BACKEND` | 查询嵌入后端（`local` / `dashscope` / `pinecone` / `ark`） | 语义通道 |
| `GH_SEARCH_DB` | sqlite 库路径 | 语义通道 |

```bash
# 查询（生产姿势；后端必须与库的模型一致）
# 需 DASHSCOPE_API_KEY + DASHSCOPE_BASE_URL
GH_SEARCH_BACKEND=dashscope GH_SEARCH_DB=<插件data目录>/gh_search_qwen.db \
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search/semantic_search.py --query "..." --top-k 15

# 全量重建（百炼 Batch，见 /tmp 管线说明；先导文件→提交→下载→回导）
# 增量补嵌新仓库用 build_index.py --backend dashscope --db <qwen库>（需脚本侧 Batch 支持）

# 星数快照刷新（混合排序的先验数据源，覆盖 ≥2000★，每周一次，约 40 分钟）
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/data/fetch_repos.py --sync-stars --db <v3库>

# 增量补嵌新仓库（断点续传，自动跳过已有）
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/pipeline/build_index.py --backend local --db <v3库>

# 全量重建走 Kaggle GPU（60 条/s）：导出→T4 嵌入→回导，
# 见 references/colab_gpu_embedding.md 与 scripts/pipeline/import_gpu_vectors.py

# 每周增量抓取近 7 天新活跃仓库
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/maintenance/incremental_update.py --mode week --since 7 --db <v3库>
```

排序机制：`score = 语义距离 − 0.03·log10(1+stars快照)`——深窗口 k=4000 召回 +
star 先验救回元数据稀疏的头部项目（alist 实测从全库第 1361 名升至第 1）；
`--pure-semantic` 回退纯距离排序。

<details>
<summary>历史/备用后端（legacy）</summary>

| 库文件 | 模型 | 维度 | 查询后端 |
|--------|------|------|----------|
| gh_search_qwen.db | qwen3.7-text-embedding (百炼) | 1024 | `--backend dashscope` |
| gh_search_index_v3.db | bge-m3 (本地) | 1024 | `--backend local` |
| gh_search_index.db | llama-text-embed-v2 (Pinecone) | 1024 | `--backend pinecone` |
| gh_search_index_v2.db | doubao-embedding-vision (方舟) | 2048 | `--backend ark` + `GH_SEARCH_EMBED_DIM=2048` |

```bash
# Pinecone 后端（多账号轮换：PINECONE_EMBED_KEY=key1,key2）
PINECONE_API_KEY=<key> python3 ${CLAUDE_PLUGIN_ROOT}/scripts/pipeline/build_index.py
# 方舟后端（--shard i:n 多 key 分片并行；注意 plan/coding 端点不同）
ARK_API_KEY=<key> ARK_BASE_URL=<套餐端点> GH_SEARCH_EMBED_DIM=2048 \
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/pipeline/build_index.py --backend ark --shard 0:3
```
</details>

- **README 双通道（已启用）**：`repo_readme_vectors` 已入库 30,378 条（stars≥2000 仓库全量，qwen3.7 Batch 嵌入），检索时自动按 id 取双表最小距离，无需开关
- **依赖**：本地后端需 `pip install sqlite-vec sentence-transformers onnxruntime`；方舟/Pinecone 各需对应 SDK 与密钥
- 索引未构建时，SKILL 自动使用**通道1关键词**，不影响基本功能