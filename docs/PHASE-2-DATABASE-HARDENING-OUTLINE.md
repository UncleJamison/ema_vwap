# Phase 2 Database Hardening Outline — SQLite WAL & Concurrency Engine

## Goal Overview
Harden and optimize `src/database.py` (`CandleDatabase`) to support high-throughput, multi-threaded concurrent reads and writes across background historical data syncs, Optuna strategy optimization, and live/paper trading ledger logging.

## Architectural Changes

### 1. Connection Initialization & PRAGMA Tuning
On connection startup and database initialization, set the following PRAGMAs:
- `PRAGMA journal_mode=WAL;` — Enables Write-Ahead Logging for non-blocking concurrent readers during writes.
- `PRAGMA synchronous=NORMAL;` — Optimal balance between durability and speed in WAL mode.
- `PRAGMA cache_size=-64000;` — Allocates 64MB of memory for the page cache (-64,000 KiB).
- `PRAGMA temp_store=MEMORY;` — Stores temporary tables and indices in memory.
- `sqlite3.connect(..., timeout=30.0)` — 30-second busy timeout to handle lock contention gracefully.

### 2. Transaction Management & Exponential Retry / Backoff
- Implement a thread-safe connection context manager `get_connection()` that automatically configures performance PRAGMAs on every connection.
- Implement `@retry_on_lock` decorator / retry helper for write operations (up to 5 retries with jittered exponential backoff: 0.1s, 0.2s, 0.4s, 0.8s, 1.6s) to guarantee zero `sqlite3.OperationalError` ("database is locked") during heavy write bursts.

### 3. Expanded Database Schemas
Abstract database schemas to prepare for upcoming system components:
- `candles`: (exchange, symbol, timeframe, timestamp, open, high, low, close, volume) with primary key `(exchange, symbol, timeframe, timestamp)` and index `idx_candles_lookup`.
- `batch_results`: Optimizer leaderboard table.
- `paper_ledger`: (id, exchange, symbol, timeframe, action, price, quantity, total_usd, timestamp, metadata)
- `optuna_profiles`: (id, exchange, symbol, timeframe, strategy_mode, target_metric, best_params, best_value, updated_at)
- `portfolio_balances`: (currency, total, available, updated_at)

### 4. Concurrency Test Suite
Create `tests/test_database_concurrency.py`:
- Multi-threaded read/write load test (10+ concurrent reader and writer threads).
- Verify WAL mode journal check (`PRAGMA journal_mode` returns `'wal'`).
- Verify PRAGMA parameters applied correctly across new connections.
- Measure bulk insertion speed and query latency under load.
