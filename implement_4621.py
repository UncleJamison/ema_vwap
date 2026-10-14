#!/usr/bin/env python3
"""Clean domain-aware init_db implementation for 462.1."""

with open("src/database.py", "r") as f:
    content = f.read()

# Add _get_db_domain helper after __init__
old = """            else:
                raise

    @staticmethod
    def _is_corruption_error"""
new = '''            else:
                raise

    def _get_db_domain(self) -> str:
        """Determine the DB domain based on the path."""
        db_path_str = os.path.abspath(self.db_path).lower()
        if "paper_trading" in db_path_str:
            return "paper_trading"
        elif "batch_results" in db_path_str:
            return "batch_results"
        elif "system" in db_path_str:
            return "system"
        return "candles"

    @staticmethod
    def _is_corruption_error'''
content = content.replace(old, new)

# Add domain detection in init_db
old = '''            cursor.execute(f"PRAGMA journal_mode = {mode};")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS candles ('''
new = '''            cursor.execute(f"PRAGMA journal_mode = {mode};")

            # Domain-aware initialization
            db_domain = self._get_db_domain()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS candles ('''
content = content.replace(old, new)

# Fix the paper_ledger block - wrap properly
old = '''            # Paper Trading Ledger Schema (Legacy/Direct)
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
            )'''
new = '''            # Paper Trading Ledger Schema (only paper_trading domain)
            if db_domain == "paper_trading":
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
                )'''
content = content.replace(old, new)

# Fix paper_profiles
old = '''            # Paper Trading Per-Asset Optuna Profiles Schema
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
            )'''
new = '''            # Paper Trading Per-Asset Optuna Profiles Schema (only paper_trading domain)
            if db_domain == "paper_trading":
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
                )'''
content = content.replace(old, new)

# Fix paper_positions
old = '''            # Paper Trading Open Positions Schema
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    position_size REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    pnl REAL NOT NULL,
                    pnl_percentage REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    unrealized_pnl_percentage REAL NOT NULL,
                    is_open INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )'''
new = '''            # Paper Trading Open Positions Schema (only paper_trading domain)
            if db_domain == "paper_trading":
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_positions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        exchange TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        position_size REAL NOT NULL,
                        entry_price REAL NOT NULL,
                        current_price REAL NOT NULL,
                        pnl REAL NOT NULL,
                        pnl_percentage REAL NOT NULL,
                        unrealized_pnl REAL NOT NULL,
                        unrealized_pnl_percentage REAL NOT NULL,
                        is_open INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )'''
content = content.replace(old, new)

# Fix paper_ledger_transactions
old = '''            # Paper Trading Double-Entry Ledger Transactions Schema
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_ledger_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    transaction_type TEXT NOT NULL,
                    amount REAL NOT NULL,
                    balance_after REAL NOT NULL,
                    transaction_timestamp TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )'''
new = '''            # Paper Trading Double-Entry Ledger Transactions Schema (only paper_trading domain)
            if db_domain == "paper_trading":
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_ledger_transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        exchange TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        transaction_type TEXT NOT NULL,
                        amount REAL NOT NULL,
                        balance_after REAL NOT NULL,
                        transaction_timestamp TEXT NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )'''
content = content.replace(old, new)

# Fix paper_trade_history
old = '''            # Paper Trading Completed Round-Trip Trade History Schema
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_trade_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    trade_type TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    pnl REAL NOT NULL,
                    pnl_percentage REAL NOT NULL,
                    trade_timestamp TEXT NOT NULL,
                    duration_seconds INTEGER NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )'''
new = '''            # Paper Trading Completed Round-Trip Trade History Schema (only paper_trading domain)
            if db_domain == "paper_trading":
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_trade_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        exchange TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        trade_type TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        exit_price REAL NOT NULL,
                        quantity REAL NOT NULL,
                        pnl REAL NOT NULL,
                        pnl_percentage REAL NOT NULL,
                        trade_timestamp TEXT NOT NULL,
                        duration_seconds INTEGER NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )'''
content = content.replace(old, new)

# Fix batch_results (table + ALTER + index)
old = '''            # Batch sweep leaderboard table
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
            )'''
new = '''            # Batch sweep leaderboard table (only batch_results and system domains)
            if db_domain in ("batch_results", "system"):
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
                )'''
