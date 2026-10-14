"""
DB Connection Manager — Sub-bead 462.5 (P2) of ema_vwap-462.
Routes multi-DB access for the decomposed database architecture.
Provides: get_connection(db_key) -> sqlite3.Connection
         get_database(db_key) -> CandleDatabase instance for that domain
DB mappings: candles -> candles.db, batch_results -> batch_results.db,
paper_trading -> paper_trading.db, system -> system.db
"""

import os
import sqlite3
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DB_MAP: dict[str, str] = {
    "candles": str(PROJECT_ROOT / "data" / "candles.db"),
    "batch_results": str(PROJECT_ROOT / "data" / "batch_results.db"),
    "paper_trading": str(PROJECT_ROOT / "data" / "paper_trading.db"),
    "system": str(PROJECT_ROOT / "data" / "system.db"),
}


def get_connection(db_key: str, timeout: float = 30.0) -> sqlite3.Connection:
    """Return a SQLite connection for the named DB domain."""
    path = DB_MAP.get(db_key)
    if not path:
        raise KeyError(f"Unknown db_key: {db_key}. Available: {list(DB_MAP)}")
    conn = sqlite3.connect(path, timeout=timeout)
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = -64000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn


def get_database(db_key: str):
    """Return a CandleDatabase instance pointing at the named DB domain.

    The ``domain`` parameter is forwarded so ``init_db()`` only creates
    the tables that belong to this domain.
    """
    from src.database import CandleDatabase

    path = DB_MAP.get(db_key)
    if not path:
        raise KeyError(f"Unknown db_key: {db_key}. Available: {list(DB_MAP)}")
    return CandleDatabase(db_path=path, domain=db_key)


def get_db_info() -> dict[str, Any]:
    info: dict[str, Any] = {}
    for key, path in DB_MAP.items():
        if not os.path.exists(path):
            info[key] = {
                "path": path,
                "exists": False,
                "size_bytes": 0,
                "table_count": 0,
                "tables": [],
            }
            continue
        conn = sqlite3.connect(path, timeout=5.0)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [r[0] for r in cursor.fetchall()]
        conn.execute("PRAGMA database_list")
        info[key] = {
            "path": path,
            "exists": True,
            "size_bytes": os.path.getsize(path) if os.path.exists(path) else 0,
            "table_count": len(tables),
            "tables": tables,
        }
        conn.close()
    return info
