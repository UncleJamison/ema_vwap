PRAGMA foreign_keys=OFF;
PRAGMA journal_mode = DELETE;

BEGIN TRANSACTION;

DROP TABLE IF EXISTS candles;
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
CREATE INDEX idx_candles_lookup ON candles (exchange, symbol, timeframe, timestamp);
CREATE INDEX idx_candles_sym_tf ON candles (symbol, timeframe);

DROP TABLE IF EXISTS batch_results;
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
    validation_status TEXT,
    validation_json TEXT
);
CREATE INDEX idx_batch_results_metric ON batch_results (target_metric, best_value DESC);

DROP TABLE IF EXISTS alerts;
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    message TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'INFO',
    source TEXT,
    category TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    acknowledged_at TEXT
);
CREATE INDEX idx_alerts_created_at ON alerts (created_at DESC);
CREATE INDEX idx_alerts_type ON alerts (type, created_at DESC);
CREATE INDEX idx_alerts_level ON alerts (level, acknowledged);

DROP TABLE IF EXISTS background_tasks;
CREATE TABLE background_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    k TEXT NOT NULL,
    v TEXT NOT NULL,
    vtype TEXT NOT NULL DEFAULT 'string',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);

DROP TABLE IF EXISTS settings;
CREATE TABLE settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    k TEXT NOT NULL,
    v TEXT NOT NULL,
    vtype TEXT NOT NULL DEFAULT 'string',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);

COMMIT;