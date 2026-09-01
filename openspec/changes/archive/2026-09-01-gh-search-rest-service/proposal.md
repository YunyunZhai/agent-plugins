## Why

gh-search 当前是 Claude Code 的 CLI 插件，通过 SKILL.md 让 LLM 编排多个 Python 脚本完成搜索。现在需要：
1. 暴露 REST API 给前端 UI 使用（带用户计费）
2. 重新组织目录结构，将原型代码按流程阶段分离
3. 保留全部现有功能（多后端嵌入、模型对比评估、索引维护）

## What Changes

- **新增 REST 服务层**（`service/`）：FastAPI 应用，单端点 `POST /api/v1/search`，通过参数控制搜索通道和管线步骤
- **重组目录结构**：将扁平的 15 个脚本按 5 个流程阶段分到子目录
- **抽取公共模块**：`github_client.py`、`sqlite_store.py`、`ark_client.py`、`logsetup.py` 移入 `_common/`
- **拆分 fetch_repos.py**：`sync_stars()` 函数抽出为独立的 `maintenance/sync_stars.py`
- **新增配置系统**：`config.yaml` 集中管理 GitHub token、嵌入后端、DB 路径等
- **新增计费模块**：`service/billing.py` 记录调用次数 + token 用量

## Capabilities

### New Capabilities

- `rest-search-api`: REST 搜索服务 — 单端点，参数控制通道（keyword/semantic/hybrid）和管线步骤（enrich/readme/rerank），返回结构化 JSON
- `billing`: 计费记录 — 每次搜索记录 user_id、调用次数、token 用量
- `config-management`: 配置管理 — config.yaml 加载，环境变量覆盖

### Modified Capabilities

（无现有 spec 需修改，openspec/specs/ 目录为空）

## Impact

- **代码重组**：15 个脚本从 `scripts/` 扁平结构迁移到 `scripts/{_common,data,pipeline,search,eval,maintenance}/`
- **import 路径变化**：所有脚本的 `from github_client import ...` 改为 `from scripts._common.github_client import ...`
- **新增依赖**：FastAPI、uvicorn、pydantic、pyyaml
- **保留兼容**：每个脚本仍可独立 CLI 运行（`python3 -m scripts.search.search_repos`）
- **SKILL.md 更新**：脚本路径引用需要同步更新
