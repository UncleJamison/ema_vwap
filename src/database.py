"""
SQLite Database Module for Persistent Candle Storage & Local Caching.
Stores and queries historical OHLCV data, optimizer results, paper trading ledger,
Optuna hyperparameter profiles, portfolio balances, background tasks, and settings under high concurrency with WAL mode.
"""

import functools
import json
import os
import random
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from src.logger import logger

# ------------------------------------------------------------
# Secret-at-rest encryption (ema_vwap-y16r)
# ------------------------------------------------------------
# Fernet symmetric encryption for settings flagged is_secret=True.
# Key source precedence: SETTINGS_ENCRYPTION_KEY env var -> key file.
# The key file lives outside the database so a stolen .db file alone
# cannot decrypt secrets.

_ENC_PREFIX = "enc:v1:"


def _get_or_create_encryption_key() -> bytes:
    """Load or create the Fernet key used to encrypt settings at rest."""
    from cryptography.fernet import Fernet

    env_key = os.environ.get("SETTINGS_ENCRYPTION_KEY")
    if env_key:
        return env_key.encode()

    key_path = os.environ.get(
        "SETTINGS_ENCRYPTION_KEY_FILE",
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", ".settings_key"
        ),
    )
    key_path = os.path.abspath(key_path)
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            key = f.read().strip()
            if key:
                return key

    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    with open(key_path, "wb") as f:
        f.write(key)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass  # Windows: chmod is advisory only
    return key


def _encrypt_value(value: str) -> str:
    """Encrypt a plaintext value for storage (enc:v1:<token>)."""
    from cryptography.fernet import Fernet

    f = Fernet(_get_or_create_encryption_key())
    return _ENC_PREFIX + f.encrypt(value.encode()).decode()


def _decrypt_value(stored: str) -> str:
    """Decrypt an enc:v1: token back to plaintext. Non-prefixed values pass through."""
    from cryptography.fernet import Fernet

    if not stored.startswith(_ENC_PREFIX):
        return stored  # legacy plaintext value
    f = Fernet(_get_or_create_encryption_key())
    return f.decrypt(stored[len(_ENC_PREFIX) :].encode()).decode()


DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "candles.db"),
)


def normalize_symbol_key(symbol: str) -> str:
    """Normalize equivalent exchange symbol spellings to BASE/QUOTE."""
    clean = symbol.strip().upper().replace("-", "/").replace("_", "/")
    if "/" not in clean:
        for quote in ("USDT", "USDC", "USD"):
            if clean.endswith(quote):
                return f"{clean[:-len(quote)]}/{quote}"
        return f"{clean}/USD"
    return clean


