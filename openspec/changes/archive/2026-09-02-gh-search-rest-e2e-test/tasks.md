## 1. 测试基础设施

- [x] 1.1 创建 `gh-search/tests/` 目录与 `__init__.py`（如需 pytest 收集）
- [x] 1.2 编写 session 级 fixture：选择非冲突端口、设置测试环境变量、启动 uvicorn 子进程、等待 `/api/v1/health` 就绪、session 结束后终止进程
- [x] 1.3 在 fixture 中隔离计费库（使用独立 `X-User-Id`，必要时覆盖 billing db 路径避免污染生产 `data/billing.db`）

## 2. 接口测试用例

- [x] 2.1 健康检查：断言 `GET /api/v1/health` 返回 200 且 `status=ok`、`db_connected=true`
- [x] 2.2 关键词通道：断言 `POST /api/v1/search`（`channel=keyword`）返回 200 且含 `candidates_list`
- [x] 2.3 语义通道：dashscope 凭据齐全时断言 `channel=semantic` 返回 200 且含 `candidates_list`；凭据缺失时 `pytest.skip` 并记录原因
- [x] 2.4 并行通道：断言 `POST /api/v1/search`（`channel=hybrid`）返回 200 且含 `candidates_list`、`channel=hybrid`
- [x] 2.5 管线步骤：断言 `enrich=true,readme=true,rerank=true` 时 `pipeline_steps` 包含 `recall`/`enrich`/`readme`/`rerank`（rerank 缺密钥时允许降级并记录）
- [x] 2.6 默认管线：断言默认参数时 `pipeline_steps` 仅含召回步骤
- [x] 2.7 计费汇总：在搜索后请求 `/api/v1/billing/summary`（对应 `user_id` 与 `period`），断言 `total_calls > 0`
- [x] 2.8 参数校验：断言 `channel=invalid` 返回 422 且含校验错误信息

## 3. 执行并记录结果

- [x] 3.1 本地运行 `pytest gh-search/tests/test_rest_e2e.py -v`，确认通过/跳过状态符合预期
- [x] 3.2 撰写 `gh-search/references/e2e-test-report.md`：记录执行环境（gh 认证状态、DASHSCOPE 凭据是否配置、语义库路径）、运行命令、逐用例结果与关键产物（响应结构、计费落库、降级路径）

## 4. 文档更新

- [x] 4.1 在 `gh-search/README.md` 新增「测试」小节：测试依赖安装、运行命令
- [x] 4.2 在「测试」小节补充完整环境变量配置清单（`DASHSCOPE_API_KEY`/`DASHSCOPE_BASE_URL`/`DASHSCOPE_RERANK_URL`、`GH_SEARCH_BACKEND`/`GH_SEARCH_DB` 等，含必需条件、示例，并区分嵌入端点与 rerank 端点）

## 5. 校验

- [x] 5.1 运行 `openspec validate gh-search-rest-e2e-test --type change --strict`
