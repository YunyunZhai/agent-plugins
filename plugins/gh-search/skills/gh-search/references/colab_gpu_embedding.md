# Colab 免费GPU批量嵌入操作手册

目标：用 Colab 免费 T4 把 43 万条 embed_text 一次跑完（约 30-50 分钟），替代本地 34 小时。

## 步骤

### 1. 上传数据
把 `agent-plugins/embed_texts.jsonl.gz`（29.9MB）拖进 Colab 左侧文件面板。

### 2. 新建 Notebook，粘贴运行以下单元格

```python
# 单元格1: 安装
!pip -q sentence-transformers

# 单元格2: 加载数据 + GPU 批量嵌入 + 保存
import json, gzip, numpy as np, time, torch
from sentence_transformers import SentenceTransformer

print("GPU:", torch.cuda.get_device_name(0))
model = SentenceTransformer('BAAI/bge-m3', device='cuda')
model.max_seq_length = 256          # embed_text 均为短文本，256 足够

ids, texts = [], []
with gzip.open('embed_texts.jsonl.gz', 'rt', encoding='utf-8') as f:
    for line in f:
        d = json.loads(line)
        ids.append(d['i']); texts.append(d['t'])
print('载入:', len(ids))

t0 = time.time()
vecs = model.encode(texts, batch_size=256,
                    normalize_embeddings=True,
                    show_progress_bar=True,
                    convert_to_numpy=True).astype(np.float32)
dt = time.time() - t0
print(f'完成: {len(vecs)} 条 / {dt/60:.1f} 分钟 = {len(vecs)/dt:.0f} 条/s')

np.savez_compressed('vectors.npz', ids=np.array(ids), vecs=vecs)
!ls -lh vectors.npz
```

```python
# 单元格3: 打包下载（约1.5GB，浏览器下载）
from google.colab import files
files.download('vectors.npz')
```

### 3. 回传本机后导入数据库

```bash
python3 plugins/gh-search/skills/gh-search/scripts/import_gpu_vectors.py ~/Downloads/vectors.npz
```

## 注意事项

1. **精度约定**：Colab 端必须 fp32（默认，不要加 .half()），与本地查询端 fp32 ONNX 保持同源。
2. **顺序无关**：npz 内含 id 数组，回导按 id 对应，乱序也不影响。
3. **断线重连**：免费会话可能中断，重跑单元格2即可（数据还在则跳过上传）。
4. 若 T4 排队紧张，可换 Runtime → Change runtime type 重试，或改用 Kaggle（P100，流程相同，去掉 files.download 改手动保存到输出）。
