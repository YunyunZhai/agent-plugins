## Context

gh-search 是一个 GitHub 开源项目智能搜索系统，当前以 Claude Code CLI 插件形式运行。核心是一个 5 阶段数据管线：

```
Phase 1: 数据采集 → Phase 2: 批量嵌入 → Phase 3: 效果评估 → Phase 4: 在线查询 → Phase 5: 运维
```

**当前状态**：15 个 Python 脚本扁平放在 `scripts/` 下，LLM 通过 SKILL.md 编排执行。语义通道依赖本地 sqlite-vec 索引（43 万仓库 + 3 万 README 向量，qwen3.7 1024 维）。

**约束**：
- 个人使用，暂不高并发
- 保留 `gh` CLI 认证方式
- 保留全部现有脚本和数据库（含 BGE/豆包/llama/qwen 多版本库）
- 嵌入后端支持多模型（local/pinecone/ark/dashscope），生产用 local

**利益相关者**：前端 UI（计费用户）、未来 AI Agent（MCP 接口，暂不实现）

## Goals / Non-Goals

**Goals:**

- 提供 REST API 给前端 UI 搜索使用，带用户计费
- 按流程阶段重组目录结构，消除扁平混乱
- 每个脚本仍可独立 CLI 运行
- 配置集中管理（config.yaml + 环境变量覆盖）

**Non-Goals:**

- 不实现 MCP 接口（后续再加）
- 不做高并发优化（个人使用）
- 不删除任何现有脚本或数据库
- 不重写核心搜索逻辑（复用现有 Python 模块）

## Decisions

### D1: 目录结构按流程阶段分组

**选择**：将 15 个脚本从 `scripts/` 扁平结构迁移到 6 个子目录

```
scripts/
├── _common/         共享基础设施（github_client, sqlite_store, ark_client, logsetup）
├── data/            Phase 1 数据采集（fetch_repos, fetch_readmes, fetch_readmes_server）
├── pipeline/        Phase 2 批量嵌入（build_index, import_gpu_vectors）
├── search/          Phase 4 在线查询（search_repos, semantic_search, hybrid_search, enrich_metrics, fetch_readme, rerank_results）
├── eval/            Phase 3 效果评估（compare_models）
└── maintenance/     Phase 5 运维（incremental_update, sync_stars）
```

**备选方案**：
- A) 按技术分组（api/、db/、ml/）→ 否定：与用户思维模型不匹配
- B) 保持扁平 → 否定：15 个文件已难以导航

**理由**：用户描述的流程是"登录→抓数据→嵌入→对比→搜索→运维"，目录结构直接映射这个心智模型。

### D2: REST 服务与 scripts 平级

**选择**：`service/` 目录与 `scripts/` 平级，而非嵌入 scripts 内部

```
plugins/gh-search/
├── service/          REST 服务（FastAPI）
├── scripts/          CLI 脚本链
├── data/             数据库 + 日志
├── config.yaml       运行时配置
└── SKILL.md          Claude Code 技能
```

**理由**：service 是新增的 HTTP 层，scripts 是已有的 CLI 工具链，两者是不同接口层，平级更清晰。service 调用 scripts 的核心函数，不复制逻辑。

### D3: import 路径使用包导入

**选择**：所有脚本使用 `from scripts._common.xxx import ...` 形式的包导入

**备选方案**：
- A) sys.path.insert 相对导入 → 已有做法，但 REST 服务调用时路径脆弱
- B) 安装为 Python 包（pip install -e .）→ 过度工程，个人项目不需要

**理由**：包导入在两种场景下都稳定工作：CLI（`python3 -m scripts.search.search_repos`）和 FastAPI 服务。

### D4: sync_stars 从 fetch_repos.py 拆出

**选择**：将 `sync_stars()` 函数从 fetch_repos.py 抽出为独立的 `maintenance/sync_stars.py`

**理由**：sync_stars 是独立运维操作（每周刷新 star 快照），与全量抓取的分片算法逻辑无关。拆出后 fetch_repos.py 从 487 行减少到 ~380 行，sync_stars.py ~100 行。

### D5: 配置系统

**选择**：config.yaml + 环境变量覆盖

```yaml
github:
  token: ""                    # 留空则从 gh CLI 获取
  timeout: 60

embedding:
  backend: local               # local / pinecone / ark / dashscope
  db_path: data/gh_search_qwen.db

server:
  host: "0.0.0.0"
  port: 8000

billing:
  db_path: data/billing.db
```

环境变量 `GH_SEARCH_BACKEND`、`GH_SEARCH_DB` 等覆盖 config.yaml 值。

**理由**：YAML 可读性好，环境变量便于容器化部署和 CI 覆盖。

### D6: 计费存储使用独立 SQLite

**选择**：计费记录存入 `data/billing.db`，与搜索数据库分离

**理由**：计 millis 是追加写入的时序数据，与搜索的读密集型负载模式不同。分离后可以独立备份/清理，不影响搜索性能。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| import 路径变化导致现有 CLI 脚本 break | 每个脚本保留 `__main__.py` 或 `if __name__ == "__main__"` 入口，用 `python3 -m scripts.search.xxx` 执行 |
| SKILL.md 中的脚本路径引用失效 | 重构后同步更新 SKILL.md 所有路径 |
| 数据库文件搬迁丢失 | data/ 目录不动，只更新代码中的路径引用 |
| config.yaml 泄露 GitHub token | .gitignore 排除 config.yaml，提供 config.yaml.example 模板 |
| 多后端嵌入代码维护负担 | 保留全部后端代码，不做删除，REST 服务默认走 local |

## Migration Plan

1. **创建新目录结构**：mkdir 所有子目录，添加 `__init__.py`
2. **移动脚本**：按阶段将脚本从 `scripts/` 迁移到子目录
3. **修复 import**：所有脚本的 import 路径更新为 `scripts._common.xxx`
4. **拆分 sync_stars**：从 fetch_repos.py 抽出为独立文件
5. **创建 service/**：FastAPI 入口 + pipeline 编排 + 计费 + 配置
6. **创建 config.yaml**：提供 config.yaml.example 模板
7. **更新 SKILL.md**：所有脚本路径引用同步更新
8. **测试**：每个脚本 CLI 独立运行验证 + REST API 端到端测试
9. **更新 .gitignore**：排除 config.yaml、data/*.db、data/*.log

## Open Questions

- 无现有 ADR 需要 supersede
- 前端 UI 的认证方式（是否需要 REST 服务自身的 API key 鉴权）待后续确认
