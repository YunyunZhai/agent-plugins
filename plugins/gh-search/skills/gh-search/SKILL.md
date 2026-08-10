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
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/github_client.py
```

- 输出"已认证用户: <name>" → 继续
- 报错（未安装 / 未认证）→ 通过 **AskUserQuestion** 引导：告知需要安装 GitHub CLI 并运行 `gh auth login`（或选择退出）

### 询问【深度语义匹配】开关

每次会话运行时，用 **AskUserQuestion** 询问用户是否开启深度模式：

- **开启（深度）** → 在 Step 4 拉取并截断 README，做语义增强
- **关闭（简易）** → 跳过 Step 4，仅输出元数据

## 4 步过滤管线

### Step 1 — GraphQL 原始召回

调用脚本，传入用户意图、语言（如能推断）、star 阈值：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search_repos.py \
  --query "<用户检索意图>" \
  --language <language> \
  --min-stars 200 \
  --max-recalls 400 \
  --json
```

**说明**：
- 查询串使用 `fork:false`（**不是** `not:fork`，后者在 GraphQL 会静默返回 0 结果）
- 活跃窗口动态计算：`pushed:>=<6个月前>`，过滤僵尸仓库
- 召回 200-400 条，含 `nameWithOwner, description, topics, stars, forks, pushedAt, createdAt, license`

**Step1 输出**：原始召回列表（存在大量语义不匹配、description 为空的项目属正常）。

### Step 2 — 内存粗筛（元数据层，不读 README）

`search_repos.py` 已自动完成硬过滤（丢弃 fork/archived/超6个月未推送），并**保留 description 为空的项目**。

**你的任务**：对返回的候选做语义初筛——
- 优先看 topics 关键词是否匹配用户领域
- 对 description 非空者，轻量判断描述是否匹配用户意图
- **description 为空的项目直接保留**，不丢弃（很多正经项目不填描述）

**Step2 输出**：裁剪到约 80-150 条候选。

### Step 3 — 高阶成熟度指标过滤（仅对小集合调用）

对 Step 2 的小集合调用富化脚本，批量获取成熟度指标并过滤：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/enrich_metrics.py \
  --input <step2.json> \
  --min-contributors 8 \
  --min-commits-30d 3 \
  --json
```

**获取指标**（GraphQL 批量 + REST 贡献者）：
- 独立贡献者总数（REST `/contributors`，含匿名）
- 近 30 天 commit 数（`history(since:)` — 注意不是 `until`）
- 累计合并 PR 数（`pullRequests(states: MERGED)`）

**过滤条件**（可配置，默认）：
1. 独立贡献者 ≥ 8（过滤单人维护的玩具项目）
2. 活跃度双条件（满足其一即保留，避免误杀稳定低变更的成熟项目）：
   - 近 30 天 commit ≥ 3；**或**
   - 最后推送在 6 个月内

**Step3 输出**：20-60 条高质量候选。到此为止**没有拉取任何 README**。

### Step 4 — 可选增强：深度模式

仅当用户开启【深度语义匹配】开关时执行：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_readme.py \
  --input <step3.json> \
  --max-chars 2000 \
  --json
```

批量拉取 Step 3 小集合的 README 并**截断**（不拿全文）。简易模式直接跳过此步。

## 交给大模型的输入与最终输出

### LLM 输入

用户原始查询意图 + Step 3（+Step 4 可选 README 片段）的候选项目数组。每条项目携带：
`repo 名称、star、fork、topics、description、createdAt、pushedAt、contributors、近30天commit、合并PR、【可选 README 片段】`

### 最终输出（给用户）

1. **筛选过滤**：剔除语义不匹配、玩具 Demo 项目；区分【成熟高置信项目】/【潜力新项目】两组
2. **每个项目**：简短能力说明、成熟度风险提示（单人维护、版本状态等）
3. **按匹配度 & 社区权威度排序**
4. **对比摘要**（如资源、适用场景）

## 关键约束（务必遵守）

1. GitHub Search API **不能在查询侧直接过滤 contributors、PR**；只能召回后对小集合二次查询。
2. description 大量为空，**不能作为丢弃条件**。
3. 不把 commit>X 作为硬必选条件，保护改动少的稳定成熟库。
4. README 作为可选增强，默认关闭，降低 token、网络开销。

## 错误处理

- `gh` 未认证 → 引导 `gh auth login`，不崩溃
- 单仓库无权限/不存在 → 跳过该仓库，继续处理其余
- 网络抖动 → 脚本内置重试；仍失败则提示用户重试
- 详见 `references/error-handling.md`