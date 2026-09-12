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
import time
from typing import Any

import pandas as pd

from src.logger import logger

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


class CandleDatabase:
    """SQLite Database Manager for caching and retrieving historical crypto candle data & trading artifacts."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
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

    def _recover_corrupted_db(self, exc: Exception) -> None:
        """Quarantine malformed database files and allow creating a clean database."""
        logger.error(
            f"[CandleDatabase] Malformed SQLite database detected ({exc}). Auto-recovering..."
        )
        ts = int(time.time())
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
                    except Exception:
                        pass

    def get_connection(self) -> sqlite3.Connection:
        """Create database connection with performance PRAGMAs & busy timeout tuned for WAL concurrency."""
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
                except Exception:
                    pass
            if self._is_corruption_error(e):
                self._recover_corrupted_db(e)
                new_conn = sqlite3.connect(self.db_path, timeout=30.0)
                new_conn.execute("PRAGMA synchronous = NORMAL;")
                new_conn.execute("PRAGMA cache_size = -64000;")
                new_conn.execute("PRAGMA temp_store = MEMORY;")
                return new_conn
            raise

    @retry_on_db_lock()
    def init_db(self) -> None:
        """Initialize tables, WAL mode, and indexes."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            is_default_db = os.path.abspath(self.db_path) == os.path.abspath(DB_PATH)
            in_docker = (
                os.path.exists("/.dockerenv") or os.environ.get("IN_DOCKER") == "1"
            )
            # The default DB (candles.db) is shared with Docker via volume mounts (./data:/app/data).
            # On Windows hosts, SQLite WAL mode uses POSIX shared memory (-shm) which fails
            # over VirtioFS/9p volume mounts with 'unable to open database file'.
            # Therefore, the default DB must stay in DELETE mode unless explicitly overridden.
            if "DB_JOURNAL_MODE" in os.environ:
                mode = os.environ["DB_JOURNAL_MODE"]
            elif in_docker or is_default_db:
                mode = "DELETE"
            else:
                mode = "WAL"
            cursor.execute(f"PRAGMA journal_mode = {mode};")

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

            # Batch sweep leaderboard table
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
                    favorite INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {
                row[1]
                for row in cursor.execute("PRAGMA table_info(batch_results)").fetchall()
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

            # Paper Trading Open Positions Schema
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
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
                    metadata TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(exchange, symbol)
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_paper_positions_sym
                ON paper_positions (exchange, symbol)
                """
            )

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
                logger.info("[CandleDatabase] portfolio_balances migration complete")

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

    @retry_on_db_lock()
    def save_candles(
        self, exchange: str, symbol: str, timeframe: str, df: pd.DataFrame
    ) -> int:
        """
        Bulk upsert candles into SQLite database.
        Returns number of rows saved/updated.
        """
        if df.empty:
            return 0

        exchange_clean = exchange.lower()
        symbol_clean = normalize_symbol_key(symbol)
        timeframe_clean = timeframe.lower()

        rows = []
        for _, row in df.iterrows():
            ts_str = str(row["timestamp"])
            rows.append(
                (
                    exchange_clean,
                    symbol_clean,
                    timeframe_clean,
                    ts_str,
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row["volume"]),
                )
            )

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT OR REPLACE INTO candles 
                (exchange, symbol, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
            return len(rows)

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

        with self.get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params)

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
    ) -> None:
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
    ) -> int:
        """Upsert per-asset calibrated Optuna strategy parameters into paper_profiles."""
        from datetime import datetime, timezone

        now_iso = updated_at or datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO paper_profiles
                (exchange, symbol, timeframe, strategy_mode, target_metric, params_json, optuna_score, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange, symbol, timeframe, strategy_mode) DO UPDATE SET
                    target_metric = excluded.target_metric,
                    params_json = excluded.params_json,
                    optuna_score = excluded.optuna_score,
                    is_active = excluded.is_active,
                    updated_at = excluded.updated_at
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
        self, exchange: str | None = None, active_only: bool = False
    ) -> list[dict[str, Any]]:
        """List all paper trading profiles with optional exchange and active filtering."""
        query = "SELECT * FROM paper_profiles"
        conditions = []
        params: list[Any] = []

        if exchange:
            conditions.append("exchange = ?")
            params.append(exchange.lower())
        if active_only:
            conditions.append("is_active = 1")

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
    def set_paper_profile_active(self, profile_id: int, is_active: bool) -> bool:
        """Toggle active state of a paper profile."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
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
                (exchange, symbol, side, entry_price, current_price, quantity, cost_basis, stop_loss, take_profit, unrealized_pnl, unrealized_pnl_pct, entry_time, updated_time, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange, symbol) DO UPDATE SET
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
                    metadata = excluded.metadata
                """,
                (
                    exchange.lower(),
                    normalize_symbol_key(symbol),
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
                    str(metadata),
                ),
            )
            pos_id = cursor.lastrowid
            conn.commit()
            return pos_id

    def get_paper_position(self, exchange: str, symbol: str) -> dict[str, Any] | None:
        """Retrieve open paper position for a given exchange and symbol."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM paper_positions WHERE exchange = ? AND symbol = ?",
                (exchange.lower(), normalize_symbol_key(symbol)),
            )
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))

    def list_paper_positions(self, exchange: str | None = None) -> list[dict[str, Any]]:
        """List all active open paper positions."""
        query = "SELECT * FROM paper_positions"
        params: list[Any] = []
        if exchange:
            query += " WHERE exchange = ?"
            params.append(exchange.lower())
        query += " ORDER BY updated_time DESC"

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    @retry_on_db_lock()
    def close_paper_position(self, exchange: str, symbol: str) -> bool:
        """Remove/close an open paper position."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM paper_positions WHERE exchange = ? AND symbol = ?",
                (exchange.lower(), normalize_symbol_key(symbol)),
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

    # -------------------------------------------------------------------------
    # System Settings Methods
    # -------------------------------------------------------------------------

    @retry_on_db_lock()
    def save_setting(self, key: str, value: str, is_secret: bool = False) -> None:
        """Upsert a key-value setting in SQLite."""
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO settings
                (key, value, is_secret, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (key, value, int(is_secret), now_iso),
            )
            conn.commit()

    def get_setting(self, key: str) -> dict[str, Any] | None:
        """Retrieve setting row by key."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))

    def load_all_settings(self) -> list[dict[str, Any]]:
        """Load all stored settings."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM settings")
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]


# Global default database instance
db = CandleDatabase()
