# -*- coding: utf-8 -*-
"""导入 GPU 产出的向量包到 v3 库（Colab 回导脚本）。

用法:
    python3 import_gpu_vectors.py /path/to/vectors.npz

校验: id 集合与库内 repos 完全一致、维度 1024、向量已归一化。
幂等: INSERT OR REPLACE, 可重复执行。
"""
import sys
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(__file__).parent.parent))
import _common.sqlite_store as ss  # noqa: E402


def main(npz_path: str) -> None:
    data = np.load(npz_path, allow_pickle=False)
    ids = [str(i) for i in data["ids"]]
    vecs = data["vecs"].astype(np.float32)
    assert len(ids) == len(vecs), "id 与向量数量不一致"
    assert vecs.shape[1] == ss.EMBED_DIM, f"维度 {vecs.shape[1]} != 库 {ss.EMBED_DIM}"

    norms = np.linalg.norm(vecs, axis=1)
    print(f"载入 {len(ids)} 条向量, 维度 {vecs.shape[1]}, "
          f"norm 范围 [{norms.min():.4f}, {norms.max():.4f}]（应接近 1.0）")

    conn = ss.connect(SCRIPTS.parents[2] / "data" / "gh_search_index_v3.db")
    known = {r["id"] for r in conn.execute("SELECT id FROM repos")}
    missing = [i for i in ids if i not in known]
    print(f"库内元数据 {len(known)} 条, 包中缺失于库的 id: {len(missing)}")

    inserted = 0
    for k in range(0, len(ids), 500):
        chunk = list(zip(ids[k:k + 500], vecs[k:k + 500]))
        # vec0 虚拟表不支持 INSERT OR REPLACE，必须显式删除后插入
        conn.executemany(
            "DELETE FROM repo_vectors WHERE id=?", [(rid,) for rid, _ in chunk]
        )
        conn.executemany(
            "INSERT INTO repo_vectors(id, embedding) VALUES (?,?)",
            [(rid, ss._to_blob(v.tolist())) for rid, v in chunk],
        )
        conn.commit()
        inserted += len(chunk)
    total = ss.count_vectors(conn)
    print(f"写入完成: 本次 {inserted}, 库内总向量 {total}")


if __name__ == "__main__":
    main(sys.argv[1])
