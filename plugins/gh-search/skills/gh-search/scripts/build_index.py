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
    PINECONE_API_KEY  - Pinecone API 密钥(必需)
    PINECONE_MODEL    - 嵌入模型(默认 llama-text-embed-v2)
    GH_SEARCH_DB      - sqlite 路径
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
    is_embedded,
    list_all_repo_ids,
    record_embed,
    upsert_vec,
)

EMBED_BATCH = 96          # Pinecone embed 单批上限（实测兼容）
DEFAULT_LIMIT = 0         # 0 = 全部


class EmbedError(RuntimeError):
    """嵌入失败"""


def get_pinecone():
    """初始化 Pinecone 客户端；缺 API key 抛 EmbedError。"""
    key = os.environ.get("PINECONE_API_KEY", "")
    if not key:
        raise EmbedError("未设置 PINECONE_API_KEY 环境变量")
    try:
        from pinecone import Pinecone
    except ImportError:
        raise EmbedError("缺少 pinecone 库: pip install --user pinecone")
    return Pinecone(api_key=key)


def embed_batch(pc, model: str, texts: List[str]) -> List[List[float]]:
    """调 Pinecone embed 一批文本，返回向量列表。失败抛 EmbedError。"""
    try:
        r = pc.inference.embed(
            model=model,
            inputs=texts,
            parameters={"input_type": "passage", "truncate": "END"},
        )
    except Exception as e:
        raise EmbedError(f"嵌入失败: {e}")
    return [d.values for d in r.data]


def build_index(db_path: Optional[str] = None, limit: int = DEFAULT_LIMIT,
                model: str = EMBED_MODEL, batch: int = EMBED_BATCH,
                dry_run: bool = False, force_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """主流程。返回统计 dict。
    force_ids: 若指定，则只处理这些 id（忽略本已嵌入检查，强制重嵌）。
               用于 monthly 变化检测：embed_text 变了的仓库需要重新嵌入。
    """
    conn = connect(db_path)
    pc = None if dry_run else get_pinecone()

    if force_ids:
        # 只处理指定 id（强制重嵌, 不跳过已嵌入的）
        todo = list(force_ids)
    else:
        # 读全部未嵌入仓库
        ids = list_all_repo_ids(conn)
        todo = [i for i in ids if not is_embedded(conn, i)]
    if limit:
        todo = todo[:limit]
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

        vectors = embed_batch(pc, model, texts)
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
    parser.add_argument("--model", default=EMBED_MODEL, help=f"嵌入模型（默认 {EMBED_MODEL}）")
    parser.add_argument("--batch", type=int, default=EMBED_BATCH,
                        help=f"每批嵌入条数（默认 {EMBED_BATCH}）")
    parser.add_argument("--dry-run", action="store_true", help="只预览不实际嵌入")
    args = parser.parse_args()

    try:
        stats = build_index(args.db, args.limit, args.model, args.batch, args.dry_run)
        if not args.dry_run and stats.get("embedded", 0) == 0:
            print("[提示] 没有新嵌入。若想强制重建，请删除 embed_status 对应行。")
    except EmbedError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()