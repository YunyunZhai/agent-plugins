# gh-search 测试与评测

> 更新于 2026-09-01。本文汇总此前的测试验证结论与证据来源。详细原始实验数据见
> `embedding-engineering-notes.md`。当前**没有**正式标注评测集，以下结论为定性/相对比较，非 nDCG/Recall@k 正式评测。

## 一、嵌入模型对比

### 结论

| 模型 | 维度 | 结论 | 证据 |
|------|------|------|------|
| llama-text-embed-v2 (Pinecone) | 1024 | ❌ 跨语言鸿沟，中文「聚合网盘」查不到英文 alist（全库排名 3000+/43万） | `embedding-engineering-notes.md` §一 |
| doubao-embedding-vision (方舟) | 2048 | ✅ 跨语言桥接，alist/OpenList 小池前五 | 同上 |
| BAAI/bge-m3 (本地 fp32) | 1024 | ✅ 与豆包质量相当，alist #3、jcode #5、bandit/walk #1 | 同上 |

**决策**：生产用 bge-m3 fp32，弃 llama（跨语言鸿沟）与 doubao（配额/成本），仅保留 doubao 作备用后端。

### A/B 重叠率（`scripts/eval/compare_models.py` + `data/compare_report*.json`）

- 中文 20 查询（`compare_report.json`）：BGE vs 豆包 top-20 平均交集 ≈ 4.6/20；豆包 4/20 查询失败（timeout/unknown error）。
- 英文 20 查询（`compare_report_en.json`）：平均交集 ≈ 7.65/20；无后端失败。
- 嵌入耗时：BGE ~0.13–0.30s/query；豆包 ~0.55–0.84s/query。

**解释**：top-20 重叠率低属预期——两个模型都在小池子（subset 库）上检索，背景竞争密度不足会放大排名差异；仅用于模型间相对比较，不能预测全库表现。

### 已知正样本命中（质量锚点）

| 正样本 | 查询 | bge-m3 结果 |
|--------|------|-------------|
| AlistGo/alist | 聚合网盘文件管理 | 裸距离 #15（距离 0.4428）；加 star 先验后 #1 |
| bandit/walk | Python 安全 | #1 |
| jcode | 编码智能体 | #5 |

证据：`embedding-engineering-notes.md` §一、`architecture.md` 决策表。

---

## 二、嵌入性能基准

### 本地 CPU（i5-7500, 4C/4T, 无 GPU/AVX-512/VNNI）

| 配置 | 吞吐 | 备注 |
|------|------|------|
| PyTorch fp32 | 6.0 条/s | 10 token 假文本 |
| ONNX fp32 | 10.7 条/s | 假文本 |
| ONNX int8 动态量化 | 14.8 条/s | CPU 无 VNNI，收益仅 ~40% |
| ONNX int8 **真实文本**（~50 tok） | **3.5 条/s 稳态** | ⚠️ 假文本基准高估约 4 倍 |
| OpenVINO fp32 | 2.4 条/s | 反而更慢，放弃 |

证据：`embedding-engineering-notes.md` §三。

### GPU（Kaggle T4）

- bge-m3 fp32 ≈ 60–76 条/s 稳态；43 万条 ≈ 2 小时（vs 本地 int8 3.5 条/s ≈ 34 小时）。
- 最终产出：432,586 向量（`embed_full.log` 显示 69,590 嵌入 / 累计 1,246,139 tokens / 总向量 432,586）。

证据：`scripts/embed_full.log`、`architecture.md`。

### 方舟（Ark）限速实测

- `HTTP 429 AccountRateLimitExceeded` 为账号级令牌桶，加进程无效（单账号稳态 1800~11700 TPM）；三账号 ≈ 三倍吞吐。
- Embeddings API 单请求上限 10 条。
- 全量 43 万条约 710 AFP（Small 月额度 20000 的 3.5%）。

证据：`embedding-engineering-notes.md` §四、`data/embed_ark*.log`。

### fp32 vs int8 精度一致性

