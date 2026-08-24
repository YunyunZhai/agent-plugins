# 嵌入引擎迁移：事实结论与踩坑记录（2026-08-24）

本文记录 gh-search 语义索引从 Pinecone llama-text-embed-v2 迁移到本地 bge-m3 过程中的
全部实测数据与教训。所有结论均有当日日志/实验佐证，非推测。

## 一、嵌入模型质量对比（同一组对照实验）

测试集：随机 200~1000 仓库 + 注入已知正例（alist/OpenList/jcode 等），观察正例全局排名。

| 模型 | 维度 | 关键发现 |
|------|------|----------|
| llama-text-embed-v2 (Pinecone) | 1024 | ❌ 跨语言概念鸿沟：中文「聚合网盘」查不到自述为英文 "file list/WebDAV multiple storages" 的 alist（距离排名 3000+/43万）；jcode 同样埋没 |
| doubao-embedding-vision-251215 (方舟) | 2048 | ✅ 原生桥接跨语言概念，alist/OpenList 进入前五（小池对照）|
| BAAI/bge-m3 (本地 int8/fp32) | 1024 | ✅ 与豆包质量相当：alist 第3、jcode 第5、bandit/walk 第1 |

**核心教训**：
- 元数据稀疏的头部项目（官方描述不含用户领域词）会被关键词堆砌的长尾淹没，
  这是召回层缺陷，rerank 救不了（候选集里根本没有）。
- 换强多语言模型能解决"概念表述差异"，但**属性型查询**（如"readme 中有性能对比数据"）
  的信号只在 README 里，必须靠 README 嵌入（双通道方案预留，见下文）。
- **抽样小池子基准会系统性高估绝对排名**（1000/43万 的背景竞争密度不足），
  抽样实验只可用于模型间相对比较，不可用于预测全库表现。

## 二、精度一致性规则

- 语料向量与查询向量必须**同模型 + 同数值管线**。
- fp32 ↔ int8 同文本向量余弦：min 0.971 / mean 0.978 —— 存在真实漂移，
  混用会引入噪声级排序偏移（实测 ±3 名内，但不应依赖）。
- 最终约定：语料（Kaggle GPU fp32）↔ 查询（本地 fp32 ONNX model.onnx）✓ 数值同源。
- int8 文件（onnx/model_int8.onnx）仅用于离线批量加速，禁止接入在线查询链路。

## 三、本地 CPU 性能实测（i5-7500 4核4线程, 无 GPU, 无 AVX-512/VNNI）

| 配置 | 吞吐 | 备注 |
|------|------|------|
| PyTorch fp32 | 6.0 条/s* | *10 token 假文本基准 |
| ONNX fp32 | 10.7 条/s* | |
| ONNX int8 动态量化 | 14.8 条/s* | CPU 无 VNNI，量化收益仅 ~40% |
| ONNX int8 **真实文本**（~50 tok） | **3.5 条/s 稳态** | ⚠️ 假文本基准会高估 4 倍 |
| OpenVINO fp32 | 2.4 条/s | 反而更慢，放弃 |

⚠️ **基准陷阱**：用重复短语测吞吐会把结果高估约 4 倍（token 数与 cache 局部性双重影响），
必须用真实负载文本做稳态测量。

## 四、火山方舟套餐（Agent/Coding Plan）限速实测

- 错误码：`HTTP 429 AccountRateLimitExceeded` —— **账号级**限速，确凿证据来自
  ARK_TRACE=1 分块计时日志。
- 行为模式：**令牌桶**。新进程有突发余量（首分钟可达 15+ 条/s），随后衰减至稳态；
  单账号稳态通过率实测在 1800~11700 TPM 之间波动（惩罚期随持续消费逐步恢复）。
- **加进程无效**（桶按账号计），三账号并行 ≈ 三倍吞吐。
- AFP 额度完全不是瓶颈：全量 43 万条仅约 710 AFP（Small 月额度 20000 的 3.5%）。
- Embeddings API 单请求上限 **10 条**（超出报 InvalidParameter）。
- 套餐端点互不相通（key 跨端点使用报 401）：
  - Agent Plan → `/api/plan/v3`
  - Coding Plan → `/api/coding/v3`
- 新式 KGAT_ token 的方舟无关，但同类问题见 Kaggle 一节。

