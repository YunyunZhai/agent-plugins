## 1. 目录结构重组

- [x] 1.1 创建子目录：`scripts/_common/`、`scripts/data/`、`scripts/pipeline/`、`scripts/search/`、`scripts/eval/`、`scripts/maintenance/`，每个目录添加 `__init__.py`
- [x] 1.2 移动共享模块到 `_common/`：`github_client.py`、`sqlite_store.py`、`ark_client.py`、`logsetup.py`
- [x] 1.3 移动数据采集脚本到 `data/`：`fetch_repos.py`、`fetch_readmes.py`、`fetch_readmes_server.py`
- [x] 1.4 移动批量嵌入脚本到 `pipeline/`：`build_index.py`、`import_gpu_vectors.py`
- [x] 1.5 移动搜索脚本到 `search/`：`search_repos.py`、`semantic_search.py`、`hybrid_search.py`、`enrich_metrics.py`、`fetch_readme.py`、`rerank_results.py`
- [x] 1.6 移动评估脚本到 `eval/`：`compare_models.py`
- [x] 1.7 移动运维脚本到 `maintenance/`：`incremental_update.py`

## 2. sync_stars 拆分

- [x] 2.1 从 `fetch_repos.py` 中提取 `sync_stars()` 函数及相关逻辑到 `maintenance/sync_stars.py`
- [x] 2.2 更新 `fetch_repos.py` 的 `main()` 移除 `--sync-stars` 参数
- [x] 2.3 验证 `sync_stars.py` 可独立 CLI 运行

## 3. import 路径修复

- [x] 3.1 修复 `_common/` 模块间的交叉 import（`sqlite_store` → 无外部依赖，`github_client` → 无外部依赖）
- [x] 3.2 修复 `data/fetch_repos.py` 的 import：`from scripts._common.github_client import ...`
- [x] 3.3 修复 `data/fetch_readmes.py` 的 import
- [x] 3.4 修复 `pipeline/build_index.py` 的 import（依赖 `_common/sqlite_store`、`_common/ark_client`）
- [x] 3.5 修复 `pipeline/import_gpu_vectors.py` 的 import
- [x] 3.6 修复 `search/search_repos.py` 的 import（依赖 `_common/github_client`）
- [x] 3.7 修复 `search/semantic_search.py` 的 import（依赖 `_common/sqlite_store`、`_common/ark_client`）
- [x] 3.8 修复 `search/hybrid_search.py` 的 import（依赖 `search/search_repos`、`search/semantic_search`）
- [x] 3.9 修复 `search/enrich_metrics.py` 的 import
- [x] 3.10 修复 `search/fetch_readme.py` 的 import
- [x] 3.11 修复 `search/rerank_results.py` 的 import
- [x] 3.12 修复 `eval/compare_models.py` 的 import（依赖 `pipeline/build_index`、`_common/ark_client`）
- [x] 3.13 修复 `maintenance/incremental_update.py` 的 import（依赖 `data/fetch_repos`、`pipeline/build_index`）

## 4. CLI 兼容性验证

- [x] 4.1 验证 `python3 -m scripts.search.search_repos --query "test" --json` 可运行
- [x] 4.2 验证 `python3 -m scripts.search.semantic_search --query "test" --json` 可运行
- [x] 4.3 验证 `python3 -m scripts.search.hybrid_search --query "test" --json` 可运行
- [x] 4.4 验证 `python3 -m scripts.pipeline.build_index --dry-run` 可运行
- [x] 4.5 验证 `python3 -m scripts.maintenance.sync_stars --help` 可运行
- [x] 4.6 验证 `python3 -m scripts.eval.compare_models --help` 可运行

## 5. REST 服务实现

- [x] 5.1 创建 `service/__init__.py` 和 `service/config.py`（加载 config.yaml + 环境变量覆盖）
- [x] 5.2 创建 `service/models.py`（Pydantic 请求/响应模型：SearchRequest、SearchResponse、BillingSummary、HealthResponse）
- [x] 5.3 创建 `service/billing.py`（SQLite 计费记录：初始化表、写入记录、查询汇总）
- [x] 5.4 创建 `service/pipeline.py`（管线编排：根据 channel 参数调用 search/ 下的核心函数，串联 enrich/readme/rerank 步骤）
- [x] 5.5 创建 `service/main.py`（FastAPI 入口：`POST /api/v1/search`、`GET /api/v1/health`、`GET /api/v1/billing/summary`）
- [x] 5.6 创建 `config.yaml.example`（配置模板，不含真实 token）
- [x] 5.7 更新 `.gitignore` 排除 `config.yaml`、`data/*.db`、`data/*.log`

## 6. 文档更新

- [x] 6.1 更新 `SKILL.md` 中所有脚本路径引用（从 `scripts/xxx.py` 改为 `scripts/search/xxx.py` 等）
- [x] 6.2 更新 `README.md` 中的脚本用法示例和目录结构说明
- [x] 6.3 更新 `references/architecture.md` 中的文件清单

## 7. 端到端测试

- [x] 7.1 启动 FastAPI 服务，验证 `GET /api/v1/health` 返回 200
- [x] 7.2 发送 `POST /api/v1/search` keyword 通道请求，验证返回 candidates_list
- [x] 7.3 发送 `POST /api/v1/search` semantic 通道请求，验证返回 candidates_list
- [x] 7.4 发送 `POST /api/v1/search` hybrid 通道请求，验证返回 candidates_list
- [x] 7.5 验证计费记录正确写入 `data/billing.db`
- [x] 7.6 验证带 `enrich=true,rerank=true` 参数的完整管线执行
