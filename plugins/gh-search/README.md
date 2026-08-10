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

| 步骤 | 名称 | 数据源 | 输出 |
|------|------|--------|------|
| Step 1 | GraphQL 原始召回 | GitHub GraphQL Search | 200-400 条 |
| Step 2 | 内存粗筛（元数据层，不读 README） | description + topics | 80-150 条 |
| Step 3 | 高阶成熟度指标过滤 | GraphQL 批量 + REST 贡献者 | 20-60 条 |
| Step 4 | 可选深度增强（默认关闭） | README 截断片段 | 20-60 条 + 片段 |

**关键设计**：
- 只用 `fork:false`，不用 `not:fork`（GraphQL 陷阱）
- description 为空**不丢弃**（很多正经项目不填描述）
- 贡献者数用 REST 精确计数（含匿名），避免年轻高产仓库被低估
- 30 天 commit 用 `history(since:)`（非 `until`），否则会统计全部 commit
- README 默认关闭，降低 token 与网络开销

## 依赖

唯一的运行时依赖是 **GitHub CLI（`gh`）**，需已认证：

```bash
# 安装见 https://cli.github.com/
gh auth login
```

无 Python 第三方依赖（纯标准库，通过 subprocess 调 `gh api`）。

## 脚本

```bash
# Step1+2：召回与粗筛
python3 skills/gh-search/scripts/search_repos.py \
  --query "网络安全 安全扫描" --language python --json

# Step3：成熟度指标过滤
python3 skills/gh-search/scripts/enrich_metrics.py \
  --input step2.json --json

# Step4：深度模式 README 片段
python3 skills/gh-search/scripts/fetch_readme.py \
  --input step3.json --json
```

## 配置

- **GitHub 认证**：复用 `gh` CLI 凭据（`~/.config/gh/hosts.yml`）
- **深度模式**：每次会话由 AskUserQuestion 询问是否开启
- **过滤阈值**：可通过脚本参数调整（`--min-stars`、`--min-contributors`、`--min-commits-30d` 等）
- **超时**：`GH_SEARCH_TIMEOUT` 环境变量（秒，默认 60）