## 五、Kaggle 自动化踩坑（API 全自动跑 kernel）

1. **认证**：新式 `KGAT_` token 必须 **Bearer** 方式（basic auth 返回 401）；
   CLI 用环境变量 `KAGGLE_API_TOKEN` 即可启用 Bearer。
2. **数据集挂载路径**：新版为 `/kaggle/input/datasets/<user>/<slug>/`（多了 `datasets/` 段），
   且 `.gz` 文件被自动解压成 `.jsonl`。代码里不要硬编码路径，用 os.walk 按文件名前缀查找。
3. **GPU 型号分配**：kernel-metadata.json 的 `machine_shape` 枚举值**无公开文档**，
   无法识别的值会**静默回落到 P100**；而 Kaggle 预装 torch 仅支持 sm_70+，
   P100 (sm_60) 直接不兼容报错。实测 `nvidiaTeslaT4`、`gpu_t4x2` 均回落 P100。
   **唯一可靠方法：网页 Session options → Accelerator → GPU T4 x2**。
4. 并发 GPU 会话上限 **2 个**（超出报 Maximum batch GPU session count）。
5. kernel 日志只有跑完（或出错）后才能经 output 接口拉取，运行中拿不到。

## 六、本机网络环境坑（Clash TUN fake-ip）

- Clash 开 TUN + fake-ip 模式时，**所有域名**（含国内 volces.com）都解析为 fake-IP
  （IPv4: 198.18.x.x / IPv6: fc00::x），流量全部经代理客户端转发。
- 本 Linux 内核**不支持 IPv6**：AAAA 记录的连接必报 `[Errno 97] Address family not
  supported by protocol`。
- 组合症状极具迷惑性：**长跑进程"启动快→逐渐全部超时"，重启进程又恢复正常**
  （DNS 解析结果在好坏地址间波动）。极易误判为服务端限速或代码问题。
- 排查顺序（本次总结）：① 读应用日志的真实错误串（Errno 97 ≠ 429）
  ② `socket.getaddrinfo` 多次采样看地址族/IP ③ `cat /etc/resolv.conf` + `ip addr`
  找 TUN 设备 ④ 用 wchan/py-spy 确认阻塞点。
- 缓解：`ark_client._force_ipv4()` 过滤 AAAA（已内置，ARK_FORCE_IPV4=0 可关）；
  彻底解决需在 Clash 配置 `fake-ip-filter: ['+.volces.com']`。

## 七、检索工程结论

1. **关键词通道曾有致命 bug**：`--query` 从未拼进 GraphQL 检索式（等于高星活跃仓库
   随机采样）。修复后中文意图经 LLM 双语改写可用，但仍有天花板：
   - GitHub 全文匹配是 AND 语义，元数据太短的项目（jcode 描述仅 6 词）无法命中；
   - LLM 改写的关键词偏泛化，精确命中依赖项目描述自带领域词。
2. 语义双语 RRF 融合（--dual-query）在旧模型下无增益（问题在模型不在查询词汇），
   保留开关供新模型复测。
3. star 先验混合排序（distance − λ·log10(1+stars)）：能救 alist 类头部项目，
   但实测会同时拉入无关大热门（cdnjs/OmniRoute 类），**默认关闭**（--star-weight 0），
   待 README 双通道落地去噪后再评估启用。
4. README 是属性型查询的唯一信号源；双通道表 `repo_readme_vectors` 已建，
   抓取管线待落地。README 入库前必须清理：HTML 标签、徽章墙、裸 URL、代码块
   （否则 600 字符里全是标记垃圾，语义为零——v1 实验翻车实录）。

## 八、运维操作备忘

- 后台任务启动模板：`setsid nohup env ... python3 -u ... >> log 2>&1 < /dev/null & disown`
  （工具超时会杀进程组，setsid 脱离是必须的）。
- `pkill -f`/`pgrep -f` 的模式会匹配当前 shell 自身命令行里的同名文本 → 自杀。
  用 `pgrep -f "xxx[.]y"` 括号技巧，且 kill 与 launch 分开在不同调用中执行。
- 断点续传天然支持：todo = 全量 id − repo_vectors 已有 id，中断重跑零浪费。
- py-spy dump 需 sudo（无密码则用 `/proc/PID/wchan` 粗判：do_sys_poll = 等 IO）。
