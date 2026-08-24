#!/usr/bin/env python3
"""
为本地仓库元数据生成嵌入向量并写入 sqlite-vec（语义索引 Step 2）。

读取 fetch_repos.py 落库的 `repos` 元数据表，把每条构造为嵌入文本，
调用 Pinecone integrated inference 生成向量（llama-text-embed-v2, 1024 维），
写入本地 sqlite-vec 的 `repo_vectors` 虚拟表 + `embed_status` 记录表。

设计要点:
    - 断点续传: 已嵌入的仓库(id 在 repo_vectors)自动跳过, 支持中断后重跑
    - 批量嵌入: 每批 EMBED_BATCH 条 (Pinecone 单请求上限, 实测 96 兼容)
    - 文本构造: "Repo: {name}. Description: {desc}. Topics: {topic1,topic2}"
    - 嵌入成本: 按 token 计费(约 $0.08/M token), 本脚本打印累计 token 便于监控

用法:
    python3 build_index.py                          # 嵌入全部未嵌入仓库
    python3 build_index.py --limit 1000             # 只嵌入前 N 个(测试)
    python3 build_index.py --db /path/to.db         # 指定数据库

环境变量:
    PINECONE_EMBED_KEY - 嵌入专用密钥(优先; 逗号分隔多账号自动轮换)
    PINECONE_API_KEY   - 回落密钥(二者至少设一个)
    PINECONE_MODEL     - 嵌入模型(默认 llama-text-embed-v2)
    GH_SEARCH_DB       - sqlite 路径
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from sqlite_store import (
    DB_PATH,
    EMBED_DIM,
    EMBED_MODEL,
    connect,
    count_repos,
    count_vectors,
    list_all_repo_ids,
    record_embed,
    upsert_vec,
)

EMBED_BATCH = 96          # Pinecone embed 单批上限（实测兼容）
DEFAULT_LIMIT = 0         # 0 = 全部

_LOCAL_MODEL = None       # 进程内缓存，避免重复加载


def _get_local_model():
    """加载本地 bge-m3 int8 ONNX 模型（进程内单例）。"""
    global _LOCAL_MODEL
    if _LOCAL_MODEL is None:
        import glob
        cands = []
        for snap in glob.glob(os.path.expanduser(
                "~/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/*/")):
            if os.path.exists(os.path.join(snap, "onnx", "model_int8.onnx")) \
                    and os.path.exists(os.path.join(snap, "config.json")):
                cands.append(snap)
        if not cands:
            raise EmbedError("未找到含 onnx/model_int8.onnx 的 bge-m3 本地快照")
        snap = os.environ.get("GH_SEARCH_LOCAL_MODEL", sorted(cands)[-1])
        from sentence_transformers import SentenceTransformer
        _LOCAL_MODEL = SentenceTransformer(
            snap, backend="onnx",
            model_kwargs={"file_name": "onnx/model_int8.onnx"})
    return _LOCAL_MODEL


def embed_batch_local(texts: List[str], model_name: str) -> List[List[float]]:
    """本地 bge-m3 嵌入；归一化输出使 L2 距离与余弦序一致。"""
    m = _get_local_model()
    vecs = m.encode(texts, batch_size=min(len(texts), 64),
                    normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


class EmbedError(RuntimeError):
    """嵌入失败"""


class QuotaExhausted(EmbedError):
    """当月嵌入 token 配额用尽的标志（Pinecone 429 RESOURCE_EXHAUSTED）。
    提示嵌入脚本应切换到下一个可用账号继续。"""


def embed_batch(pc, model: str, texts: List[str]) -> List[List[float]]:
    """调 Pinecone embed 一批文本，返回向量列表。配额耗尽抛 QuotaExhausted。"""
    try:
        r = pc.inference.embed(
            model=model,
            inputs=texts,
            parameters={"input_type": "passage", "truncate": "END"},
        )
    except Exception as e:
        s = str(e)
        if "429" in s or "RESOURCE_EXHAUSTED" in s:
            raise QuotaExhausted(s)
        raise EmbedError(f"嵌入失败: {e}")
    return [d.values for d in r.data]


def build_index(db_path: Optional[str] = None, limit: int = DEFAULT_LIMIT,
                model: str = EMBED_MODEL, batch: int = EMBED_BATCH,
                dry_run: bool = False, force_ids: Optional[List[str]] = None,
                backend: str = "pinecone", shard: Optional[str] = None) -> Dict[str, Any]:
    """主流程。返回统计 dict。
    force_ids: 若指定，则只处理这些 id（忽略本已嵌入检查，强制重嵌）。
               用于 monthly 变化检测：embed_text 变了的仓库需要重新嵌入。
    backend:   pinecone(llama-text-embed-v2, 1024维) | ark(doubao-embedding-vision, 2048维)。
               ark 后端须 GH_SEARCH_EMBED_DIM=2048 且目标库为对应维度新建。
    """
    conn = connect(db_path)

    _ark = None
    _keys: List[str] = []
    pc = None
    if backend == "ark":
        from ark_client import ArkEmbed
        try:
            _ark = ArkEmbed()
        except Exception as e:  # noqa: BLE001
            raise EmbedError(str(e))
    elif backend == "local":
        if not dry_run:
            _get_local_model()   # 提前加载，失败即报
    else:
        # 多个嵌入账号（逗号分隔）自动轮换：一个撞 429 配额后切下一个继续
        _keys = [
            k.strip() for k in (
                os.environ.get("PINECONE_EMBED_KEY", "") or os.environ.get("PINECONE_API_KEY", "")
            ).split(",") if k.strip()
        ]
        if not _keys:
            raise EmbedError("未设置 PINECONE_EMBED_KEY 或 PINECONE_API_KEY 环境变量")
        if not dry_run:
            from pinecone import Pinecone
            try:
                pc = Pinecone(api_key=_keys[0])
            except ImportError:
                raise EmbedError("缺少 pinecone 库: pip install --user pinecone")

    if force_ids:
        # 只处理指定 id（强制重嵌, 不跳过已嵌入的）
        todo = list(force_ids)
    else:
        # 读全部未嵌入仓库（一次拉出已嵌入 id 集合, 内存判定, 避免逐条查库）
        ids = list_all_repo_ids(conn)
        embedded = {r["id"] for r in conn.execute("SELECT id FROM repo_vectors")}
        todo = [i for i in ids if i not in embedded]
    if limit:
        todo = todo[:limit]
    if shard:
        # 多进程分片: --shard i:n 取第 i 路（与其它 key 的 worker 并行分摊）
        si, sn = map(int, shard.split(":"))
        todo = todo[si::sn]
    total = len(todo)
    done = skipped = total_tokens = 0
    t0 = time.time()

    print(f"[index] 待嵌入 {total}（{'强制重嵌' if force_ids is not None else '已嵌入自动跳过'}）")
    if dry_run:
        print("[dry-run] 预览 3 条嵌入文本：")
        for i in todo[:3]:
            row = conn.execute("SELECT embed_text FROM repos WHERE id=?", (i,)).fetchone()
            print("   ", (row["embed_text"] if row else "")[:100])
        return {"todo": total, "dry_run": True}

    for start in range(0, total, batch):
        batch_ids = todo[start:start + batch]
        texts = []
        for i in batch_ids:
            row = conn.execute("SELECT embed_text FROM repos WHERE id=?", (i,)).fetchone()
            texts.append(row["embed_text"] if row else "")

        # 嵌入这一批；配额耗尽时轮换下一个账号（仅 pinecone）；
        # 偶发网关错误(504/超时等)指数退避重试, 避免长跑任务被单次抖动打断
        # （ark 后端在 ArkEmbed 内部已做 429/瞬断重试限速）
        vectors = None
        attempts = 0
        while vectors is None:
            try:
                if backend == "ark":
                    try:
                        vectors = _ark.embed(texts)
                    except Exception as e:  # noqa: BLE001
                        raise EmbedError(f"方舟嵌入失败: {e}")
                elif backend == "local":
                    try:
                        vectors = embed_batch_local(texts, model)
                    except Exception as e:  # noqa: BLE001
                        raise EmbedError(f"本地嵌入失败: {e}")
                else:
                    vectors = embed_batch(pc, model, texts)
            except QuotaExhausted:
                key_idx = getattr(pc, "_embed_key_idx", 0)
                next_idx = key_idx + 1
                if next_idx >= len(_keys):
                    print(f"\n[quota] 全部 {len(_keys)} 个嵌入账号当月配额均耗尽, 停止。"
                          f"已嵌 {done}/{total} 条, 剩余部分断点续传: "
                          f"重设 PINECONE_EMBED_KEY 后重跑即可。")
                    break
                print(f"  [quota] 嵌入账号{key_idx} 配额耗尽, 切换到账号{next_idx}...")
                pc = Pinecone(api_key=_keys[next_idx])
                pc._embed_key_idx = next_idx
            except EmbedError as e:
                attempts += 1
                if attempts >= 4:
                    raise
                wait = min(5 * 2 ** (attempts - 1), 40)
                print(f"  [retry] {e}\n  [retry] {wait}s 后第 {attempts}/3 次重试...")
                time.sleep(wait)

        if vectors is None:  # 全部账号配额耗尽，stop
            break

        for i, vec, text in zip(batch_ids, vectors, texts):
            if len(vec) != EMBED_DIM:
                print(f"  [warn] {i}: 维度 {len(vec)} != {EMBED_DIM}, 跳过")
                continue
            upsert_vec(conn, i, vec)
            record_embed(conn, i, model, 0, embed_text=text)
            done += 1
        conn.commit()

        # token 统计（可选：Pinecone embed 返回 usage）
        try:
            usage = None
            # embed_batch 不返回 usage; 这里粗略按批次估算文本长度
            usage = sum(len(t.split()) for t in texts)
        except Exception:
            usage = 0
        total_tokens += usage
        elapsed = time.time() - t0
        print(f"  [{done}/{total}] 已嵌 {done} 条, {elapsed:.0f}s"
              f"（{done/max(elapsed,0.001):.1f} 条/s）")

    print(f"\n[index] 完成: 嵌入 {done} 条, 跳过 {skipped} 条, 累计 token 约 {total_tokens}")
    print(f"[index] 向量总数 {count_vectors(conn)}, 元数据总数 {count_repos(conn)}")
    return {"embedded": done, "skipped": skipped, "tokens": total_tokens}


def main():
    parser = argparse.ArgumentParser(description="为仓库元数据生成嵌入向量并写入 sqlite-vec")
    parser.add_argument("--db", default=None, help="sqlite 路径（默认插件 data 目录）")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="只嵌入前 N 条（默认全部）")
    parser.add_argument("--backend", choices=["pinecone", "ark", "local"], default="pinecone",
                        help="嵌入后端（ark=doubao 2048维 / local=bge-m3 int8 本地, 须配对应维度库）")
    parser.add_argument("--model", default=None,
                        help="嵌入模型（默认按 backend: pinecone=llama-text-embed-v2 / ark=doubao-embedding-vision / local=BAAI-bge-m3）")
    parser.add_argument("--batch", type=int, default=EMBED_BATCH,
                        help=f"每批嵌入条数（默认 {EMBED_BATCH}）")
    parser.add_argument("--dry-run", action="store_true", help="只预览不实际嵌入")
    parser.add_argument("--shard", default=None,
                        help="分片 i:n（多进程/多 key 并行时各取一路，如 0:2 / 1:2）")
    args = parser.parse_args()

    model = args.model or {
        "ark": "doubao-embedding-vision",
        "local": "BAAI/bge-m3(int8-onnx)",
    }.get(args.backend, EMBED_MODEL)

    try:
        stats = build_index(args.db, args.limit, model, args.batch, args.dry_run,
                            backend=args.backend, shard=args.shard)
        if not args.dry_run and stats.get("embedded", 0) == 0:
            print("[提示] 没有新嵌入。若想强制重建，请删除 embed_status 对应行。")
    except EmbedError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()