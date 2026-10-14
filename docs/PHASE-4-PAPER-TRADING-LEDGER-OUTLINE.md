# Phase 4 Architecture Outline — Paper Trading Portfolio Ledger & Optuna Profile Registry

## Overview

Design and implement an isolated Paper Trading Portfolio & Transaction Ledger Module to forward-test calibrated Optuna strategy parameters in simulated real-time on a per-ticker and per-crypto asset basis.

---

## Directory Structure

```
src/
├── paper/
│   ├── __init__.py            # Exports PaperProfileRegistry, PaperLedger, models
│   ├── models.py              # Dataclasses & Enums (PaperPosition, PaperTrade, LedgerTransaction)
│   ├── profiles.py            # Per-Asset Optuna Profile Registry
│   └── ledger.py              # Double-entry TransactionLedger & Balance Manager
├── database.py                # SQLite schema: paper_profiles, paper_positions, paper_ledger_transactions, paper_trade_history
└── app.py                     # API endpoints for paper profiles, positions, and ledger history
```

---

## Database Schemas (`src/database.py`)

### 1. `paper_profiles`
Per-symbol active Optuna parameter sets and strategy configs:
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `exchange TEXT NOT NULL`
- `symbol TEXT NOT NULL`
- `timeframe TEXT NOT NULL`
- `strategy_mode TEXT NOT NULL`
- `target_metric TEXT NOT NULL`
- `params_json TEXT NOT NULL`
- `optuna_score REAL NOT NULL`
- `is_active INTEGER NOT NULL DEFAULT 1`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- `UNIQUE(exchange, symbol, timeframe, strategy_mode)`

### 2. `paper_positions`
Simulated active open positions:
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `exchange TEXT NOT NULL`
- `symbol TEXT NOT NULL`
- `side TEXT NOT NULL` (`'LONG'` / `'SHORT'`)
- `entry_price REAL NOT NULL`
- `current_price REAL NOT NULL`
- `quantity REAL NOT NULL`
- `cost_basis REAL NOT NULL`
- `stop_loss REAL`
- `take_profit REAL`
- `unrealized_pnl REAL NOT NULL DEFAULT 0.0`
- `unrealized_pnl_pct REAL NOT NULL DEFAULT 0.0`
- `entry_time TEXT NOT NULL`
- `updated_time TEXT NOT NULL`
- `metadata TEXT NOT NULL DEFAULT '{}'`
- `UNIQUE(exchange, symbol)`

### 3. `paper_ledger_transactions`
Double-entry ledger logging cash and asset balance movements:
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `transaction_id TEXT NOT NULL UNIQUE`
- `account_id TEXT NOT NULL DEFAULT 'default'`
- `exchange TEXT NOT NULL`
- `symbol TEXT NOT NULL`
- `type TEXT NOT NULL` (`'DEPOSIT'`, `'WITHDRAWAL'`, `'BUY'`, `'SELL'`, `'FEE'`, `'PNL_ADJUST'`)
- `amount REAL NOT NULL`
- `asset_qty REAL NOT NULL DEFAULT 0.0`
- `price REAL NOT NULL DEFAULT 0.0`
- `fee REAL NOT NULL DEFAULT 0.0`
- `balance_after REAL NOT NULL`
- `timestamp TEXT NOT NULL`
- `notes TEXT NOT NULL DEFAULT ''`
- `metadata TEXT NOT NULL DEFAULT '{}'`

### 4. `paper_trade_history`
Completed round-trip trade executions with performance tracking:
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `trade_id TEXT NOT NULL UNIQUE`
- `exchange TEXT NOT NULL`
- `symbol TEXT NOT NULL`
- `timeframe TEXT NOT NULL`
- `side TEXT NOT NULL`
- `entry_price REAL NOT NULL`
- `exit_price REAL NOT NULL`
- `quantity REAL NOT NULL`
- `entry_time TEXT NOT NULL`
- `exit_time TEXT NOT NULL`
- `holding_period_bars INTEGER NOT NULL DEFAULT 0`
- `realized_pnl REAL NOT NULL`
- `realized_pnl_pct REAL NOT NULL`
- `fees REAL NOT NULL DEFAULT 0.0`
- `exit_reason TEXT NOT NULL` (`'TAKE_PROFIT'`, `'STOP_LOSS'`, `'SIGNAL'`, `'TIMEOUT'`, `'MANUAL'`)
- `params_json TEXT NOT NULL DEFAULT '{}'`
- `metadata TEXT NOT NULL DEFAULT '{}'`

---

## Sub-phases

1. **`ema_vwap-5fp.1`**: Per-Asset Optuna Profile Registry & SQLite Ledger Database Schema
2. **`ema_vwap-5fp.2`**: Optuna UI 1-Click 'Deploy to Paper Trading Profile' Workflow
3. **`ema_vwap-5fp.3`**: Virtual Execution Engine with Live Polling & SL/TP Order Manager
4. **`ema_vwap-5fp.4`**: Paper Trading Web UI Dashboard & Performance Ledger
