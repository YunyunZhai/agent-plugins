# gh-search 语义检索：当前实现方案

> 更新于 2026-08-24。本文描述**当前生产实现**，历史方案与踩坑细节见
> `embedding-engineering-notes.md`。

## 一图总览

```
【建库（一次性/每周增量）】
GitHub GraphQL ──fetch_repos.py──▶ sqlite repos 表
                                   （embed_text / stars 快照 / readme_embed_text 预留）
        │
        ▼
嵌入层（二选一）：
  · Kaggle 免费 T4，bge-m3 fp32   ~60 条/s，43万条约 2 小时   ← 当前采用
  · 本地 i5-7500，int8 ONNX       ~3.5 条/s                  ← 备用（离线增量可用）
        │
        ▼
sqlite-vec repo_vectors（1024 维，归一化）

【查询（semantic_search.py --backend local）】
用户意图 ──本地 fp32 ONNX──▶ 查询向量
  → 深窗口 kNN（k=4000，vec0 上限）
  → fork/archived 硬过滤
  → 混合排序：score = dist − 0.03·log10(1+stars快照)
  → 截断 top_k
  → 仅对 top_k 在线刷新实时 stars（展示用，失败回落快照值）
```

## 为什么是这套设计（关键决策及依据）

| 决策 | 依据 |
|------|------|
| 嵌入模型选 **bge-m3**（弃 doubao/Pinecone llama） | 跨语言概念桥接与豆包相当（对照实验），且无配额限制；llama 模型存在跨语言鸿沟（alist 全库排名 3000+ 的根因之一） |
| 语料在 **Kaggle T4 上以 fp32** 批量产出 | 本地 CPU 仅 3.5 条/s（34h），GPU 60 条/s（2h）；免费额度每周 30h 绰绰有余 |
| 查询端用 **fp32 ONNX**（非 int8） | 与 GPU fp32 语料数值同源（同文本向量余弦≈1.0）；int8 会引入 ~0.02 漂移，禁止混入在线链路 |
| 深窗口 k=4000 | 元数据稀疏的头部项目裸距离排名可达 1300+（alist 实测），浅窗口直接漏掉；vec0 全库暴力扫描，深堆零额外成本 |
| 混合排序 star 先验（λ=0.08） | 把"描述写得烂但实力强"的头部项目从千名外拉回前排（alist 1361→第1）；λ 取 0.08 使无关大热门不会压过真相关小项目 |
| 打分用 **stars 快照**、仅 top_k 在线刷新 | 深窗口下对数千候选逐个在线拉 star 需要 130+ GraphQL 批次（数分钟/次查询），不可行；快照允许周级陈旧 |

## 文件清单

| 文件 | 角色 |
|------|------|
| `scripts/build_index.py` | 批量嵌入入口。`--backend local/ark/pinecone`，`--shard i:n` 多进程分片 |
| `scripts/import_gpu_vectors.py` | Kaggle npz 回导 v3 库（校验维度/归一化/id 完整性，幂等 DELETE+INSERT） |
| `scripts/semantic_search.py` | 查询入口。`--backend local` + `--star-weight 0.08`(默认) + `--dual-query`/`--pure-semantic` 开关 |
| `scripts/fetch_repos.py` | 元数据抓取 + `--sync-stars` 星数快照同步（REST 自适应区间，30 req/min 限速内） |
| `scripts/sqlite_store.py` | schema 与 vec0 封装。EMBED_DIM 由 `GH_SEARCH_EMBED_DIM` 控制（v3 用默认 1024） |
| `scripts/ark_client.py` | 方舟 chat/embeddings 客户端（备用后端；强制 IPv4 绕 fake-ip 故障） |
| `references/colab_gpu_embedding.md` | Colab/Kaggle 手动操作手册 |
| `references/embedding-engineering-notes.md` | 全部实测数据与踩坑记录 |

## 数据库版本

| 库 | 模型 | 维度 | 状态 |
|----|------|------|------|
| **gh_search_index_v3.db** | bge-m3 fp32 (Kaggle T4) | 1024 | ✅ **当前生产库**（43万条全量） |
| gh_search_index_v2.db | doubao-embedding-vision | 2048 | 早期迁移遗留（~3700 条），仅存档 |
| gh_search_index.db | llama-text-embed-v2 | 1024 | 旧生产库，回退兜底 |

## 运维命令速查

```bash
# 查询（生产姿势）
GH_SEARCH_BACKEND=local GH_SEARCH_DB=<v3库> \
  python3 scripts/semantic_search.py --query "..." --top-k 15

# 星数快照刷新（每周一次即可，覆盖 ≥2000★，约 40 分钟受 GitHub 限速）
python3 scripts/fetch_repos.py --sync-stars --db <v3库>

# 增量补嵌新仓库（断点续传，自动跳过已有）
python3 scripts/build_index.py --backend local --db <v3库>

# 未来启用 README 双通道后：repo_readme_vectors 表已建，
# semantic_search 自动取双表最小距离，无需改动调用方式
```

## 已知限制与后续方向

1. README 双通道未落地：属性型查询（"readme 中有性能对比"类）仍依赖描述质量；
   jcode 类项目靠 star 先验救回而非语义命中。
2. 查询端模型加载有 ~5s 冷启动（fp32 ONNX），高频使用可加常驻进程。
3. OpenList 在 bge-m3 下裸距离跌出前 4000（豆包小池曾排第 4），靠 star 先验兜底；
   若未来不满意，可在 Kaggle 上对方舟 API 分片调用建豆包全库版做同题 A/B。
