#!/usr/bin/env python3
"""
BGE-M3 vs 豆包 嵌入模型对比实验（进程内版本，无子进程）。
"""

import argparse
import json
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

QUERIES = [
    "聚合网盘文件管理",
    "低延迟编程智能体",
    "Python 安全漏洞扫描",
    "Markdown 笔记编辑器",
    "Rust 高性能 HTTP 框架",
    "Docker 容器编排工具",
    "机器学习模型部署",
    "爬虫数据采集框架",
    "JSON Schema 验证",
    "WebSocket 实时通信",
    "代码审查机器人",
    "API 文档生成",
    "分布式任务调度",
    "Git 工作流自动化",
    "React UI 组件库",
    "终端颜色主题",
    "日志分析工具",
    "图像处理 CLI",
    "数据库迁移工具",
    "加密通信协议",
]


def load_bge_model():
    """加载本地 bge-m3 fp32 ONNX 模型。"""
    from pipeline.build_index import _get_local_model
    return _get_local_model()


def load_ark_client():
    """加载方舟 doubao 嵌入客户端。"""
    from _common.ark_client import ArkEmbed
    api_key = os.environ.get("ARK_API_KEY", "")
    base_url = os.environ.get("ARK_BASE_URL", None)
    if not api_key:
        raise RuntimeError("未设置 ARK_API_KEY")
    return ArkEmbed(api_key=api_key, base_url=base_url)


def search_knn_raw(conn, query_vec, k, table="repo_vectors"):
    """直接 kNN 搜索，不经过 sqlite_store 的维度检查。"""
    blob = struct.pack(f"<{len(query_vec)}f", *query_vec)
    rows = conn.execute(
        f"SELECT id, distance FROM {table} WHERE embedding MATCH ? AND k = ?",
        (blob, k),
    ).fetchall()
    return [{"id": r["id"], "distance": r["distance"] ** 2 / 2} for r in rows]


def run_search(query: str, db_path: str, embed_fn, embed_dim: int,
               top_k: int = 20) -> Dict[str, Any]:
    """在指定库上跑语义搜索。"""
    import sqlite3
    import sqlite_vec

    # Embed query
    t0 = time.monotonic()
    qvec = embed_fn([query])[0] if isinstance(embed_fn([query]), list) else embed_fn([query])
    if isinstance(qvec, list) and len(qvec) > 0 and isinstance(qvec[0], list):
        qvec = qvec[0]
    embed_time = time.monotonic() - t0

    # Connect to DB
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    # kNN
    t1 = time.monotonic()
    hits = search_knn_raw(conn, qvec, k=top_k * 2)
    search_time = time.monotonic() - t1

    # Build candidates
    candidates = []
    for h in hits:
        repo = conn.execute("SELECT * FROM repos WHERE id=?", (h["id"],)).fetchone()
        if not repo:
            continue
        if repo["is_fork"] or repo["is_archived"]:
            continue
        topics = repo["topics"] or []
        if isinstance(topics, str):
            try:
                topics = json.loads(topics)
            except Exception:
                topics = []
        candidates.append({
            "full_name": repo["id"],
            "description": repo["description"],
            "topics": topics,
            "primary_language": repo["primary_language"],
            "stars": int(repo["stars"] or 0),
            "_semantic_distance": round(h["distance"], 4),
        })

    candidates = candidates[:top_k]
    conn.close()

    return {
        "query": query,
        "embed_dim": embed_dim,
        "embed_time_s": round(embed_time, 3),
        "search_time_s": round(search_time, 3),
        "candidates": candidates,
    }


