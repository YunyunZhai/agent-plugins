#!/usr/bin/env python3
"""
本地语义索引的共享存储模块（sqlite + sqlite-vec）。

职责:
    - 建库: 初始化 `repos`(元数据表)、`repo_vectors`(vec0 向量表)、`embed_status`(嵌入记录表)
    - 元数据: upsert/get/get_by_name/exists/is_fork_archived/filter_by_stars 等
    - 向量: 插入/状态检查/kNN 检索（sqlite-vec vec0）
    - 供 fetch_repos.py / build_index.py / semantic_search.py / incremental_update.py 复用

设计要点:
    - 数据库文件默认放插件目录 `plugins/gh-search/data/gh_search_index.db`
      (可用环境变量 GH_SEARCH_DB 覆盖)
    - 嵌入向量固定 1024 维（llama-text-embed-v2 实测维度）
    - 本模块含 sqlite-vec 加载样板，其他脚本 import 后用 store.DB / store.vec_conn()
      即可直接用 SQL

环境变量:
    GH_SEARCH_DB  - 数据库文件路径（默认 <脚本目录>/../../data/gh_search_index.db）
"""

import os
import sqlite3
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import sqlite_vec
    HAS_SQLITE_VEC = True
except ImportError:
    HAS_SQLITE_VEC = False

# 嵌入模型与维度（须与 build_index.py 的 embedding 模型一致）
EMBED_MODEL = "llama-text-embed-v2"
# 向量维度可按库切换（1024=llama-text-embed-v2 旧库, 2048=doubao-embedding-vision 新库）。
# 进程内必须与目标 DB 的实际维度一致，否则 vec0 校验失败。
EMBED_DIM = int(os.environ.get("GH_SEARCH_EMBED_DIM", "1024"))

# 默认数据库路径：插件 data 目录（plugins/gh-search/data/）
# 脚本位于 plugins/gh-search/skills/gh-search/scripts/ → 上 4 层到 plugins/gh-search/
_DEFAULT_DB = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "gh_search_index_v3.db"
)
DB_PATH = Path(os.environ.get("GH_SEARCH_DB", str(_DEFAULT_DB)))


class VectorSearchError(RuntimeError):
    """向量检索相关错误（未加载 sqlite-vec 等）"""


# ══ 数据库连接与初始化 ═══════════════════════════════════════════════

def connect(db_path: Optional[str | Path] = None) -> sqlite3.Connection:
    """建立带 row_factory 的 sqlite 连接, 并加载 sqlite-vec 扩展。"""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _load_vec_extension(conn)
    init_schema(conn)
    return conn


