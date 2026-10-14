"""Baseline schema from init_db

Captures the complete database schema as created by CandleDatabase.init_db().
This is the starting point for all future Alembic migrations — existing
databases should be stamped at this revision (alembic stamp head).

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-08

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Full schema as emitted by SQLite for a fresh init_db() run.
SCHEMA_DDL = """
CREATE TABLE alerts (
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
                );

CREATE TABLE background_tasks (
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
                );

CREATE TABLE batch_results (
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
                , validation_status TEXT, validation_json TEXT);

CREATE TABLE candles (
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
                );

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
                );

CREATE TABLE paper_ledger (
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
                );

CREATE TABLE paper_ledger_transactions (
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
                );

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
                );

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
                    deployed INTEGER NOT NULL DEFAULT 0,
                    deployed_at TEXT, validation_report_json TEXT,
                    UNIQUE(exchange, symbol, timeframe, strategy_mode)
                );

CREATE TABLE paper_trade_history (
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
                );

CREATE TABLE portfolio_balances (
                    currency TEXT PRIMARY KEY,
                    total REAL NOT NULL,
                    available REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );

CREATE TABLE settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    is_secret INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );



CREATE INDEX idx_alerts_created_at
                ON alerts (created_at DESC)
                ;

CREATE INDEX idx_alerts_level
                ON alerts (level)
                ;

CREATE INDEX idx_alerts_type
                ON alerts (type)
                ;

CREATE INDEX idx_background_tasks_created
                ON background_tasks (created_at DESC)
                ;

CREATE INDEX idx_batch_results_asset
                ON batch_results (exchange, symbol, timeframe, target_metric)
                ;

CREATE INDEX idx_batch_results_metric
                ON batch_results (target_metric, best_value DESC)
                ;

CREATE INDEX idx_candles_lookup 
                ON candles (exchange, symbol, timeframe, timestamp)
                ;

CREATE INDEX idx_optuna_profiles_lookup
                ON optuna_profiles (exchange, symbol, timeframe)
                ;

CREATE INDEX idx_paper_ledger_lookup
                ON paper_ledger (exchange, symbol, timestamp)
                ;

CREATE INDEX idx_paper_ledger_trades
                ON paper_ledger (exchange, symbol, timeframe, action, timestamp)
                ;

CREATE INDEX idx_paper_ledger_tx_time
                ON paper_ledger_transactions (account_id, timestamp DESC)
                ;

CREATE INDEX idx_paper_positions_sym
                ON paper_positions (exchange, symbol, account_id)
                ;

CREATE INDEX idx_paper_profiles_best
                ON paper_profiles (exchange, symbol, timeframe, is_active, optuna_score DESC)
                ;

CREATE INDEX idx_paper_profiles_lookup
                ON paper_profiles (exchange, symbol, is_active)
                ;

CREATE INDEX idx_paper_trade_hist_sym
                ON paper_trade_history (exchange, symbol, exit_time DESC)
                ;

"""


def upgrade() -> None:
    for statement in SCHEMA_DDL.split(";"):
        stmt = statement.strip()
        if stmt:
            op.execute(stmt)


def downgrade() -> None:
    # Baseline downgrade: drop every object created in the baseline, in
    # dependency-safe (reverse) order.
    drop_order = [
        "idx_alerts_created_at",
        "idx_alerts_level",
        "idx_alerts_type",
        "alerts",
        "background_tasks",
        "idx_background_tasks_created",
        "settings",
        "portfolio_balances",
        "idx_batch_results_asset",
        "idx_batch_results_metric",
        "batch_results",
        "idx_candles_lookup",
        "candles",
        "idx_optuna_profiles_lookup",
        "optuna_profiles",
        "idx_paper_ledger_lookup",
        "idx_paper_ledger_trades",
        "idx_paper_ledger_tx_time",
        "paper_ledger_transactions",
        "paper_ledger",
        "idx_paper_profiles_best",
        "idx_paper_profiles_lookup",
        "paper_profiles",
        "idx_paper_positions_sym",
        "paper_positions",
        "idx_paper_trade_hist_sym",
        "paper_trade_history",
    ]
    for name in drop_order:
        kind = "INDEX" if name.startswith("idx_") else "TABLE"
        op.execute(f"DROP {kind} IF EXISTS {name}")