def compare(bge_db: str, doubao_db: str, queries: List[str],
            top_k: int = 20) -> List[Dict[str, Any]]:
    """主对比流程。"""
    # 加载模型
    print("加载 BGE 模型...", file=sys.stderr)
    bge_model = load_bge_model()
    print("加载豆包 API 客户端...", file=sys.stderr)
    ark = load_ark_client()

    results = []

    for i, query in enumerate(queries):
        print(f"\n[{i+1}/{len(queries)}] Query: {query}", file=sys.stderr)
        print("─" * 60, file=sys.stderr)

        # BGE search
        try:
            bge_result = run_search(
                query, bge_db,
                lambda texts: [v.tolist() for v in bge_model.encode(
                    texts, batch_size=1, normalize_embeddings=True, show_progress_bar=False)],
                1024, top_k,
            )
        except Exception as e:
            print(f"  BGE ERROR: {e}", file=sys.stderr)
            bge_result = {"query": query, "error": str(e), "candidates": []}

        # Doubao search
        try:
            doubao_result = run_search(
                query, doubao_db,
                ark.embed,
                2048, top_k,
            )
        except Exception as e:
            print(f"  豆包 ERROR: {e}", file=sys.stderr)
            doubao_result = {"query": query, "error": str(e), "candidates": []}

        bge_cands = bge_result.get("candidates", [])
        db_cands = doubao_result.get("candidates", [])

        bge_names = {c["full_name"] for c in bge_cands}
        db_names = {c["full_name"] for c in db_cands}
        common = bge_names & db_names
        bge_only = bge_names - db_names
        db_only = db_names - bge_names

        print(f"  BGE:  {len(bge_cands)} candidates, {bge_result.get('embed_time_s', '?')}s", file=sys.stderr)
        print(f"  豆包: {len(db_cands)} candidates, {doubao_result.get('embed_time_s', '?')}s", file=sys.stderr)
        print(f"  交集: {len(common)}, BGE独有: {len(bge_only)}, 豆包独有: {len(db_only)}", file=sys.stderr)

        # 前 5 对比
        print(f"  {'Rank':<5} {'BGE (bge-m3)':<45} {'豆包 (doubao)':<45}", file=sys.stderr)
        print(f"  {'─'*5} {'─'*45} {'─'*45}", file=sys.stderr)
        for rank in range(min(5, max(len(bge_cands), len(db_cands)))):
            bge_name = bge_cands[rank]["full_name"] if rank < len(bge_cands) else "—"
            db_name = db_cands[rank]["full_name"] if rank < len(db_cands) else "—"
            marker = " ✓" if bge_name == db_name else ""
            print(f"  {rank+1:<5} {bge_name:<45} {db_name:<45}{marker}", file=sys.stderr)

        results.append({
            "query": query,
            "bge": bge_result,
            "doubao": doubao_result,
            "overlap": len(common),
            "bge_only": len(bge_only),
            "doubao_only": len(db_only),
        })

    return results


def print_summary(results: List[Dict[str, Any]]):
    """打印汇总统计。"""
    print("\n" + "=" * 70)
    print("对比汇总")
    print("=" * 70)

    total_overlap = 0
    total_bge_only = 0
    total_db_only = 0
    bge_times = []
    db_times = []

    for r in results:
        total_overlap += r.get("overlap", 0)
        total_bge_only += r.get("bge_only", 0)
        total_db_only += r.get("doubao_only", 0)
        if "embed_time_s" in r.get("bge", {}):
            bge_times.append(r["bge"]["embed_time_s"])
        if "embed_time_s" in r.get("doubao", {}):
            db_times.append(r["doubao"]["embed_time_s"])

    n = len(results)
    print(f"Query 数: {n}")
    print(f"Top-20 平均交集: {total_overlap / n:.1f} / 20")
    print(f"BGE 独有: {total_bge_only / n:.1f}, 豆包独有: {total_db_only / n:.1f}")
    if bge_times:
        print(f"BGE  平均嵌入耗时: {sum(bge_times)/len(bge_times):.2f}s")
    if db_times:
        print(f"豆包 平均嵌入耗时: {sum(db_times)/len(db_times):.2f}s")


def main():
    parser = argparse.ArgumentParser(description="BGE vs 豆包 嵌入模型对比")
    parser.add_argument("--queries", default=None, help="查询文件路径")
    parser.add_argument("--top-k", type=int, default=20, help="返回候选数")
    parser.add_argument("--bge-db", default=None, help="BGE 库路径")
    parser.add_argument("--doubao-db", default=None, help="豆包库路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--output", default=None, help="保存 JSON 到文件")
    args = parser.parse_args()

    data_dir = Path(__file__).resolve().parent.parent.parent / "data"
    bge_db = args.bge_db or os.environ.get("GH_SEARCH_BGE",
                                           str(data_dir / "gh_search_bge_subset.db"))
    doubao_db = args.doubao_db or os.environ.get("GH_SEARCH_DOUBAO",
                                                  str(data_dir / "gh_search_doubao_subset.db"))

    for label, path in [("BGE", bge_db), ("豆包", doubao_db)]:
        if not os.path.exists(path):
            print(f"❌ {label} 库不存在: {path}", file=sys.stderr)
            sys.exit(1)

    if args.queries:
        with open(args.queries) as f:
            queries = [line.strip() for line in f if line.strip()]
    else:
        queries = QUERIES

    print(f"对比实验: {len(queries)} 个 query, top_k={args.top_k}", file=sys.stderr)
    print(f"BGE 库: {bge_db}", file=sys.stderr)
    print(f"豆包库: {doubao_db}", file=sys.stderr)

    results = compare(bge_db, doubao_db, queries, args.top_k)
    print_summary(results)

    if args.json or args.output:
        output = {
            "config": {
                "bge_db": bge_db,
                "doubao_db": doubao_db,
                "top_k": args.top_k,
                "num_queries": len(queries),
            },
            "results": results,
        }
        json_str = json.dumps(output, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w") as f:
                f.write(json_str)
            print(f"\nJSON 已保存到: {args.output}", file=sys.stderr)
        if args.json:
            print(json_str)


if __name__ == "__main__":
    main()