def _load_vec_extension(conn: sqlite3.Connection) -> None:
    """加载 sqlite-vec 扩展; 失败则抛 VectorSearchError（仅向量功能不可用）。"""
    if not HAS_SQLITE_VEC:
        raise VectorSearchError(
            "缺少 sqlite-vec 依赖, 无法进行向量检索。请先安装: pip install --user sqlite-vec"
        )
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def init_schema(conn: sqlite3.Connection) -> None:
    """创建全部表结构（幂等）。

    设计（2026-08 精简版）：
    repos 只存「语义相关 + 基本不变」字段，用于向量嵌入与变化检测；
    stars 为例外（2026-08 排序先验本地化）：允许周级陈旧，检索时零在线调用。
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repos (
            id              TEXT PRIMARY KEY,          -- nameWithOwner (owner/name)
            name            TEXT NOT NULL,             -- 仓库名（不含 owner）
            description     TEXT,                      -- 仓库描述
            topics          TEXT,                      -- JSON 数组字符串
            primary_language TEXT,                     -- 主语言（语义查询语言维度）
            is_fork         INTEGER NOT NULL DEFAULT 0,
            is_archived     INTEGER NOT NULL DEFAULT 0,
            embed_text      TEXT NOT NULL DEFAULT '',  -- 构造的嵌入文本（变化检测依据）
            stars           INTEGER NOT NULL DEFAULT 0 -- star 快照（排序先验, 周级陈旧可接受）
        )
        """
    )
    # 迁移：存量库补 stars 列
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(repos)")}
    if "stars" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN stars INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embed_status (
            id              TEXT PRIMARY KEY,
            model           TEXT NOT NULL,
            token_count     INTEGER NOT NULL DEFAULT 0,
            embed_text_hash TEXT,              -- 嵌入时的文本哈希(变化检测用)
            embedded_at     TEXT DEFAULT (datetime('now'))
        )
        """
    )
    # vec0 虚拟表只能存在与否检查，不能 CREATE IF NOT EXISTS 重复
    _create_vec_table(conn)
    _create_readme_vec_table(conn)

    # 迁移：存量库补 readme_embed_text 列（README 双通道的嵌入文本快照）
    if "readme_embed_text" not in cols:
        conn.execute(
            "ALTER TABLE repos ADD COLUMN readme_embed_text TEXT NOT NULL DEFAULT ''"
        )
        conn.commit()


def _create_vec_table(conn: sqlite3.Connection) -> None:
    """按需创建主向量表（元数据嵌入通道，幂等）。"""
    if not _table_exists(conn, "repo_vectors"):
        conn.execute(
            f"CREATE VIRTUAL TABLE repo_vectors USING vec0"
            f"(id TEXT PRIMARY KEY, embedding FLOAT[{EMBED_DIM}])"
        )
        conn.commit()


def _create_readme_vec_table(conn: sqlite3.Connection) -> None:
    """按需创建 README 向量表（双通道之二；空表时检索自动回落主通道）。"""
    if not _table_exists(conn, "repo_readme_vectors"):
        conn.execute(
            f"CREATE VIRTUAL TABLE repo_readme_vectors USING vec0"
            f"(id TEXT PRIMARY KEY, embedding FLOAT[{EMBED_DIM}])"
        )
        conn.commit()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


# ══ 元数据操作 ═════════════════════════════════════════════════════

MAX_DESC_CHARS = 350           # GitHub 网页端描述字段硬性上限；超过此长度的为 API 绕过写入的垃圾内容，直接丢弃

def build_embed_text(repo: Dict[str, Any]) -> str:
    """
    把仓库元数据构造成嵌入文本（语义索引的输入，同时作为变化检测依据）。
    只含语义相关字段：name/primary_language/description/topics。
    description 超过 MAX_DESC_CHARS（GitHub 网页端硬限）视为垃圾内容，丢弃。
    """
    name = repo.get("id") or ""            # id 即 nameWithOwner (owner/name)
    lang = repo.get("primary_language") or ""
    desc = (repo.get("description") or "").strip()
    if len(desc) > MAX_DESC_CHARS:
        desc = ""                           # 超出 GitHub 网页端硬限，视为垃圾内容
    topics = repo.get("topics") or []
    if isinstance(topics, str):
        try:
            topics = json.loads(topics)
        except Exception:
            topics = []
    parts = [f"Repo: {name}"]
    if lang:
        parts.append(f"Language: {lang}")
    if desc:
        parts.append(f"Description: {desc}")
    if topics:
        parts.append("Topics: " + ", ".join(topics))
    return ". ".join(parts)


def insert_new_repo(conn: sqlite3.Connection, repo: Dict[str, Any]) -> bool:
    """
    只插入新仓库（INSERT OR IGNORE 语义）。
    已存在 → 不更新、返回 False；新插入 → 返回 True。
    供「每周只插新」使用。
    """
    topics = repo.get("topics") or ""
    if not isinstance(topics, str):
        topics = json.dumps(topics, ensure_ascii=False)
    name = repo.get("name") or (repo.get("id") or "").split("/")[-1]
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO repos (
            id, name, description, topics, primary_language,
            is_fork, is_archived, embed_text, stars
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            repo["id"],
            name,
            repo.get("description"),
            topics,
            repo.get("primary_language"),
            1 if repo.get("is_fork") else 0,
            1 if repo.get("is_archived") else 0,
            build_embed_text(repo),
            int(repo.get("stars") or 0),
        ),
    )
    return cur.rowcount > 0


def update_if_text_changed(conn: sqlite3.Connection, repo: Dict[str, Any]) -> bool:
    """
    对比 embed_text，仅当语义文本（desc/topics/lang）变化时更新元数据与 embed_text。
    返回是否发生了更新（变化检测用：变了 → 调用方需重新嵌入该仓库向量）。
    仓库不存在 → 走 insert_new_repo。
    """
    new_text = build_embed_text(repo)
    row = conn.execute(
        "SELECT embed_text FROM repos WHERE id=?", (repo["id"],)
    ).fetchone()
    if row is None:
        return insert_new_repo(conn, repo)
    if row["embed_text"] == new_text:
        return False  # 语义文本未变, 无需更新也无需重嵌
    topics = repo.get("topics") or ""
    if not isinstance(topics, str):
        topics = json.dumps(topics, ensure_ascii=False)
    name = repo.get("name") or (repo.get("id") or "").split("/")[-1]
    conn.execute(
        """
        UPDATE repos SET
            name=?, description=?, topics=?, primary_language=?,
            is_fork=?, is_archived=?, embed_text=?
        WHERE id=?
        """,
        (
            name,
            repo.get("description"),
            topics,
            repo.get("primary_language"),
            1 if repo.get("is_fork") else 0,
            1 if repo.get("is_archived") else 0,
            new_text,
            repo["id"],
        ),
    )
    return True


def get_repo(conn: sqlite3.Connection, repo_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM repos WHERE id=?", (repo_id,)).fetchone()
    return dict(row) if row else None


def upsert_stars(conn: sqlite3.Connection, mapping: Dict[str, int]) -> None:
    """批量更新仓库 star 快照（排序先验用，允许周级陈旧）。"""
    rows = [(int(s), rid) for rid, s in mapping.items() if rid]
    conn.executemany("UPDATE repos SET stars=? WHERE id=?", rows)
    conn.commit()


def list_all_repo_ids(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute("SELECT id FROM repos").fetchall()
    return [r["id"] for r in rows]


def count_repos(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0]


# ══ 向量操作（sqlite-vec）═══════════════════════════════════════════

def _to_blob(values: List[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def upsert_vec(conn: sqlite3.Connection, repo_id: str, embedding: List[float]) -> None:
    """写入一条向量（upsert 语义：先删后插，避免重复）。"""
    if len(embedding) != EMBED_DIM:
        raise ValueError(
            f"嵌入维度 {len(embedding)} != 期望 {EMBED_DIM}（模型 {EMBED_MODEL}）"
        )
    conn.execute("DELETE FROM repo_vectors WHERE id=?", (repo_id,))
    conn.execute(
        "INSERT INTO repo_vectors (id, embedding) VALUES (?, ?)",
        (repo_id, _to_blob(embedding)),
    )


def count_vectors(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM repo_vectors").fetchone()[0]


def record_embed(
    conn: sqlite3.Connection, repo_id: str, model: str, token_count: int,
    embed_text: str = "",
) -> None:
    """记录嵌入状态（用于断点续传、计费追溯与变化检测）。"""
    conn.execute(
        """
        INSERT INTO embed_status(id, model, token_count, embed_text_hash)
        VALUES (?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            model=excluded.model,
            token_count=excluded.token_count,
            embed_text_hash=excluded.embed_text_hash,
            embedded_at=datetime('now')
        """,
        (repo_id, model, token_count, _hash(embed_text)),
    )


def find_changed_repos(conn: sqlite3.Connection) -> List[str]:
    """
    month 增量用：找出「当前 embed_text 与嵌入时的 embed_text_hash 不一致」的仓库，
    即文本变过、需要重新嵌入的仓库 id 列表。
    """
    rows = conn.execute(
        "SELECT r.id, r.embed_text, e.embed_text_hash FROM repos r "
        "JOIN embed_status e ON e.id = r.id WHERE e.embed_text_hash IS NOT NULL"
    ).fetchall()
    return [r["id"] for r in rows if _hash(r["embed_text"]) != r["embed_text_hash"]]


def _hash(text: str) -> str:
    """简单哈希（md5 前 16 位），用于 embed_text 变化检测。"""
    import hashlib
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def search_knn(
    conn: sqlite3.Connection,
    query_vec: List[float],
    k: int = 20,
    table: str = "repo_vectors",
) -> List[Dict[str, Any]]:
    """sqlite-vec kNN 检索，返回 [{id, distance}, ...] 按距离升序。

    table 可选 "repo_vectors"(元数据通道) 或 "repo_readme_vectors"(README 通道)。
    返回的 distance 是余弦距离 (0=相同, 2=相反)。
    sqlite-vec vec0 默认用 L2 距离，对归一化向量 L2² = 2*(1-cos_sim)，
    因此 cos_dist = L2² / 2。
    """
    if len(query_vec) != EMBED_DIM:
        raise ValueError(f"查询向量维度 {len(query_vec)} != {EMBED_DIM}")
    rows = conn.execute(
        f"SELECT id, distance FROM {table} WHERE embedding MATCH ? AND k = ?",
        (_to_blob(query_vec), k),
    ).fetchall()
    return [{"id": r["id"], "distance": r["distance"] ** 2 / 2} for r in rows]


def count_readme_vectors(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM repo_readme_vectors").fetchone()[0]


# ══ 便捷入口 ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    # 自检: 建库 + 演示 kNN（内存库）
    import random
    conn = connect(":memory:")
    ver = conn.execute("SELECT vec_version()").fetchone()[0] if HAS_SQLITE_VEC else "无"
    print(f"schema 初始化 OK, sqlite-vec 版本: {ver}")
    # 插几条假向量验证 kNN
    for i in range(30):
        rnd = [random.uniform(-1, 1) for _ in range(EMBED_DIM)]
        upsert_vec(conn, f"repo{i}", rnd)
    q = [0.1] * EMBED_DIM
    res = search_knn(conn, q, k=3)
    print(f"kNN 自检: {len(res)} 条 -> {[r['id'] for r in res]}")
    print(f"向量表计数: {count_vectors(conn)}")