- fp32 ↔ int8 同文本向量余弦：min 0.971 / mean 0.978，存在真实漂移（±3 名内噪声）。
- 规则：语料（Kaggle GPU fp32）↔ 查询（本地 fp32 ONNX `model.onnx`）同源；int8 禁止接入在线查询链路。

证据：`embedding-engineering-notes.md` §二。

---

## 三、搜索质量探针

### 混合排序（star 先验）

- 公式：`score = distance − λ·log10(1+stars快照)`，生产 λ=0.03。
- alist 从裸距离 #1361 → 混合排序 #1；λ=0.08 会放行 mega-list 挤掉真相关小项目，实测 0.03 兼顾召回与精度。
- `min_stars>0` 时先构造符合星数条件的临时向量子表，再执行 kNN，避免先召回低星仓库后丢弃。

证据：`architecture.md` 决策表、`semantic_search.py` 代码注释。

### README 双通道

- `repo_readme_vectors` 全量 30,378 条（stars≥2000），覆盖率约 7%。
- 查询自动合并 repo + readme 两表距离（同模型同空间取最小），无需开关。
- 属性型查询（"readme 中有性能对比"）依赖此通道；未覆盖仓库仍依赖描述质量。

证据：`data/semantic_search.log`（kNN 4000 → merge 7639，new_from_readme=3639）。

### 关键词通道 AND→OR 降级

- 关键词 ≤5 词分块；AND 优先，召回 <20 时同词 OR 兜底补池。
- 实测「免费聚合网盘开源项目」AND 返回 0，自动 OR 补池 → 合并 305 候选，命中 wzdnzd/aggregator (6753★)、cloudreve/cloudreve (28607★)。

证据：`data/search_repos.log`。

### hybrid 并行通道

- 实测「coding agent lightweight low resource fast startup」：关键词通道召回 0，语义通道召回 5，merge `keyword_only=0, semantic_only=5, both=0`。
- 语义冷启动耗时 ~30.8s（含模型加载），关键词 ~0.87s。

证据：`data/debug_hybrid_result.json`、`data/debug_hybrid.log`。

### star 快照同步

- 实测同步 32,890 条 star 快照，452 次 REST 调用（30 req/min 限速内）。

证据：`data/sync_stars.log`。

### README 抓取

- 目标 30,459 条（stars≥2000），~2.6–4.2s/条，ETA ~21–35h。

证据：`data/fetch_readmes.log`。

---

## 四、已知未验证项

| 项 | 状态 | 说明 |
|----|------|------|
| qwen3.7-text-rerank 精排 | ⚠️ 仅验证端点可达与优雅降级 | 实测 `qwen3.7-text-rerank` 在 `<workspace>/compatible-api/v1/reranks` 返回 200；但 rerank 数值基准仍未做，`_rerank_score` 排序质量待评测 |
| 正式标注评测集 | ❌ 无 | 无 query→gold-repo 标注集，无 nDCG/MRR/Recall@k |
| hybrid 大样本对比 | ⚠️ 未做 | 仅小探针验证（关键词 0 召回场景），无大样本 hybrid vs semantic 精度/召回对比 |
| dashscope 后端 | ⚠️ 未实测 | 代码已实现（qwen3.7-text-embedding），无对应生产日志 |
| billing token 计费 | ⚠️ 未填充 | `embedding_tokens`/`rerank_tokens` 恒为 0，`total_tokens` 恒 0 |

证据：`data/rerank_results.log`（4 行，跳过 rerank）、`service/billing.py`。

---

## 五、测试产物清单

| 类型 | 路径 | 用途 |
|------|------|------|
| 对比脚本 | `scripts/eval/compare_models.py` | BGE vs 豆包 A/B |
| 对比结果 | `data/compare_report.json` / `compare_report_en.json` | 中/英 20 查询 A/B 重叠率 |
| 搜索探针 | `data/debug_semantic_result.json` / `debug_hybrid_result.json` | semantic/hybrid 单次查询结果 |
| 日志 | `data/*.log`、`scripts/*.log` | 抓取/嵌入/查询/限速记录 |
| 子集库 | `data/gh_search_bge_subset.db` / `gh_search_doubao_subset.db` | 模型对比用的 subset 索引 |