content = content.replace(old, new)

# Fix optuna_profiles
old = '''            # Optuna Profiles Schema (Legacy / General)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS optuna_profiles (
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
            )'''
new = '''            # Optuna Profiles Schema (only system and batch_results domains)
            if db_domain in ("system", "batch_results"):
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS optuna_profiles (
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
                )'''
content = content.replace(old, new)

# Fix portfolio_balances
old = '''            # Portfolio Balances Schema
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio_balances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    balance REAL NOT NULL,
                    balance_value REAL NOT NULL,
                    balance_timestamp TEXT NOT NULL
                )
                """
            )'''
new = '''            # Portfolio Balances Schema (only system domain)
            if db_domain == "system":
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS portfolio_balances (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        exchange TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        balance REAL NOT NULL,
                        balance_value REAL NOT NULL,
                        balance_timestamp TEXT NOT NULL
                    )
                    """
                )'''
content = content.replace(old, new)

# Fix background_tasks
old = '''            # Background Tasks Schema
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS background_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,
                    result TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                )
                """
            )'''
new = '''            # Background Tasks Schema (only system domain)
            if db_domain == "system":
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS background_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id TEXT NOT NULL,
                        task_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        progress REAL NOT NULL DEFAULT 0,
                        result TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        completed_at TEXT NOT NULL
                    )
                    """
                )'''
content = content.replace(old, new)

# Fix settings
old = '''            # System Settings Schema
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL UNIQUE,
                    value TEXT NOT NULL,
                    is_secret INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )'''
new = '''            # System Settings Schema (only system domain)
            if db_domain == "system":
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS settings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        key TEXT NOT NULL UNIQUE,
                        value TEXT NOT NULL,
                        is_secret INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
                    )
                    """
                )'''
content = content.replace(old, new)

# Fix alerts
old = '''            # System Alerts Schema
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    level TEXT NOT NULL,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    acknowledged_at TEXT NOT NULL
                )
                """
            )'''
new = '''            # System Alerts Schema (only system domain)
            if db_domain == "system":
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alerts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        type TEXT NOT NULL,
                        message TEXT NOT NULL,
                        level TEXT NOT NULL,
                        source TEXT NOT NULL,
                        category TEXT NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        acknowledged INTEGER NOT NULL DEFAULT 0,
                        acknowledged_at TEXT NOT NULL
                    )
                    """
                )'''
content = content.replace(old, new)

# Add missing alert methods before the global instance
old = """# Global default database instance"""
new = '''    # ── Alert management (system domain) ──

    def save_alert(self, entry: dict[str, Any]) -> None:
        """Save an alert entry to the database."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO alerts
                (id, type, message, level, source, category, metadata, created_at, acknowledged, acknowledged_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.get("id"),
                    entry.get("type"),
                    entry.get("message"),
                    entry.get("level"),
                    entry.get("source"),
                    entry.get("category"),
                    str(entry.get("metadata", "")),
                    entry.get("created_at"),
                    entry.get("acknowledged", 0),
                    entry.get("acknowledged_at"),
                ),
            )
            conn.commit()

    def get_alerts(
        self,
        limit: int = 100,
        level: str | None = None,
        acknowledged: bool | None = None,
        type_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve alerts with optional filters."""
        with self.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            where = []
            params = []
            if level is not None:
                where.append("level = ?")
                params.append(level)
            if acknowledged is not None:
                where.append("acknowledged = ?")
                params.append(int(acknowledged))
            if type_filter is not None:
                where.append("type = ?")
                params.append(type_filter)
            where.append("id > 0")
            params.append(limit)
            query = "SELECT * FROM alerts WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT ?"
            rows = cursor.execute(query, params).fetchall()
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

    def acknowledge_alert(self, alert_id: int) -> bool:
        """Acknowledge an alert by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE alerts SET acknowledged = 1, acknowledged_at = ? WHERE id = ?",
                (time.strftime("%Y-%m-%dT%H:%M:%SZ"), alert_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_alert(self, alert_id: int) -> bool:
        """Delete an alert by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
            conn.commit()
            return cursor.rowcount > 0


# Global default database instance'''
content = content.replace(old, new)

with open("src/database.py", "w") as f:
    f.write(content)

print("Domain-aware init_db() + alert methods implemented successfully")
