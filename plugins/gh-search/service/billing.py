"""计费记录：SQLite 存储调用次数 + token 用量。"""

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

_DEFAULT_BILLING_DB = Path(__file__).resolve().parent.parent / "data" / "billing.db"


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _DEFAULT_BILLING_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS billing (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            timestamp   REAL NOT NULL,
            channel     TEXT NOT NULL,
            call_count  INTEGER NOT NULL DEFAULT 1,
            candidates  INTEGER NOT NULL DEFAULT 0,
            embedding_tokens INTEGER NOT NULL DEFAULT 0,
            rerank_tokens    INTEGER NOT NULL DEFAULT 0,
            error       INTEGER NOT NULL DEFAULT 0,
            elapsed_s   REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_billing_user_ts ON billing(user_id, timestamp)"
    )
    conn.commit()


def record_call(
    user_id: str,
    channel: str,
    candidates: int = 0,
    embedding_tokens: int = 0,
    rerank_tokens: int = 0,
    error: bool = False,
    elapsed_s: float = 0,
    db_path: Optional[str] = None,
) -> None:
    """写入一条计费记录。"""
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO billing (user_id, timestamp, channel, call_count,
           candidates, embedding_tokens, rerank_tokens, error, elapsed_s)
           VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)""",
        (user_id, time.time(), channel, candidates,
         embedding_tokens, rerank_tokens, 1 if error else 0, elapsed_s),
    )
    conn.commit()
    conn.close()


def get_summary(user_id: str, period: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """查询用户某月的用量汇总。period 格式: '2026-09'。"""
    conn = _get_conn(db_path)
    # 按月过滤: timestamp → datetime → strftime('%Y-%m')
    rows = conn.execute(
        """SELECT channel, SUM(call_count) as calls,
                  SUM(embedding_tokens + rerank_tokens) as tokens
           FROM billing
           WHERE user_id = ? AND strftime('%Y-%m', datetime(timestamp, 'unixepoch')) = ?
           GROUP BY channel""",
        (user_id, period),
    ).fetchall()
    conn.close()

    by_channel: Dict[str, int] = {}
    total_calls = 0
    total_tokens = 0
    for row in rows:
        by_channel[row["channel"]] = row["calls"]
        total_calls += row["calls"]
        total_tokens += row["tokens"]

    return {
        "user_id": user_id,
        "period": period,
        "total_calls": total_calls,
        "total_tokens": total_tokens,
        "by_channel": by_channel,
    }