def retry_on_db_lock(max_retries: int = 5, base_delay: float = 0.1):
    """
    Decorator for retrying database write operations on sqlite3.OperationalError
    (e.g., database is locked or busy under concurrent write load).
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    err_msg = str(e).lower()
                    if (
                        "locked" in err_msg
                        or "busy" in err_msg
                        or "disk i/o" in err_msg
                        or "io error" in err_msg
                    ):
                        retries += 1
                        if retries > max_retries:
                            raise
                        delay = base_delay * (2 ** (retries - 1)) + random.uniform(
                            0, 0.05
                        )
                        time.sleep(delay)
                    else:
                        raise

        return wrapper

    return decorator


# Domain → table mapping: each DB file only creates its own tables.
# None (legacy/test) creates everything for backward compatibility.
DOMAIN_TABLES: dict[str | None, set[str]] = {
    "candles": {"candles"},
    "batch_results": {"batch_results"},
    "paper_trading": {
        "paper_ledger",
        "paper_profiles",
        "paper_positions",
        "paper_ledger_transactions",
        "paper_trade_history",
        "optuna_profiles",
    },
    "system": {
        "settings",
        "alerts",
        "background_tasks",
        "portfolio_balances",
    },
    None: {  # legacy / tests: all tables
        "candles",
        "batch_results",
        "paper_ledger",
        "paper_profiles",
        "paper_positions",
        "paper_ledger_transactions",
        "paper_trade_history",
        "optuna_profiles",
        "portfolio_balances",
        "alerts",
        "background_tasks",
        "settings",
    },
}


class CandleDatabase:
    """SQLite Database Manager for caching and retrieving historical crypto candle data & trading artifacts."""

    def __init__(
        self, db_path: str = DB_PATH, pool_size: int = 4, domain: str | None = None
    ):
        self.db_path = db_path
        self.domain = domain
        self._pool_size = pool_size
        self._pool: list[sqlite3.Connection] = []
        self._pool_lock = threading.Lock()
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        # Pre-flight: if an existing file cannot be opened at all, quarantine it
        # before trying init_db (catches both "malformed" and "unable to open").
        if os.path.exists(self.db_path):
            self._preflight_check()
        try:
            self.init_db()
        except (sqlite3.DatabaseError, sqlite3.OperationalError) as e:
            if self._is_corruption_error(e):
                self._recover_corrupted_db(e)
                self.init_db()
            else:
                raise

    @staticmethod
    def _is_corruption_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "malformed" in msg or "not a database" in msg or "corrupt" in msg

    def _preflight_check(self) -> None:
        """Open the existing DB file to verify it is not corrupt. Quarantine only on real corruption."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.execute("PRAGMA schema_version;")
            conn.close()
        except sqlite3.DatabaseError as e:
            if self._is_corruption_error(e):
                logger.warning(
                    f"[CandleDatabase] Pre-flight check detected corruption ({e}). Quarantining..."
                )
                self._recover_corrupted_db(e)
            else:
                logger.warning(
                    f"[CandleDatabase] Pre-flight check encountered operational warning: {e}"
                )

    def _recover_corrupted_db(self, exc: Exception) -> list[str]:
        """Quarantine malformed database files and allow creating a clean database."""
        logger.error(
            f"[CandleDatabase] Malformed SQLite database detected ({exc}). Auto-recovering..."
        )
        ts = int(time.time())
        # Brief pause to release any file handles before rename (Windows can be slow)
        time.sleep(0.5)
        for ext in ["", "-wal", "-shm"]:
            src = f"{self.db_path}{ext}"
            if os.path.exists(src):
                dest = f"{self.db_path}.corrupt_{ts}{ext}"
                try:
                    os.rename(src, dest)
                    logger.warning(
                        f"[CandleDatabase] Quarantined corrupted DB file: {src} -> {dest}"
                    )
                except Exception as err:
                    logger.error(f"[CandleDatabase] Could not rename {src}: {err}")
                    try:
                        os.remove(src)
                    except Exception as e:
                        logger.warning(f"[CandleDatabase] Could not remove {src}: {e}")
        quarantined = [f"{self.db_path}.corrupt_{ts}"]
        return quarantined

    def get_db_info(self) -> dict[str, Any]:
        """Return comprehensive database integrity and metadata info."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            result = cur.execute("PRAGMA integrity_check").fetchall()
            integrity = str(result[0][0]) if result else "unknown"
            # Journal mode
            journal = cur.execute("PRAGMA journal_mode").fetchone()
            journal_mode = journal[0].lower() if journal else "unknown"
            # File size
            import os

            file_size = (
                os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
            )
            # Tables
            tables = [
                r[0]
                for r in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            # Candle record count
            candle_count = 0
            if "candles" in tables:
                candle_count = cur.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
            return {
                "integrity_check": integrity,
                "journal_mode": journal_mode,
                "file_size_bytes": file_size,
                "tables": tables,
                "candle_record_count": candle_count,
            }
        finally:
            self._return_connection(conn)

    def export_candles_json(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> str:
        """Export candles to JSON string (OHLCV format)."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            # Select only OHLCV columns for standard format
            query = "SELECT timestamp, open, high, low, close, volume FROM candles"
            params = []
            conditions = []
            if exchange:
                conditions.append("exchange = ?")
                params.append(exchange.lower())
            if symbol:
                conditions.append("symbol = ?")
                params.append(symbol)
            if timeframe:
                conditions.append("timeframe = ?")
                params.append(timeframe.lower())
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY timestamp ASC"
            if limit:
                query += " LIMIT ?"
                params.append(limit)
            rows = cur.execute(query, params).fetchall()
            cols = [d[0] for d in cur.description]
            import json

            return json.dumps([dict(zip(cols, row)) for row in rows])
        finally:
            self._return_connection(conn)

    def export_candles_csv(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> str:
        """Export candles to CSV string (OHLCV format)."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            # Select only OHLCV columns for standard format
            query = "SELECT timestamp, open, high, low, close, volume FROM candles"
            params = []
            conditions = []
            if exchange:
                conditions.append("exchange = ?")
                params.append(exchange.lower())
            if symbol:
                conditions.append("symbol = ?")
                params.append(symbol)
            if timeframe:
                conditions.append("timeframe = ?")
                params.append(timeframe.lower())
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY timestamp ASC"
            if limit:
                query += " LIMIT ?"
                params.append(limit)
            rows = cur.execute(query, params).fetchall()
            cols = [d[0] for d in cur.description]
            import csv
            from io import StringIO

            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(cols)
            writer.writerows(rows)
            return output.getvalue()
        finally:
            self._return_connection(conn)

    def vacuum_database(self) -> dict[str, Any]:
        """Run VACUUM on the database."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("VACUUM")
            return {"status": "success", "message": "Database vacuumed successfully"}
        except Exception as e:
            logger.error(f"[Database] vacuum failed: {e}")
            return {"status": "error", "message": "vacuum failed due to internal error"}
        finally:
            self._return_connection(conn)

    def save_alert(self, alert: dict[str, Any]) -> str:
        """Persist an alert to the alerts table."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            ts = (
                alert.get("timestamp")
                or alert.get("created_at")
                or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            )
            cur.execute(
                """
                INSERT INTO alerts (timestamp, type, message, level, source, category, metadata, acknowledged, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    alert.get("type") or alert.get("alert_type") or "unknown",
                    alert.get("message") or "",
                    alert.get("level") or "INFO",
                    alert.get("source") or "unknown",
                    alert.get("category") or "general",
                    str(alert.get("metadata", {})),
                    1 if alert.get("acknowledged") else 0,
                    ts,
                ),
            )
            conn.commit()
            return str(cur.lastrowid)
        finally:
            self._return_connection(conn)

    def get_alerts(
        self,
        limit: int = 100,
        level: str | None = None,
        acknowledged: bool | None = None,
    ) -> list[dict[str, Any]]:
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            query = "SELECT id, timestamp, type, message, level, source, category, metadata, acknowledged, acknowledged_at, created_at FROM alerts"
            params = []
            conditions = []
            if level:
                conditions.append("level = ?")
                params.append(level)
            if acknowledged is not None:
                conditions.append("acknowledged = ?")
                params.append(1 if acknowledged else 0)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            rows = cur.execute(query, tuple(params)).fetchall()
            return [
                {
                    "id": row[0],
                    "timestamp": row[1],
                    "type": row[2],
                    "message": row[3],
                    "level": row[4],
                    "source": row[5],
                    "category": row[6],
                    "metadata": row[7],
                    "acknowledged": bool(row[8]),
                    "acknowledged_at": row[9],
                    "created_at": row[10],
                }
                for row in rows
            ]
        finally:
            self._return_connection(conn)

    def acknowledge_alert(self, alert_id: str) -> bool:
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE alerts SET acknowledged = 1, acknowledged_at = datetime('now') WHERE id = ?",
                (alert_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            self._return_connection(conn)

    def delete_alert(self, alert_id: str) -> bool:
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            self._return_connection(conn)

    def repair_database(self, db_path: str | None = None) -> dict[str, Any]:
        """Repair a corrupted database by quarantining it and re-initializing."""
        path = db_path or self.db_path
        if not os.path.exists(path):
            return {
                "status": "no_file",
                "message": "no repair needed",
                "quarantined_files": [],
            }
        try:
            with open(path, "rb") as f:
                header = f.read(16)
            if header != b"SQLite format 3\x00":
                logger.warning(
                    f"[Database] File at {path} is not a valid SQLite database (bad header). Quarantining."
                )
                quarantined = self._recover_corrupted_db(
                    sqlite3.DatabaseError("Not a valid SQLite database file")
                )
                self.init_db()
                return {
                    "status": "repaired",
                    "message": "repaired: corrupt database quarantined and re-initialized",
                    "quarantined_files": quarantined,
                }
        except Exception as e:
            logger.warning(f"[Database] Repair cleanup error: {e}")
        conn = None
        try:
            conn = sqlite3.connect(path, timeout=30.0)
            cur = conn.cursor()
            rows = cur.execute("PRAGMA integrity_check").fetchall()
            conn.close()
            is_ok = len(rows) == 1 and str(rows[0][0]).strip().lower() == "ok"
            if is_ok:
                return {
                    "status": "healthy",
                    "message": "no repair needed",
                    "quarantined_files": [],
                }
            logger.warning(
                f"[Database] Integrity check failed on {path}: {rows}. Quarantining and rebuilding."
            )
            quarantined = self._recover_corrupted_db(
                sqlite3.DatabaseError(f"Integrity check failed: {rows}")
            )
            self.init_db()
            return {
                "status": "repaired",
                "message": "repaired: corrupt database quarantined and re-initialized",
                "quarantined_files": quarantined,
            }
        except (sqlite3.DatabaseError, sqlite3.OperationalError) as db_err:
            if conn:
                try:
                    conn.close()
                except Exception as e:
                    logger.warning(f"[Database] Could not close connection: {e}")
            conn = None
            logger.error(f"[Database] repair_database failed: {db_err}")
            try:
                if os.path.exists(path):
                    quarantined = self._recover_corrupted_db(db_err)
                    self.init_db()
                    return {
                        "status": "repaired",
                        "message": "repaired: corrupt database quarantined and re-initialized",
                        "quarantined_files": quarantined,
                    }
            except Exception as e:
                logger.error(f"[Database] repair_database failed: {e}")
                return {
                    "status": "error",
                    "message": "repair failed",
                    "quarantined_files": [],
                }

    def get_connection(self) -> sqlite3.Connection:
        """Get a database connection from the pool (ema_vwap-10tl).

        Creates a new connection if pool is exhausted, reuses pooled connections
        when available. Connections are configured with WAL-optimized PRAGMAs.
        """
        # Try to get a connection from the pool
        conn = None
        with self._pool_lock:
            if self._pool:
                conn = self._pool.pop()

        if conn:
            try:
                # Verify connection is still valid
                conn.execute("SELECT 1;")
                return conn
            except sqlite3.DatabaseError:
                # Connection stale, create new one
                try:
                    conn.close()
                except Exception as e:
                    logger.warning(f"[Database] Could not close stale connection: {e}")
                conn = None

        # Create new connection
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA cache_size = -64000;")  # 64MB memory page cache
            conn.execute("PRAGMA temp_store = MEMORY;")
            return conn
        except sqlite3.DatabaseError as e:
            if conn:
                try:
                    conn.close()
                except Exception as e:
                    logger.warning(f"[CandleDatabase] Could not close connection: {e}")
            if self._is_corruption_error(e):
                self._recover_corrupted_db(e)
                new_conn = sqlite3.connect(self.db_path, timeout=30.0)
                new_conn.execute("PRAGMA synchronous = NORMAL;")
                new_conn.execute("PRAGMA cache_size = -64000;")
                new_conn.execute("PRAGMA temp_store = MEMORY;")
                return new_conn
            raise

    def _return_connection(self, conn: sqlite3.Connection) -> None:
        """Return a connection to the pool for reuse (ema_vwap-10tl)."""
        if len(self._pool) >= self._pool_size:
            try:
                conn.close()
            except Exception as e:
                logger.warning(f"[Database] Could not close pooled connection: {e}")
        else:
            with self._pool_lock:
                self._pool.append(conn)

    @retry_on_db_lock()
    def init_db(self) -> None:
        """Initialize tables, WAL mode, and indexes.

        Only creates tables belonging to ``self.domain`` (see DOMAIN_TABLES).
        When domain is None (legacy/tests) all tables are created.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            _tables = DOMAIN_TABLES.get(self.domain, DOMAIN_TABLES[None])
            is_default_db = os.path.abspath(self.db_path) == os.path.abspath(DB_PATH)
            in_docker = (
                os.path.exists("/.dockerenv") or os.environ.get("IN_DOCKER") == "1"
            )
            # The default DB (candles.db) is shared with Docker via volume mounts (./data:/app/data).
            # On Windows hosts, SQLite WAL mode uses POSIX shared memory (-shm) which fails
            # over VirtioFS/9p volume mounts with 'unable to open database file'.
            # Therefore, the default DB must stay in DELETE mode unless explicitly overridden.
            # Validate journal mode against allowlist to prevent SQL injection
            ALLOWED_JOURNAL_MODES = {
                "DELETE",
                "WAL",
                "MEMORY",
                "OFF",
                "PERSIST",
                "TRUNCATE",
            }
            if "DB_JOURNAL_MODE" in os.environ:
                mode = os.environ["DB_JOURNAL_MODE"].strip().upper()
                if mode not in ALLOWED_JOURNAL_MODES:
                    logger.warning(
                        f"[CandleDatabase] Invalid journal_mode '{mode}', falling back to DELETE"
                    )
                    mode = "DELETE"
            elif in_docker or is_default_db:
                mode = "DELETE"
            else:
                mode = "WAL"
            # PRAGMA doesn't support parameterized queries; mode is validated against allowlist
            # Mode is validated against ALLOWED_JOURNAL_MODES above; safe to use in f-string
            cursor.execute(f"PRAGMA journal_mode = {mode};")

            # -- Candles domain ------------------------------------------------
            if "candles" in _tables:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS candles (
                        exchange TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        open REAL NOT NULL,
                        high REAL NOT NULL,
                        low REAL NOT NULL,
                        close REAL NOT NULL,
                        volume REAL NOT NULL,
                        PRIMARY KEY (exchange, symbol, timeframe, timestamp)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_candles_lookup 
                    ON candles (exchange, symbol, timeframe, timestamp)
                    """
                )

            # -- Batch results domain ------------------------------------------
            if "batch_results" in _tables:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS batch_results (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        exchange TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        strategy_mode TEXT NOT NULL,
                        target_metric TEXT NOT NULL,
                        best_value REAL NOT NULL,
                        trade_count INTEGER NOT NULL,
                        sharpe_ratio REAL,
                        calmar_ratio REAL,
                        win_rate REAL,
                        max_drawdown_pct REAL,
                        total_return_pct REAL,
                        best_params TEXT NOT NULL,
                        run_timestamp TEXT NOT NULL,
                        favorite INTEGER NOT NULL DEFAULT 0,
                        deployed INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                columns = {
                    row[1]
                    for row in cursor.execute(
                        "PRAGMA table_info(batch_results)"
                    ).fetchall()
                }
                if "favorite" not in columns:
                    cursor.execute(
                        "ALTER TABLE batch_results ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0"
                    )
                if "validation_status" not in columns:
                    cursor.execute(
                        "ALTER TABLE batch_results ADD COLUMN validation_status TEXT"
                    )
                if "validation_json" not in columns:
                    cursor.execute(
                        "ALTER TABLE batch_results ADD COLUMN validation_json TEXT"
                    )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_batch_results_metric
                    ON batch_results (target_metric, best_value DESC)
                    """
                )
                # Compound index for asset-specific batch result queries (ema_vwap-453f)
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_batch_results_asset
                    ON batch_results (exchange, symbol, timeframe, target_metric)
                    """
                )

            # -- Paper trading domain ------------------------------------------
            if "paper_ledger" in _tables:
                # Paper Trading Ledger Schema (Legacy/Direct)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_ledger (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        exchange TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        action TEXT NOT NULL,
                        price REAL NOT NULL,
                        quantity REAL NOT NULL,
                        total_usd REAL NOT NULL,
                        timestamp TEXT NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_paper_ledger_lookup
                    ON paper_ledger (exchange, symbol, timestamp)
                    """
                )
                # Compound index for trade history queries by asset + action (ema_vwap-453f)
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_paper_ledger_trades
                    ON paper_ledger (exchange, symbol, timeframe, action, timestamp)
                    """
                )

            if "paper_profiles" in _tables:
                # Paper Trading Per-Asset Optuna Profiles Schema
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_profiles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        exchange TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        strategy_mode TEXT NOT NULL,
                        target_metric TEXT NOT NULL,
                        params_json TEXT NOT NULL,
                        optuna_score REAL NOT NULL DEFAULT 0.0,
                        is_active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        deployed INTEGER NOT NULL DEFAULT 0,
                        deployed_at TEXT,
                        validation_report_json TEXT,
                        UNIQUE(exchange, symbol, timeframe, strategy_mode)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_paper_profiles_lookup
                    ON paper_profiles (exchange, symbol, is_active)
                    """
                )
                # Compound index for finding best active profile by asset (ema_vwap-453f)
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_paper_profiles_best
                    ON paper_profiles (exchange, symbol, timeframe, is_active, optuna_score DESC)
                    """
                )

                # Migration: add deployed and deployed_at columns to paper_profiles
                pp_cols = {
                    row[1]
                    for row in cursor.execute(
                        "PRAGMA table_info(paper_profiles)"
                    ).fetchall()
                }
                if "deployed" not in pp_cols:
                    logger.info(
                        "[CandleDatabase] Migrating paper_profiles: adding deployed and deployed_at columns"
                    )
                    cursor.execute(
                        "ALTER TABLE paper_profiles ADD COLUMN deployed INTEGER NOT NULL DEFAULT 0"
                    )
                    cursor.execute(
                        "ALTER TABLE paper_profiles ADD COLUMN deployed_at TEXT"
                    )
                    logger.info(
                        "[CandleDatabase] paper_profiles deployed migration complete"
                    )

                # Migration: add validation_report_json column to paper_profiles
                pp_cols = {
                    row[1]
                    for row in cursor.execute(
                        "PRAGMA table_info(paper_profiles)"
                    ).fetchall()
                }
                if "validation_report_json" not in pp_cols:
                    logger.info(
                        "[CandleDatabase] Migrating paper_profiles: adding validation_report_json column"
                    )
                    cursor.execute(
                        "ALTER TABLE paper_profiles ADD COLUMN validation_report_json TEXT"
                    )
                    logger.info(
                        "[CandleDatabase] paper_profiles validation_report_json migration complete"
                    )

                # Migration: add UNIQUE constraint to paper_profiles if missing.
                # The table may have been created before the constraint was added to
                # the DDL above.  CREATE TABLE IF NOT EXISTS is a no-op on existing
                # tables, so the constraint was never retroactively applied.  SQLite
                # does not support ALTER TABLE … ADD CONSTRAINT, so we rebuild the
                # table using the standard rename→recreate→copy→drop procedure.
                pp_ddl = cursor.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='paper_profiles'"
                ).fetchone()
                if pp_ddl and "UNIQUE" not in pp_ddl[0].upper():
                    logger.info(
                        "[CandleDatabase] Migrating paper_profiles: adding UNIQUE(exchange, symbol, timeframe, strategy_mode)"
                    )
                    cursor.execute(
                        "ALTER TABLE paper_profiles RENAME TO _paper_profiles_old"
                    )
                    cursor.execute(
                        """
                        CREATE TABLE paper_profiles (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            exchange TEXT NOT NULL,
                            symbol TEXT NOT NULL,
                            timeframe TEXT NOT NULL,
                            strategy_mode TEXT NOT NULL,
                            target_metric TEXT NOT NULL,
                            params_json TEXT NOT NULL,
                            optuna_score REAL NOT NULL DEFAULT 0.0,
                            is_active INTEGER NOT NULL DEFAULT 1,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            UNIQUE(exchange, symbol, timeframe, strategy_mode)
                        )
                        """
                    )
                    cursor.execute(
                        """
                        INSERT INTO paper_profiles
                            (id, exchange, symbol, timeframe, strategy_mode,
                             target_metric, params_json, optuna_score, is_active,
                             created_at, updated_at)
                        SELECT id, exchange, symbol, timeframe, strategy_mode,
                               target_metric, params_json,
                               COALESCE(optuna_score, 0.0),
                               is_active, created_at, updated_at
                        FROM _paper_profiles_old
                        """
                    )
                    cursor.execute("DROP TABLE _paper_profiles_old")
                    # Recreate the index on the new table
                    cursor.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_paper_profiles_lookup
                        ON paper_profiles (exchange, symbol, is_active)
                        """
                    )
                    logger.info("[CandleDatabase] paper_profiles migration complete")

            if "paper_positions" in _tables:
                # Paper Trading Open Positions Schema
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_positions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        exchange TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        account_id TEXT NOT NULL DEFAULT 'default',
                        side TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        current_price REAL NOT NULL,
                        quantity REAL NOT NULL,
                        cost_basis REAL NOT NULL,
                        stop_loss REAL,
                        take_profit REAL,
                        unrealized_pnl REAL NOT NULL DEFAULT 0.0,
                        unrealized_pnl_pct REAL NOT NULL DEFAULT 0.0,
                        entry_time TEXT NOT NULL,
                        updated_time TEXT NOT NULL,
                        last_bar_time TEXT,
                        metadata TEXT NOT NULL DEFAULT '{}',
                        UNIQUE(exchange, symbol, account_id)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_paper_positions_sym
                    ON paper_positions (exchange, symbol, account_id)
                    """
                )

                # Migration: add account_id and last_bar_time columns, rebuild unique constraint
                pp_cols = {
                    row[1]
                    for row in cursor.execute(
                        "PRAGMA table_info(paper_positions)"
                    ).fetchall()
                }
                # Rebuild when account_id is missing OR the live UNIQUE constraint is the
                # legacy (exchange, symbol) form. The upsert targets ON CONFLICT(exchange,
                # symbol, account_id); against a legacy table SQLite raises an error so
                # BUYs hit the ledger but positions never appear in the table.
                pp_ddl_row = cursor.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='paper_positions'"
                ).fetchone()
                pp_ddl = (pp_ddl_row[0] or "") if pp_ddl_row else ""
                needs_rebuild = ("account_id" not in pp_cols) or (
                    "UNIQUE(exchange, symbol, account_id)" not in pp_ddl
                )
                if needs_rebuild:
                    logger.info(
                        "[CandleDatabase] Migrating paper_positions: adding account_id and last_bar_time"
                    )
                    if "account_id" not in pp_cols:
                        cursor.execute(
                            "ALTER TABLE paper_positions ADD COLUMN account_id TEXT NOT NULL DEFAULT 'default'"
                        )
                    if "last_bar_time" not in pp_cols:
                        cursor.execute(
                            "ALTER TABLE paper_positions ADD COLUMN last_bar_time TEXT"
                        )
                    # Rebuild table to change unique constraint (SQLite doesn't support ALTER TABLE DROP CONSTRAINT)
                    cursor.execute(
                        "ALTER TABLE paper_positions RENAME TO _paper_positions_old"
                    )
                    cursor.execute(
                        """
                        CREATE TABLE paper_positions (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            exchange TEXT NOT NULL,
                            symbol TEXT NOT NULL,
                            account_id TEXT NOT NULL DEFAULT 'default',
                            side TEXT NOT NULL,
                            entry_price REAL NOT NULL,
                            current_price REAL NOT NULL,
                            quantity REAL NOT NULL,
                            cost_basis REAL NOT NULL,
                            stop_loss REAL,
                            take_profit REAL,
                            unrealized_pnl REAL NOT NULL DEFAULT 0.0,
                            unrealized_pnl_pct REAL NOT NULL DEFAULT 0.0,
                            entry_time TEXT NOT NULL,
                            updated_time TEXT NOT NULL,
                            last_bar_time TEXT,
                            metadata TEXT NOT NULL DEFAULT '{}',
                            UNIQUE(exchange, symbol, account_id)
                        )
                        """
                    )
                    cursor.execute(
                        """
                        INSERT INTO paper_positions
                            (id, exchange, symbol, account_id, side, entry_price, current_price, quantity,
                             cost_basis, stop_loss, take_profit, unrealized_pnl, unrealized_pnl_pct,
                             entry_time, updated_time, last_bar_time, metadata)
                        SELECT id, exchange, symbol,
                               COALESCE(account_id, 'default'),
                               side, entry_price, current_price, quantity,
                               cost_basis, stop_loss, take_profit, unrealized_pnl, unrealized_pnl_pct,
                               entry_time, updated_time, last_bar_time, metadata
                        FROM _paper_positions_old
                        """
                    )
                    cursor.execute("DROP TABLE _paper_positions_old")
                    cursor.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_paper_positions_sym
                        ON paper_positions (exchange, symbol, account_id)
                        """
                    )
                    logger.info("[CandleDatabase] paper_positions migration complete")

            if "paper_ledger_transactions" in _tables:
                # Paper Trading Double-Entry Ledger Transactions Schema
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_ledger_transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        transaction_id TEXT NOT NULL UNIQUE,
                        account_id TEXT NOT NULL DEFAULT 'default',
                        exchange TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        type TEXT NOT NULL,
                        amount REAL NOT NULL,
                        asset_qty REAL NOT NULL DEFAULT 0.0,
                        price REAL NOT NULL DEFAULT 0.0,
                        fee REAL NOT NULL DEFAULT 0.0,
                        balance_after REAL NOT NULL,
                        timestamp TEXT NOT NULL,
                        notes TEXT NOT NULL DEFAULT '',
                        metadata TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_paper_ledger_tx_time
                    ON paper_ledger_transactions (account_id, timestamp DESC)
                    """
                )

            if "paper_trade_history" in _tables:
                # Paper Trading Completed Round-Trip Trade History Schema
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_trade_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trade_id TEXT NOT NULL UNIQUE,
                        exchange TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        side TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        exit_price REAL NOT NULL,
                        quantity REAL NOT NULL,
                        entry_time TEXT NOT NULL,
                        exit_time TEXT NOT NULL,
                        holding_period_bars INTEGER NOT NULL DEFAULT 0,
                        realized_pnl REAL NOT NULL,
                        realized_pnl_pct REAL NOT NULL,
                        fees REAL NOT NULL DEFAULT 0.0,
                        exit_reason TEXT NOT NULL,
                        params_json TEXT NOT NULL DEFAULT '{}',
                        metadata TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_paper_trade_hist_sym
                    ON paper_trade_history (exchange, symbol, exit_time DESC)
                    """
                )

            if "optuna_profiles" in _tables:
                # Optuna Profiles Schema (Legacy / General)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS optuna_profiles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        exchange TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        strategy_mode TEXT NOT NULL,
                        target_metric TEXT NOT NULL,
                        best_params TEXT NOT NULL,
                        best_value REAL NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(exchange, symbol, timeframe, strategy_mode, target_metric)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_optuna_profiles_lookup
                    ON optuna_profiles (exchange, symbol, timeframe)
                    """
                )

                # Migration: rebuild optuna_profiles if live schema has stale columns.
                # The live table may have an older column set (params_json, score,
                # profile_name, etc.) instead of the current (best_params, best_value).
                op_cols = {
                    row[1]
                    for row in cursor.execute(
                        "PRAGMA table_info(optuna_profiles)"
                    ).fetchall()
                }
                if "best_params" not in op_cols:
                    logger.info(
                        "[CandleDatabase] Migrating optuna_profiles: rebuilding to match code DDL"
                    )
                    cursor.execute("DROP TABLE optuna_profiles")
                    cursor.execute(
                        """
                        CREATE TABLE optuna_profiles (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            exchange TEXT NOT NULL,
                            symbol TEXT NOT NULL,
                            timeframe TEXT NOT NULL,
                            strategy_mode TEXT NOT NULL,
                            target_metric TEXT NOT NULL,
                            best_params TEXT NOT NULL,
                            best_value REAL NOT NULL,
                            updated_at TEXT NOT NULL,
                            UNIQUE(exchange, symbol, timeframe, strategy_mode, target_metric)
                        )
                        """
                    )
                    cursor.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_optuna_profiles_lookup
                        ON optuna_profiles (exchange, symbol, timeframe)
                        """
                    )
                    logger.info("[CandleDatabase] optuna_profiles migration complete")

            # -- System domain -------------------------------------------------
            if "portfolio_balances" in _tables:
                # Portfolio Balances Schema
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS portfolio_balances (
                        currency TEXT PRIMARY KEY,
                        total REAL NOT NULL,
                        available REAL NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )

                # Migration: rebuild portfolio_balances if live schema uses the old
                # account-level JSON blob design (account_id, balances_json) instead
                # of the current per-currency row design (currency, total, available).
                pb_cols = {
                    row[1]
                    for row in cursor.execute(
                        "PRAGMA table_info(portfolio_balances)"
                    ).fetchall()
                }
                if "currency" not in pb_cols:
                    logger.info(
                        "[CandleDatabase] Migrating portfolio_balances: rebuilding to match code DDL"
                    )
                    cursor.execute("DROP TABLE portfolio_balances")
                    cursor.execute(
                        """
                        CREATE TABLE portfolio_balances (
                            currency TEXT PRIMARY KEY,
                            total REAL NOT NULL,
                            available REAL NOT NULL,
                            updated_at TEXT NOT NULL
                        )
                        """
                    )
                    logger.info(
                        "[CandleDatabase] portfolio_balances migration complete"
                    )

            if "alerts" in _tables:
                # Alerts Schema (ema_vwap-6ic)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alerts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        type TEXT NOT NULL,
                        message TEXT NOT NULL,
                        level TEXT NOT NULL,
                        source TEXT NOT NULL,
                        category TEXT NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}',
                        acknowledged INTEGER NOT NULL DEFAULT 0,
                        acknowledged_at TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )

                # Migration: legacy alerts table (rename alert_type -> type, add missing columns)
                # Must run BEFORE index creation since indexes reference created_at
                alert_cols = {
                    row[1]
                    for row in cursor.execute("PRAGMA table_info(alerts)").fetchall()
                }
                if "type" not in alert_cols and "alert_type" in alert_cols:
                    logger.info(
                        "[CandleDatabase] Migrating alerts: adding missing columns"
                    )
                    cursor.execute(
                        "ALTER TABLE alerts RENAME COLUMN alert_type TO type"
                    )
                    if "source" not in alert_cols:
                        cursor.execute(
                            "ALTER TABLE alerts ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown'"
                        )
                    if "category" not in alert_cols:
                        cursor.execute(
                            "ALTER TABLE alerts ADD COLUMN category TEXT NOT NULL DEFAULT 'general'"
                        )
                    if "acknowledged_at" not in alert_cols:
                        cursor.execute(
                            "ALTER TABLE alerts ADD COLUMN acknowledged_at TEXT"
                        )
                    if "created_at" not in alert_cols:
                        cursor.execute(
                            "ALTER TABLE alerts ADD COLUMN created_at TEXT NOT NULL DEFAULT ''"
                        )
                        # Populate created_at from timestamp for existing rows
                        cursor.execute(
                            "UPDATE alerts SET created_at = timestamp WHERE created_at = ''"
                        )
                    logger.info("[CandleDatabase] alerts migration complete")

                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_alerts_created_at
                    ON alerts (created_at DESC)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_alerts_type
                    ON alerts (type)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_alerts_level
                    ON alerts (level)
                    """
                )

            if "background_tasks" in _tables:
                # Background Tasks Schema
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS background_tasks (
                        task_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        progress_pct REAL NOT NULL DEFAULT 0.0,
                        step_description TEXT NOT NULL DEFAULT '',
                        start_time TEXT NOT NULL,
                        end_time TEXT,
                        error TEXT,
                        result_json TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_background_tasks_created
                    ON background_tasks (created_at DESC)
                    """
                )

            if "settings" in _tables:
                # System Settings Schema
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        is_secret INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
                    )
                    """
                )

            conn.commit()
        conn.close()

    @retry_on_db_lock()
    def save_candles(
        self, exchange: str, symbol: str, timeframe: str, df: pd.DataFrame
    ) -> int:
        """
        Bulk upsert candles into SQLite database (ema_vwap-5bbj).
        Uses chunked executemany to avoid memory spikes on large DataFrames.
        Returns number of rows saved/updated.
        """
        if df.empty:
            return 0

        exchange_clean = exchange.lower()
        symbol_clean = normalize_symbol_key(symbol)
        timeframe_clean = timeframe.lower()

        # Vectorized column preparation (avoid iterrows Python loop)
        ts_col = df["timestamp"].astype(str).to_numpy()
        open_col = df["open"].astype(float).to_numpy()
        high_col = df["high"].astype(float).to_numpy()
        low_col = df["low"].astype(float).to_numpy()
        close_col = df["close"].astype(float).to_numpy()
        volume_col = df["volume"].astype(float).to_numpy()

        # Build rows as list of tuples (executemany requires sequence of sequences)
        rows = list(
            zip(
                [exchange_clean] * len(df),
                [symbol_clean] * len(df),
                [timeframe_clean] * len(df),
                ts_col,
                open_col,
                high_col,
                low_col,
                close_col,
                volume_col,
            )
        )

        CHUNK_SIZE = 5000  # SQLite parameter limit ~32k / 9 cols ≈ 3.5k per chunk
        total = 0
        with self.get_connection() as conn:
            cursor = conn.cursor()
            for i in range(0, len(rows), CHUNK_SIZE):
                chunk = rows[i : i + CHUNK_SIZE]
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO candles 
                    (exchange, symbol, timeframe, timestamp, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    chunk,
                )
                total += len(chunk)
            conn.commit()
        return total

    def load_candles(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """
        Query cached candles from SQLite database.
        Returns sorted pandas DataFrame with OHLCV data.
        """
        exchange_clean = exchange.lower()
        symbol_clean = normalize_symbol_key(symbol)
        timeframe_clean = timeframe.lower()

        query = """
                SELECT timestamp, open, high, low, close, volume
                FROM candles
                WHERE exchange = ? AND symbol = ? AND timeframe = ?
            """
        params = [exchange_clean, symbol_clean, timeframe_clean]

        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date)
        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date)

        query += " ORDER BY timestamp ASC"

        # Retry on malformed database errors with automatic recovery
        for attempt in range(2):
            try:
                with self.get_connection() as conn:
                    df = pd.read_sql_query(query, conn, params=params)
            except (sqlite3.DatabaseError, pd.errors.DatabaseError) as e:
                # Check if the underlying cause is a corruption error
                cause = getattr(e, "__cause__", e)
                if self._is_corruption_error(cause) and attempt == 0:
                    logger.warning(
                        f"[Database] Malformed database detected in load_candles: {e}. Attempting recovery..."
                    )
                    self._recover_corrupted_db(cause)
                    continue
                raise

        if df.empty:
            return df

        try:
            df["timestamp"] = pd.to_datetime(
                df["timestamp"], utc=True, format="ISO8601"
            )
        except Exception:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        if limit and len(df) > limit:
            df = df.tail(limit).reset_index(drop=True)

        return df

    def get_candle_count(self, exchange: str, symbol: str, timeframe: str) -> int:
        """Get total cached candle count for given symbol/timeframe."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) FROM candles 
                WHERE exchange = ? AND symbol = ? AND timeframe = ?
                """,
                (exchange.lower(), normalize_symbol_key(symbol), timeframe.lower()),
            )
            count = cursor.fetchone()[0]
            return count

    @retry_on_db_lock()
    def save_batch_result(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        strategy_mode: str,
        target_metric: str,
        best_value: float,
        trade_count: int,
        sharpe_ratio: float,
        calmar_ratio: float,
        win_rate: float,
        max_drawdown_pct: float,
        total_return_pct: float,
        best_params: str,
        run_timestamp: str,
    ) -> int:
        """Upsert a single batch sweep result into the leaderboard table."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT favorite, validation_status, validation_json FROM batch_results
                WHERE exchange = ? AND symbol = ? AND timeframe = ? AND target_metric = ?
                """,
                (
                    exchange.lower(),
                    normalize_symbol_key(symbol),
                    timeframe.lower(),
                    target_metric,
                ),
            )
            existing = cursor.fetchone()
            favorite = existing[0] if existing else 0
            # Carry forward any previously-run validation so a re-sweep doesn't erase it
            preserved_val_status = existing[1] if existing else None
            preserved_val_json = existing[2] if existing else None

            # Remove any existing entry for the same combo + metric
            cursor.execute(
                """
                DELETE FROM batch_results
                WHERE exchange = ? AND symbol = ? AND timeframe = ? AND target_metric = ?
                """,
                (
                    exchange.lower(),
                    normalize_symbol_key(symbol),
                    timeframe.lower(),
                    target_metric,
                ),
            )
            cursor.execute(
                """
                INSERT INTO batch_results
                (exchange, symbol, timeframe, strategy_mode, target_metric, best_value,
                 trade_count, sharpe_ratio, calmar_ratio, win_rate, max_drawdown_pct,
                 total_return_pct, best_params, run_timestamp, favorite,
                 validation_status, validation_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exchange.lower(),
                    normalize_symbol_key(symbol),
                    timeframe.lower(),
                    strategy_mode,
                    target_metric,
                    best_value,
                    trade_count,
                    sharpe_ratio,
                    calmar_ratio,
                    win_rate,
                    max_drawdown_pct,
                    total_return_pct,
                    best_params,
                    run_timestamp,
                    favorite,
                    preserved_val_status,
                    preserved_val_json,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def load_batch_results(
        self, target_metric: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return leaderboard rows sorted by best_value descending."""
        query = "SELECT * FROM batch_results"
        params: list[Any] = []
        if target_metric:
            query += " WHERE target_metric = ?"
            params.append(target_metric)
        query += " ORDER BY best_value DESC LIMIT ?"
        params.append(limit)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_batch_result(self, result_id: int) -> dict[str, Any] | None:
        """Get a single batch result by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM batch_results WHERE id = ?", (result_id,))
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))

    @retry_on_db_lock()
    def delete_batch_results(self, result_ids: list[int]) -> int:
        """Delete selected batch sweep results by their database IDs."""
        if not result_ids:
            return 0

        placeholders = ", ".join("?" for _ in result_ids)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"DELETE FROM batch_results WHERE id IN ({placeholders})",
                result_ids,
            )
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    @retry_on_db_lock()
    def set_batch_result_favorite(self, result_id: int, favorite: bool) -> bool:
        """Set the favorite flag for one batch sweep result."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE batch_results SET favorite = ? WHERE id = ?",
                (int(favorite), result_id),
            )
            updated = cursor.rowcount > 0
            conn.commit()
            return updated

    @retry_on_db_lock()
    def set_batch_result_deployed(self, result_id: int, deployed: bool) -> bool:
        """Set the deployed flag for one batch sweep result."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE batch_results SET deployed = ? WHERE id = ?",
                (int(deployed), result_id),
            )
            updated = cursor.rowcount > 0
            conn.commit()
            return updated

    @retry_on_db_lock()
    def update_batch_result_validation(
        self, result_id: int, status: str, report_json: str
    ) -> bool:
        """Update the validation status and details for a batch result row."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE batch_results SET validation_status = ?, validation_json = ? WHERE id = ?",
                (status, report_json, result_id),
            )
            updated = cursor.rowcount > 0
            conn.commit()
            return updated

    @retry_on_db_lock()
    def purge_batch_results(
        self, older_than_days: int, target_metric: str | None = None
    ) -> int:
        """Delete batch sweep results older than the requested number of days."""
        from datetime import datetime, timedelta, timezone

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=older_than_days)
        ).isoformat()
        query = "DELETE FROM batch_results WHERE run_timestamp < ?"
        params: list[Any] = [cutoff]
        if target_metric:
            query += " AND target_metric = ?"
            params.append(target_metric)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    @retry_on_db_lock()
    def clear_cache(
        self, exchange: str | None = None, symbol: str | None = None
    ) -> int:
        """Purge cached candles from SQLite database."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if exchange and symbol:
                cursor.execute(
                    "DELETE FROM candles WHERE exchange = ? AND symbol = ?",
                    (exchange.lower(), normalize_symbol_key(symbol)),
                )
            elif exchange:
                cursor.execute(
                    "DELETE FROM candles WHERE exchange = ?", (exchange.lower(),)
                )
            else:
                cursor.execute("DELETE FROM candles")
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    # -------------------------------------------------------------------------
    # Paper Trading Ledger Methods (Legacy/Direct)
    # -------------------------------------------------------------------------

    @retry_on_db_lock()
    def save_paper_trade(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        action: str,
        price: float,
        quantity: float,
        total_usd: float,
        timestamp: str,
        metadata: str = "{}",
    ) -> int:
        """Insert a paper trade order execution record into the ledger."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO paper_ledger
                (exchange, symbol, timeframe, action, price, quantity, total_usd, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exchange.lower(),
                    normalize_symbol_key(symbol),
                    timeframe.lower(),
                    action.upper(),
                    float(price),
                    float(quantity),
                    float(total_usd),
                    str(timestamp),
                    str(metadata),
                ),
            )
            trade_id = cursor.lastrowid
            conn.commit()
            return trade_id

    def load_paper_trades(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch paper trade execution logs sorted by timestamp descending."""
        query = "SELECT * FROM paper_ledger"
        conditions = []
        params: list[Any] = []

        if exchange:
            conditions.append("exchange = ?")
            params.append(exchange.lower())
        if symbol:
            conditions.append("symbol = ?")
            params.append(normalize_symbol_key(symbol))

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Paper Trading Per-Asset Optuna Profile Registry Methods
    # -------------------------------------------------------------------------

    @retry_on_db_lock()
    def save_paper_profile(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        strategy_mode: str,
        target_metric: str,
        params_json: str,
        optuna_score: float = 0.0,
        is_active: bool = True,
        updated_at: str | None = None,
        deployed: bool = False,
        deployed_at: str | None = None,
        validation_report_json: str | None = None,
    ) -> int:
        """Upsert per-asset calibrated Optuna strategy parameters into paper_profiles."""
        from datetime import datetime, timezone

        now_iso = updated_at or datetime.now(timezone.utc).isoformat()
        # Auto-set deployed_at when deployed=True
        if deployed and deployed_at is None:
            deployed_at = now_iso
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO paper_profiles
                (exchange, symbol, timeframe, strategy_mode, target_metric, params_json, optuna_score, is_active, created_at, updated_at, deployed, deployed_at, validation_report_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange, symbol, timeframe, strategy_mode) DO UPDATE SET
                    target_metric = excluded.target_metric,
                    params_json = excluded.params_json,
                    optuna_score = excluded.optuna_score,
                    is_active = excluded.is_active,
                    updated_at = excluded.updated_at,
                    deployed = excluded.deployed,
                    deployed_at = excluded.deployed_at,
                    validation_report_json = excluded.validation_report_json
                """,
                (
                    exchange.lower(),
                    normalize_symbol_key(symbol),
                    timeframe.lower(),
                    strategy_mode.lower(),
                    target_metric,
                    params_json,
                    float(optuna_score),
                    int(is_active),
                    now_iso,
                    now_iso,
                    int(deployed),
                    deployed_at,
                    validation_report_json,
                ),
            )
            profile_id = cursor.lastrowid
            conn.commit()
            return profile_id

    def get_paper_profile(
        self,
        exchange: str,
        symbol: str,
        timeframe: str | None = None,
        strategy_mode: str | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve active paper profile for a specific asset and timeframe."""
        query = "SELECT * FROM paper_profiles WHERE exchange = ? AND symbol = ?"
        params: list[Any] = [exchange.lower(), normalize_symbol_key(symbol)]

        if timeframe:
            query += " AND timeframe = ?"
            params.append(timeframe.lower())
        if strategy_mode:
            query += " AND strategy_mode = ?"
            params.append(strategy_mode.lower())

        query += " ORDER BY is_active DESC, updated_at DESC LIMIT 1"

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))

    @retry_on_db_lock()
    def list_paper_profiles(
        self,
        exchange: str | None = None,
        active_only: bool = False,
        deployed_only: bool = False,
    ) -> list[dict[str, Any]]:
        """List all paper trading profiles with optional exchange, active, and deployed filtering."""
        query = "SELECT * FROM paper_profiles"
        conditions = []
        params: list[Any] = []

        if exchange:
            conditions.append("exchange = ?")
            params.append(exchange.lower())
        if active_only:
            conditions.append("is_active = 1")
        if deployed_only:
            conditions.append("deployed = 1")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY updated_at DESC"

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    @retry_on_db_lock()
    def delete_paper_profile(self, profile_id: int) -> bool:
        """Delete a paper profile by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM paper_profiles WHERE id = ?", (profile_id,))
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted

    @retry_on_db_lock()
    def set_paper_profile_active(
        self, profile_id: int, is_active: bool, deployed: bool | None = None
    ) -> bool:
        """Toggle active state of a paper profile, optionally set deployed flag."""
        from datetime import datetime, timezone

        with self.get_connection() as conn:
            cursor = conn.cursor()
            if deployed is not None:
                now_iso = datetime.now(timezone.utc).isoformat() if deployed else None
                cursor.execute(
                    "UPDATE paper_profiles SET is_active = ?, deployed = ?, deployed_at = ? WHERE id = ?",
                    (int(is_active), int(deployed), now_iso, profile_id),
                )
            else:
                cursor.execute(
                    "UPDATE paper_profiles SET is_active = ? WHERE id = ?",
                    (int(is_active), profile_id),
                )
            updated = cursor.rowcount > 0
            conn.commit()
            return updated

    @retry_on_db_lock()
    def update_paper_profile_trade_direction(
        self, profile_id: int, trade_direction: str
    ) -> bool:
        """Update trade_direction inside params_json of a paper profile."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT params_json FROM paper_profiles WHERE id = ?", (profile_id,)
            )
            row = cursor.fetchone()
            if not row:
                return False
            try:
                params = json.loads(row[0])
            except Exception:
                params = {}
            params["trade_direction"] = trade_direction
            cursor.execute(
                "UPDATE paper_profiles SET params_json = ? WHERE id = ?",
                (json.dumps(params), profile_id),
            )
            updated = cursor.rowcount > 0
            conn.commit()
            return updated

    # -------------------------------------------------------------------------
    # Paper Trading Open Positions Methods
    # -------------------------------------------------------------------------

    @retry_on_db_lock()
    def save_paper_position(
        self,
        exchange: str,
        symbol: str,
        side: str,
        entry_price: float,
        current_price: float,
        quantity: float,
        cost_basis: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        unrealized_pnl: float = 0.0,
        unrealized_pnl_pct: float = 0.0,
        entry_time: str | None = None,
        metadata: str = "{}",
        account_id: str = "default",
        last_bar_time: str | None = None,
    ) -> int:
        """Upsert an active paper position."""
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()
        entry_iso = entry_time or now_iso
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO paper_positions
                (exchange, symbol, account_id, side, entry_price, current_price, quantity, cost_basis, stop_loss, take_profit, unrealized_pnl, unrealized_pnl_pct, entry_time, updated_time, last_bar_time, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange, symbol, account_id) DO UPDATE SET
                    side = excluded.side,
                    entry_price = excluded.entry_price,
                    current_price = excluded.current_price,
                    quantity = excluded.quantity,
                    cost_basis = excluded.cost_basis,
                    stop_loss = excluded.stop_loss,
                    take_profit = excluded.take_profit,
                    unrealized_pnl = excluded.unrealized_pnl,
                    unrealized_pnl_pct = excluded.unrealized_pnl_pct,
                    updated_time = excluded.updated_time,
                    last_bar_time = excluded.last_bar_time,
                    metadata = excluded.metadata
                """,
                (
                    exchange.lower(),
                    normalize_symbol_key(symbol),
                    account_id,
                    side.upper(),
                    float(entry_price),
                    float(current_price),
                    float(quantity),
                    float(cost_basis),
                    float(stop_loss) if stop_loss is not None else None,
                    float(take_profit) if take_profit is not None else None,
                    float(unrealized_pnl),
                    float(unrealized_pnl_pct),
                    entry_iso,
                    now_iso,
                    last_bar_time,
                    str(metadata),
                ),
            )
            pos_id = cursor.lastrowid
            conn.commit()
            return pos_id

    def get_paper_position(
        self, exchange: str, symbol: str, account_id: str = "default"
    ) -> dict[str, Any] | None:
        """Retrieve open paper position for a given exchange, symbol, and account."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM paper_positions WHERE exchange = ? AND symbol = ? AND account_id = ?",
                (exchange.lower(), normalize_symbol_key(symbol), account_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))

    def list_paper_positions(
        self, exchange: str | None = None, account_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List all active open paper positions, optionally filtered by account."""
        query = "SELECT * FROM paper_positions"
        params: list[Any] = []
        conditions = []
        if exchange:
            conditions.append("exchange = ?")
            params.append(exchange.lower())
        if account_id:
            conditions.append("account_id = ?")
            params.append(account_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY updated_time DESC"

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    @retry_on_db_lock()
    def close_paper_position(
        self, exchange: str, symbol: str, account_id: str = "default"
    ) -> bool:
        """Remove/close an open paper position."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM paper_positions WHERE exchange = ? AND symbol = ? AND account_id = ?",
                (exchange.lower(), normalize_symbol_key(symbol), account_id),
            )
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted

    # -------------------------------------------------------------------------
    # Paper Trading Double-Entry Ledger Transactions Methods
    # -------------------------------------------------------------------------

    @retry_on_db_lock()
    def record_ledger_transaction(
        self,
        transaction_id: str,
        account_id: str,
        exchange: str,
        symbol: str,
        tx_type: str,
        amount: float,
        asset_qty: float = 0.0,
        price: float = 0.0,
        fee: float = 0.0,
        balance_after: float = 0.0,
        timestamp: str | None = None,
        notes: str = "",
        metadata: str = "{}",
    ) -> int:
        """Record a transaction in the double-entry paper ledger."""
        from datetime import datetime, timezone

        now_iso = timestamp or datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO paper_ledger_transactions
                (transaction_id, account_id, exchange, symbol, type, amount, asset_qty, price, fee, balance_after, timestamp, notes, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    account_id,
                    exchange.lower(),
                    normalize_symbol_key(symbol) if symbol else "",
                    tx_type.upper(),
                    float(amount),
                    float(asset_qty),
                    float(price),
                    float(fee),
                    float(balance_after),
                    now_iso,
                    str(notes),
                    str(metadata),
                ),
            )
            tx_row_id = cursor.lastrowid
            conn.commit()
            return tx_row_id

    def list_ledger_transactions(
        self,
        account_id: str = "default",
        exchange: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List transaction audit log for an account."""
        query = "SELECT * FROM paper_ledger_transactions WHERE account_id = ?"
        params: list[Any] = [account_id]

        if exchange:
            query += " AND exchange = ?"
            params.append(exchange.lower())
        if symbol:
            query += " AND symbol = ?"
            params.append(normalize_symbol_key(symbol))

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_account_balance(self, account_id: str = "default") -> float:
        """Get the latest cash balance for a paper trading account from the ledger."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT balance_after FROM paper_ledger_transactions
                WHERE account_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (account_id,),
            )
            row = cursor.fetchone()
            if row:
                return float(row[0])
            # If no transactions exist, check portfolio_balances or return default 10,000.0
            return 10000.0

    @retry_on_db_lock()
    def reset_paper_account(self, account_id: str = "default") -> dict[str, int]:
        """Reset all paper trading data for an account to initial state.

        Clears:
        - paper_ledger_transactions
        - paper_positions
        - paper_trade_history

        Returns counts of deleted rows for each table.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Clear ledger transactions
            cursor.execute(
                "DELETE FROM paper_ledger_transactions WHERE account_id = ?",
                (account_id,),
            )
            ledger_deleted = cursor.rowcount

            # Clear positions
            cursor.execute(
                "DELETE FROM paper_positions WHERE account_id = ?",
                (account_id,),
            )
            positions_deleted = cursor.rowcount

            # Clear trade history (note: no account_id column, so clears all)
            cursor.execute("DELETE FROM paper_trade_history")
            trades_deleted = cursor.rowcount

            conn.commit()

        return {
            "ledger_transactions_deleted": ledger_deleted,
            "positions_deleted": positions_deleted,
            "trade_history_deleted": trades_deleted,
        }

    # -------------------------------------------------------------------------
    # Paper Trading Completed Round-Trip Trade History Methods
    # -------------------------------------------------------------------------

    @retry_on_db_lock()
    def record_paper_trade_history(
        self,
        trade_id: str,
        exchange: str,
        symbol: str,
        timeframe: str,
        side: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        entry_time: str,
        exit_time: str,
        holding_period_bars: int,
        realized_pnl: float,
        realized_pnl_pct: float,
        fees: float = 0.0,
        exit_reason: str = "MANUAL",
        params_json: str = "{}",
        metadata: str = "{}",
    ) -> int:
        """Insert a completed round-trip trade record into paper_trade_history."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO paper_trade_history
                (trade_id, exchange, symbol, timeframe, side, entry_price, exit_price, quantity, entry_time, exit_time, holding_period_bars, realized_pnl, realized_pnl_pct, fees, exit_reason, params_json, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    exchange.lower(),
                    normalize_symbol_key(symbol),
                    timeframe.lower(),
                    side.upper(),
                    float(entry_price),
                    float(exit_price),
                    float(quantity),
                    str(entry_time),
                    str(exit_time),
                    int(holding_period_bars),
                    float(realized_pnl),
                    float(realized_pnl_pct),
                    float(fees),
                    exit_reason.upper(),
                    str(params_json),
                    str(metadata),
                ),
            )
            row_id = cursor.lastrowid
            conn.commit()
            return row_id

    def list_paper_trade_history(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        exit_reason: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query completed paper trade history logs."""
        query = "SELECT * FROM paper_trade_history"
        conditions = []
        params: list[Any] = []

        if exchange:
            conditions.append("exchange = ?")
            params.append(exchange.lower())
        if symbol:
            conditions.append("symbol = ?")
            params.append(normalize_symbol_key(symbol))
        if exit_reason:
            conditions.append("exit_reason = ?")
            params.append(exit_reason.upper())

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_paper_trade_statistics(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """Compute aggregate performance statistics across historical paper trades."""
        trades = self.list_paper_trade_history(
            exchange=exchange, symbol=symbol, limit=10000
        )
        if not trades:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "total_realized_pnl": 0.0,
                "profit_factor": 0.0,
                "winning_trades": 0,
                "losing_trades": 0,
                "avg_trade_pnl": 0.0,
                "total_fees": 0.0,
            }

        winning = [t for t in trades if t["realized_pnl"] > 0]
        losing = [t for t in trades if t["realized_pnl"] <= 0]
        total_pnl = sum(t["realized_pnl"] for t in trades)
        total_fees = sum(t["fees"] for t in trades)
        gross_profit = sum(t["realized_pnl"] for t in winning)
        gross_loss = abs(sum(t["realized_pnl"] for t in losing))

        profit_factor = (
            round(gross_profit / gross_loss, 2)
            if gross_loss > 0
            else (999.0 if gross_profit > 0 else 0.0)
        )
        win_rate = round((len(winning) / len(trades)) * 100.0, 2)

        return {
            "total_trades": len(trades),
            "win_rate": win_rate,
            "total_realized_pnl": round(total_pnl, 2),
            "profit_factor": profit_factor,
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "avg_trade_pnl": round(total_pnl / len(trades), 2),
            "total_fees": round(total_fees, 2),
        }

    # -------------------------------------------------------------------------
    # Optuna Profile Registry Methods
    # -------------------------------------------------------------------------

    @retry_on_db_lock()
    def save_optuna_profile(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        strategy_mode: str,
        target_metric: str,
        best_params: str,
        best_value: float,
        updated_at: str,
    ) -> None:
        """Upsert per-asset Optuna hyperparameter profile."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO optuna_profiles
                (exchange, symbol, timeframe, strategy_mode, target_metric, best_params, best_value, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exchange.lower(),
                    normalize_symbol_key(symbol),
                    timeframe.lower(),
                    strategy_mode,
                    target_metric,
                    best_params,
                    float(best_value),
                    str(updated_at),
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def load_optuna_profile(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        strategy_mode: str,
        target_metric: str,
    ) -> dict[str, Any] | None:
        """Retrieve hyperparameter profile for a specific asset and strategy configuration."""
        query = """
            SELECT * FROM optuna_profiles
            WHERE exchange = ? AND symbol = ? AND timeframe = ? AND strategy_mode = ? AND target_metric = ?
        """
        params = [
            exchange.lower(),
            normalize_symbol_key(symbol),
            timeframe.lower(),
            strategy_mode,
            target_metric,
        ]
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))

    # -------------------------------------------------------------------------
    # Portfolio Balance Methods
    # -------------------------------------------------------------------------

    @retry_on_db_lock()
    def update_portfolio_balance(
        self, currency: str, total: float, available: float, updated_at: str
    ) -> None:
        """Upsert current portfolio balance snapshot for a specific currency."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO portfolio_balances
                (currency, total, available, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (currency.upper(), float(total), float(available), str(updated_at)),
            )
            conn.commit()
            return cursor.lastrowid

    def get_portfolio_balances(self) -> dict[str, dict[str, Any]]:
        """Retrieve map of all portfolio currency balances."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM portfolio_balances")
            rows = cursor.fetchall()
            if not rows:
                return {}
            cols = [d[0] for d in cursor.description]
            result = {}
            for row in rows:
                item = dict(zip(cols, row))
                result[item["currency"]] = item
            return result

    # -------------------------------------------------------------------------
    # Background Tasks Methods
    # -------------------------------------------------------------------------

    @retry_on_db_lock()
    def save_background_task(self, task_data: dict[str, Any]) -> None:
        """Upsert a background task execution state."""
        result_json = (
            json.dumps(task_data["result"])
            if task_data.get("result") is not None
            else None
        )
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO background_tasks
                (task_id, name, status, progress_pct, step_description, start_time, end_time, error, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_data["task_id"],
                    task_data["name"],
                    task_data["status"],
                    float(task_data.get("progress_pct", 0.0)),
                    str(task_data.get("step_description", "")),
                    str(task_data["start_time"]),
                    str(task_data["end_time"]) if task_data.get("end_time") else None,
                    str(task_data["error"]) if task_data.get("error") else None,
                    result_json,
                    str(task_data["created_at"]),
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_background_task(self, task_id: str) -> dict[str, Any] | None:
        """Retrieve background task details by task ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM background_tasks WHERE task_id = ?", (task_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            res = dict(zip(cols, row))
            if res.get("result_json"):
                try:
                    res["result"] = json.loads(res["result_json"])
                except Exception:
                    res["result"] = None
            else:
                res["result"] = None
            return res

    def list_background_tasks(
        self, limit: int = 50, status: str | None = None
    ) -> list[dict[str, Any]]:
        """List recent background tasks ordered by creation time descending."""
        query = "SELECT * FROM background_tasks"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            cols = [d[0] for d in cursor.description]
            tasks = []
            for row in cursor.fetchall():
                item = dict(zip(cols, row))
                if item.get("result_json"):
                    try:
                        item["result"] = json.loads(item["result_json"])
                    except Exception:
                        item["result"] = None
                else:
                    item["result"] = None
                tasks.append(item)
            return tasks

    @retry_on_db_lock()
    def prune_background_tasks(self, keep_limit: int = 50) -> int:
        """Prune old background tasks, keeping only the most recent `keep_limit` tasks.

        ema_vwap-83qz: the table's primary key is task_id (no `id` column), so the
        retention subselect must order and filter on the real columns.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM background_tasks
                WHERE task_id NOT IN (
                    SELECT task_id FROM background_tasks
                    ORDER BY created_at DESC, task_id DESC
                    LIMIT ?
                )
                """,
                (keep_limit,),
            )
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    # -------------------------------------------------------------------------
    # System Settings Methods
    # -------------------------------------------------------------------------

    @retry_on_db_lock()
    def save_setting(self, key: str, value: str, is_secret: bool = False) -> None:
        """Upsert a key-value setting in SQLite. Secrets are encrypted at rest (ema_vwap-y16r)."""
        from datetime import datetime, timezone

        stored_value = _encrypt_value(value) if is_secret else value
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO settings
                (key, value, is_secret, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (key, stored_value, int(is_secret), now_iso),
            )
            conn.commit()
            return cursor.lastrowid

    def get_setting(self, key: str) -> dict[str, Any] | None:
        """Retrieve setting row by key. Secret values are decrypted on read (ema_vwap-y16r)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            result = dict(zip(cols, row))
            if result.get("is_secret") and isinstance(result.get("value"), str):
                try:
                    result["value"] = _decrypt_value(result["value"])
                except Exception:
                    logger.error(f"[DB] Failed to decrypt setting '{key}'")
                    result["value"] = None
            return result

    def load_all_settings(self) -> list[dict[str, Any]]:
        """Load all stored settings. Secret values are decrypted on read (ema_vwap-y16r)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM settings")
            cols = [d[0] for d in cursor.description]
            results = []
            for row in cursor.fetchall():
                item = dict(zip(cols, row))
                if item.get("is_secret") and isinstance(item.get("value"), str):
                    try:
                        item["value"] = _decrypt_value(item["value"])
                    except Exception:
                        logger.error(
                            f"[DB] Failed to decrypt setting '{item.get('key')}'"
                        )
                        item["value"] = None
                results.append(item)
            return results


# Global default database instance (candles domain only)
db = CandleDatabase(domain="candles")
