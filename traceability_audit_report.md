# TRACEABILITY AUDIT - DATABASE LAYER
## CandleDatabase Method Mapping to Business Logic

**Generated:** 2026-10-07  
**Database:** `src/database.py` (CandleDatabase class)  
**Tables:** candles, batch_results, paper_ledger, paper_profiles, paper_positions, paper_ledger_transactions, paper_trade_history, optuna_profiles, portfolio_balances, alerts, background_tasks, settings

---

## Legend
- **Business Function** = Entry point in business logic (API, service, worker)
- **DB Method** = CandleDatabase method called
- **SQL** = Exact SQL query executed
- **Params** = Parameters passed to query
- **Return Type** = Method return type annotation
- **Transaction Handling** = How commits/rollbacks handled
- **Validation** = ✅ PASS / ⚠️ WARN / ❌ FAIL

---

## 1. CANDLE DATA OPERATIONS

### 1.1 DataLoader.load_candles() → save_candles()
| Field | Details |
|-------|---------|
| **Business Function** | `DataLoader.fetch_kucoin_historical()`, `DataLoader.fetch_gemini_historical()`, `DataLoader.resample_and_upsert()`, `DataLoader.fetch_and_update_candles()` |
| **DB Method** | `save_candles(exchange, symbol, timeframe, df: pd.DataFrame) -> int` |
| **SQL** | `INSERT OR REPLACE INTO candles (exchange, symbol, timeframe, timestamp, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)` |
| **Params** | `(exchange.lower(), normalize_symbol_key(symbol), timeframe.lower(), ts_str, open, high, low, close, volume)` × N rows |
| **Return Type** | `int` (rows saved) |
| **Transaction** | `@retry_on_db_lock()` + `with self.get_connection() as conn: ... conn.commit()` |
| **Validation** | ✅ PASS - Correct upsert, params match, index `idx_candles_lookup` exists |

### 1.2 DataLoader.load_candles() → load_candles()
| Field | Details |
|-------|---------|
| **Business Function** | `DataLoader.load_candles()` (cache read), `BatchOptimizer._run_single_optuna()`, `PaperTradingEngine.evaluate_symbol()`, `PaperTradingEngine.reconcile_positions_on_startup()`, `app.py` endpoints |
| **DB Method** | `load_candles(exchange, symbol, timeframe, limit, start_date, end_date) -> pd.DataFrame` |
| **SQL** | `SELECT timestamp, open, high, low, close, volume FROM candles WHERE exchange = ? AND symbol = ? AND timeframe = ? [AND timestamp >= ?] [AND timestamp <= ?] ORDER BY timestamp ASC` |
| **Params** | `[exchange.lower(), normalize_symbol_key(symbol), timeframe.lower(), start_date?, end_date?]` |
| **Return Type** | `pd.DataFrame` |
| **Transaction** | Read-only: `with self.get_connection() as conn: pd.read_sql_query()` |
| **Validation** | ✅ PASS - Index used, params normalized, limit applied in Python (not SQL) ⚠️ |

### 1.3 clear_cache endpoint → clear_cache()
| Field | Details |
|-------|---------|
| **Business Function** | `app.py:clear_cache()` (POST /api/clear_cache) |
| **DB Method** | `clear_cache(exchange?, symbol?) -> int` |
| **SQL** | `DELETE FROM candles [WHERE exchange = ?] [AND symbol = ?]` |
| **Params** | `(exchange.lower()?, normalize_symbol_key(symbol)?)` |
| **Return Type** | `int` (rows deleted) |
| **Transaction** | `@retry_on_db_lock()` + explicit `conn.commit()` |
| **Validation** | ✅ PASS - Correct params, index exists |

### 1.4 get_candle_count()
| Field | Details |
|-------|---------|
| **Business Function** | Internal use, cache sizing checks |
| **DB Method** | `get_candle_count(exchange, symbol, timeframe) -> int` |
| **SQL** | `SELECT COUNT(*) FROM candles WHERE exchange = ? AND symbol = ? AND timeframe = ?` |
| **Params** | `(exchange.lower(), normalize_symbol_key(symbol), timeframe.lower())` |
| **Return Type** | `int` |
| **Transaction** | Read-only |
| **Validation** | ✅ PASS |

### 1.5 Export endpoints → export_candles_csv/json()
| Field | Details |
|-------|---------|
| **Business Function** | `app.py:db_export_csv()`, `app.py:db_export_json()` |
| **DB Method** | `export_candles_csv/exchange/symbol/timeframe/limit) -> str` |
| **SQL** | `SELECT timestamp, open, high, low, close, volume FROM candles [WHERE exchange=?] [AND symbol=?] [AND timeframe=?] ORDER BY timestamp ASC [LIMIT ?]` |
| **Params** | Same as load_candles + limit |
| **Return Type** | `str` (CSV/JSON) |
| **Transaction** | Read-only |
| **Validation** | ✅ PASS |

---

## 2. BATCH OPTIMIZER LEADERBOARD

### 2.1 BatchOptimizer._run_single_optuna() → save_batch_result()
| Field | Details |
|-------|---------|
| **Business Function** | `BatchOptimizer._run_single_optuna()` (persists sweep result) |
| **DB Method** | `save_batch_result(exchange, symbol, timeframe, strategy_mode, target_metric, best_value, trade_count, sharpe, calmar, win_rate, max_dd, total_return, best_params, run_timestamp) -> int` |
| **SQL** | 1. `SELECT favorite, validation_status, validation_json FROM batch_results WHERE exchange=? AND symbol=? AND timeframe=? AND target_metric=?`<br>2. `DELETE FROM batch_results WHERE exchange=? AND symbol=? AND timeframe=? AND target_metric=?`<br>3. `INSERT INTO batch_results (...) VALUES (?,?,...,?)` (17 params) |
| **Params** | All lowercased exchange/symbol/timeframe + metric values + JSON params |
| **Return Type** | `int` (lastrowid) |
| **Transaction** | `@retry_on_db_lock()` + `with self.get_connection(): ... conn.commit()` |
| **Validation** | ✅ PASS - Correct upsert pattern, preserves favorite/validation, index `idx_batch_results_metric` exists |

### 2.2 get_batch_results() → load_batch_results()
| Field | Details |
|-------|---------|
| **Business Function** | `app.py:get_batch_results()`, `BatchOptimizer.run()`, `auto_validate_batch_results()` |
| **DB Method** | `load_batch_results(target_metric?, limit=100) -> list[dict]` |
| **SQL** | `SELECT * FROM batch_results [WHERE target_metric = ?] ORDER BY best_value DESC LIMIT ?` |
| **Params** | `[target_metric?, limit]` |
| **Return Type** | `list[dict]` |
| **Transaction** | Read-only |
| **Validation** | ✅ PASS |

### 2.3 Batch result CRUD (delete/favorite/deployed/validation)
| Business Function | DB Method | SQL Pattern | Validation |
|-------------------|-----------|-------------|------------|
| `delete_batch_results()` | `delete_batch_results(ids)` | `DELETE FROM batch_results WHERE id IN (?,...)` | ✅ |
| `set_batch_result_favorite()` | `set_batch_result_favorite(id, bool)` | `UPDATE batch_results SET favorite=? WHERE id=?` | ✅ |
| `set_batch_result_deployed()` | ❌ **MISSING METHOD** | N/A | ❌ FAIL |
| `update_batch_result_validation()` | `update_batch_result_validation(id, status, json)` | `UPDATE batch_results SET validation_status=?, validation_json=? WHERE id=?` | ✅ |
| `purge_batch_results()` | `purge_batch_results(days, metric?)` | `DELETE FROM batch_results WHERE run_timestamp < ? [AND target_metric=?]` | ✅ |
| `get_batch_result()` | `get_batch_result(id)` | `SELECT * FROM batch_results WHERE id = ?` | ✅ |

**Critical Finding:** `set_batch_result_deployed()` is called in `app.py:1449` but **does not exist** in CandleDatabase.

---

## 3. ALERTING SYSTEM (ema_vwap-6ic)

### 3.1 AlertManager.send() → save_alert()
| Field | Details |
|-------|---------|
| **Business Function** | `AlertManager.send()` (alerting.py:210) |
| **DB Method** | `save_alert(alert: dict) -> str` |
| **SQL** | `INSERT INTO alerts (timestamp, type, message, level, source, category, metadata, acknowledged, created_at) VALUES (?,?,?,?,?,?,?,?,?)` |
| **Params** | `(ts, type, message, level, source, category, str(metadata), acknowledged, ts)` |
| **Return Type** | `str` (lastrowid) |
| **Transaction** | Explicit `conn.commit()` in try/finally |
| **Validation** | ✅ PASS - Indexes `idx_alerts_created_at`, `idx_alerts_type`, `idx_alerts_level` exist |

### 3.2 Alert endpoints → get_alerts/acknowledge/delete
| Business Function | DB Method | SQL | Validation |
|-------------------|-----------|-----|------------|
| `GET /api/alerts` | `get_alerts(limit, level, acknowledged)` | `SELECT ... FROM alerts [WHERE level=?] [AND acknowledged=?] ORDER BY timestamp DESC LIMIT ?` | ✅ |
| `PATCH /api/alerts/{id}/acknowledge` | `acknowledge_alert(id)` | `UPDATE alerts SET acknowledged=1, acknowledged_at=datetime('now') WHERE id=?` | ✅ |
| `DELETE /api/alerts/{id}` | `delete_alert(id)` | `DELETE FROM alerts WHERE id=?` | ✅ |

---

## 4. PAPER TRADING - LEGACY LEDGER

### 4.1 PaperLedger methods → save_paper_trade/load_paper_trades
| Business Function | DB Method | SQL | Validation |
|-------------------|-----------|-----|------------|
| `PaperLedger.save_paper_trade()` | `save_paper_trade(...)` | `INSERT INTO paper_ledger (exchange, symbol, timeframe, action, price, quantity, total_usd, timestamp, metadata) VALUES (?,?,?,?,?,?,?,?,?)` | ✅ |
| `PaperLedger.load_paper_trades()` | `load_paper_trades(exchange?, symbol?, limit)` | `SELECT * FROM paper_ledger [WHERE exchange=?] [AND symbol=?] ORDER BY id DESC LIMIT ?` | ✅ |

**Index:** `idx_paper_ledger_lookup` on (exchange, symbol, timestamp)

---

## 5. PAPER TRADING - PER-ASSET PROFILES

### 5.1 PaperProfileRegistry.save_profile() → save_paper_profile()
| Field | Details |
|-------|---------|
| **Business Function** | `PaperProfileRegistry.save_profile()`, `import_batch_result_as_profile()` |
| **DB Method** | `save_paper_profile(exchange, symbol, timeframe, strategy_mode, target_metric, params_json, optuna_score, is_active, deployed, deployed_at, validation_report_json) -> int` |
| **SQL** | `INSERT INTO paper_profiles (...) VALUES (...) ON CONFLICT(exchange, symbol, timeframe, strategy_mode) DO UPDATE SET target_metric=excluded.target_metric, params_json=excluded.params_json, ...` |
| **Params** | 12 params (lowercased exchange/symbol/timeframe/strategy_mode) |
| **Return Type** | `int` (lastrowid) |
| **Transaction** | `@retry_on_db_lock()` + context manager + commit |
| **Validation** | ✅ PASS - UNIQUE constraint exists, index `idx_paper_profiles_lookup` exists |

### 5.2 get_paper_profile / list_paper_profiles
| Business Function | DB Method | SQL | Validation |
|-------------------|-----------|-----|------------|
| `get_profile()` | `get_paper_profile(exchange, symbol, timeframe?, strategy_mode?)` | `SELECT * FROM paper_profiles WHERE exchange=? AND symbol=? [AND timeframe=?] [AND strategy_mode=?] ORDER BY is_active DESC, updated_at DESC LIMIT 1` | ✅ |
| `list_profiles()` | `list_paper_profiles(exchange?, active_only?, deployed_only?)` | `SELECT * FROM paper_profiles [WHERE exchange=?] [AND is_active=1] [AND deployed=1] ORDER BY updated_at DESC` | ✅ |

### 5.3 Profile mutations
| Business Function | DB Method | SQL | Validation |
|-------------------|-----------|-----|------------|
| `delete_profile()` | `delete_paper_profile(id)` | `DELETE FROM paper_profiles WHERE id=?` | ✅ |
| `set_active()` | `set_paper_profile_active(id, is_active, deployed?)` | `UPDATE paper_profiles SET is_active=?, deployed=?, deployed_at=? WHERE id=?` | ✅ |
| `update_trade_direction()` | `update_paper_profile_trade_direction(id, direction)` | `SELECT params_json → UPDATE params_json=? WHERE id=?` | ✅ |

---

## 6. PAPER TRADING - OPEN POSITIONS

### 6.1 PaperLedger.save_position() → save_paper_position()
| Field | Details |
|-------|---------|
| **Business Function** | `PaperLedger.save_position()`, `PaperTradingEngine.evaluate_symbol()` (entry/update), `reconcile_positions_on_startup()` |
| **DB Method** | `save_paper_position(exchange, symbol, side, entry_price, current_price, quantity, cost_basis, stop_loss, take_profit, unrealized_pnl, unrealized_pnl_pct, entry_time, metadata, account_id, last_bar_time) -> int` |
| **SQL** | `INSERT INTO paper_positions (...) VALUES (...) ON CONFLICT(exchange, symbol, account_id) DO UPDATE SET side=excluded.side, entry_price=..., ...` |
| **Params** | 16 params (lowercased exchange/symbol, uppercased side) |
| **Return Type** | `int` (lastrowid) |
| **Transaction** | `@retry_on_db_lock()` + context manager + commit |
| **Validation** | ✅ PASS - UNIQUE(exchange, symbol, account_id) constraint, index exists |

### 6.2 Position queries
| Business Function | DB Method | SQL | Validation |
|-------------------|-----------|-----|------------|
| `get_position()` | `get_paper_position(exchange, symbol, account_id)` | `SELECT * FROM paper_positions WHERE exchange=? AND symbol=? AND account_id=?` | ✅ |
| `list_positions()` | `list_paper_positions(exchange?, account_id?)` | `SELECT * FROM paper_positions [WHERE exchange=?] [AND account_id=?] ORDER BY updated_time DESC` | ✅ |
| `close_position()` | `close_paper_position(exchange, symbol, account_id)` | `DELETE FROM paper_positions WHERE exchange=? AND symbol=? AND account_id=?` | ✅ |

---

## 7. PAPER TRADING - DOUBLE-ENTRY LEDGER

### 7.1 record_ledger_transaction()
| Field | Details |
|-------|---------|
| **Business Function** | `PaperLedger.deposit/withdraw/record_buy/record_sell/record_sell_short/record_close_position()` |
| **DB Method** | `record_ledger_transaction(tx_id, account_id, exchange, symbol, type, amount, asset_qty, price, fee, balance_after, timestamp, notes, metadata) -> int` |
| **SQL** | `INSERT INTO paper_ledger_transactions (transaction_id, account_id, exchange, symbol, type, amount, asset_qty, price, fee, balance_after, timestamp, notes, metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)` |
| **Params** | 13 params |
| **Return Type** | `int` (lastrowid) |
| **Transaction** | `@retry_on_db_lock()` + context manager + commit |
| **Validation** | ✅ PASS - UNIQUE(transaction_id), index `idx_paper_ledger_tx_time` on (account_id, timestamp DESC) |

### 7.2 list_ledger_transactions / get_account_balance
| Business Function | DB Method | SQL | Validation |
|-------------------|-----------|-----|------------|
| `list_transactions()` | `list_ledger_transactions(account_id, exchange?, symbol?, limit)` | `SELECT * FROM paper_ledger_transactions WHERE account_id=? [AND exchange=?] [AND symbol=?] ORDER BY id DESC LIMIT ?` | ✅ |
| `get_balance()` | `get_account_balance(account_id)` | `SELECT balance_after FROM paper_ledger_transactions WHERE account_id=? ORDER BY id DESC LIMIT 1` | ✅ |

### 7.3 reset_paper_account()
| Field | Details |
|-------|---------|
| **Business Function** | `PaperLedger.reset()` |
| **DB Method** | `reset_paper_account(account_id) -> dict[str, int]` |
| **SQL** | 3 DELETEs: `paper_ledger_transactions`, `paper_positions`, `paper_trade_history` |
| **Transaction** | `@retry_on_db_lock()` + single transaction |
| **Validation** | ⚠️ WARN - `paper_trade_history` has no `account_id` column, clears ALL accounts |

---

## 8. PAPER TRADING - COMPLETED TRADE HISTORY

### 8.1 record_paper_trade_history()
| Field | Details |
|-------|---------|
| **Business Function** | `PaperLedger.record_completed_trade()`, `PaperTradingEngine.evaluate_symbol()` (exit), `reconcile_positions_on_startup()` |
| **DB Method** | `record_paper_trade_history(trade_id, exchange, symbol, timeframe, side, entry_price, exit_price, quantity, entry_time, exit_time, holding_period_bars, realized_pnl, realized_pnl_pct, fees, exit_reason, params_json, metadata) -> int` |
| **SQL** | `INSERT INTO paper_trade_history (...) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)` |
| **Params** | 17 params |
| **Return Type** | `int` (lastrowid) |
| **Transaction** | `@retry_on_db_lock()` + context manager + commit |
| **Validation** | ✅ PASS - UNIQUE(trade_id), index `idx_paper_trade_hist_sym` on (exchange, symbol, exit_time DESC) |

### 8.2 list_paper_trade_history / get_paper_trade_statistics
| Business Function | DB Method | SQL | Validation |
|-------------------|-----------|-----|------------|
| `list_trade_history()` | `list_paper_trade_history(exchange?, symbol?, exit_reason?, limit)` | `SELECT * FROM paper_trade_history [WHERE exchange=?] [AND symbol=?] [AND exit_reason=?] ORDER BY id DESC LIMIT ?` | ✅ |
| `get_statistics()` | `get_paper_trade_statistics(exchange?, symbol?)` | Calls `list_paper_trade_history(limit=10000)` then computes in Python | ✅ |

---

## 9. OPTUNA PROFILES (Legacy/General)

### 9.1 save_optuna_profile / load_optuna_profile
| Business Function | DB Method | SQL | Validation |
|-------------------|-----------|-----|------------|
| `OptunaOptimizer` (not directly called currently) | `save_optuna_profile(...)` | `INSERT OR REPLACE INTO optuna_profiles (...) VALUES (?,?,?,?,?,?,?,?)` | ✅ |
| | `load_optuna_profile(...)` | `SELECT * FROM optuna_profiles WHERE exchange=? AND symbol=? AND timeframe=? AND strategy_mode=? AND target_metric=?` | ✅ |

**Index:** `idx_optuna_profiles_lookup` on (exchange, symbol, timeframe)  
**Migration:** Rebuilds table if `best_params` column missing

---

## 10. PORTFOLIO BALANCES

### 10.1 update_portfolio_balance / get_portfolio_balances
| Business Function | DB Method | SQL | Validation |
|-------------------|-----------|-----|------------|
| Portfolio aggregator | `update_portfolio_balance(currency, total, available, updated_at)` | `INSERT OR REPLACE INTO portfolio_balances (currency, total, available, updated_at) VALUES (?,?,?,?)` | ✅ |
| Portfolio aggregator | `get_portfolio_balances()` | `SELECT * FROM portfolio_balances` | ✅ |

**Primary Key:** `currency`  
**Migration:** Rebuilds if old schema (account_id, balances_json)

---

## 11. BACKGROUND TASKS

### 11.1 TaskManager methods → background_tasks table
| Business Function | DB Method | SQL | Validation |
|-------------------|-----------|-----|------------|
| `create_task()` | `save_background_task(task_data)` | `INSERT OR REPLACE INTO background_tasks (task_id, name, status, progress_pct, step_description, start_time, end_time, error, result_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)` | ✅ |
| `update_progress()` | `save_background_task()` (upsert) | Same as above | ✅ |
| `complete_task()` | `save_background_task()` (upsert) | Same | ✅ |
| `fail_task()` | `save_background_task()` (upsert) | Same | ✅ |
| `get_task()` | `get_background_task(task_id)` | `SELECT * FROM background_tasks WHERE task_id=?` | ✅ |
| `list_tasks()` | `list_background_tasks(limit, status?)` | `SELECT * FROM background_tasks [WHERE status=?] ORDER BY created_at DESC LIMIT ?` | ✅ |

**Index:** `idx_background_tasks_created` on (created_at DESC)  
**Note:** `prune_background_tasks()` referenced in `complete_task()` but **does not exist** in CandleDatabase.

---

## 12. SYSTEM SETTINGS

### 12.1 SettingsManager methods → settings table
| Business Function | DB Method | SQL | Validation |
|-------------------|-----------|-----|------------|
| `get()` | `get_setting(key)` | `SELECT * FROM settings WHERE key=?` | ✅ |
| `set()` | `save_setting(key, value, is_secret)` | `INSERT OR REPLACE INTO settings (key, value, is_secret, updated_at) VALUES (?,?,?,?)` | ✅ |
| `get_all_masked()` | `load_all_settings()` | `SELECT * FROM settings` | ✅ |

**Primary Key:** `key`  
**Transaction:** `@retry_on_db_lock()` on save

---

## 13. DATABASE MAINTENANCE

### 13.1 get_db_info / repair_database / vacuum_database
| Business Function | DB Method | SQL | Validation |
|-------------------|-----------|-----|------------|
| `GET /api/db/info` | `get_db_info()` | `PRAGMA integrity_check`, `PRAGMA journal_mode`, `SELECT name FROM sqlite_master`, `SELECT COUNT(*) FROM candles` | ✅ |
| `POST /api/db/repair` | `repair_database(db_path?)` | Header check + `PRAGMA integrity_check` + quarantine + `init_db()` | ✅ |
| `POST /api/db/vacuum` | `vacuum_database()` | `VACUUM` | ✅ |

---

## 14. INITIALIZATION & MIGRATIONS

### 14.1 init_db() - Schema Creation
**Creates all tables with proper indexes in single transaction:**

| Table | Primary Key | Key Indexes |
|-------|-------------|-------------|
| `candles` | (exchange, symbol, timeframe, timestamp) | `idx_candles_lookup` on (exchange, symbol, timeframe, timestamp) |
| `batch_results` | id AUTOINCREMENT | `idx_batch_results_metric` on (target_metric, best_value DESC) |
| `paper_ledger` | id AUTOINCREMENT | `idx_paper_ledger_lookup` on (exchange, symbol, timestamp) |
| `paper_profiles` | id AUTOINCREMENT + UNIQUE(exchange, symbol, timeframe, strategy_mode) | `idx_paper_profiles_lookup` on (exchange, symbol, is_active) |
| `paper_positions` | id AUTOINCREMENT + UNIQUE(exchange, symbol, account_id) | `idx_paper_positions_sym` on (exchange, symbol, account_id) |
| `paper_ledger_transactions` | id AUTOINCREMENT + UNIQUE(transaction_id) | `idx_paper_ledger_tx_time` on (account_id, timestamp DESC) |
| `paper_trade_history` | id AUTOINCREMENT + UNIQUE(trade_id) | `idx_paper_trade_hist_sym` on (exchange, symbol, exit_time DESC) |
| `optuna_profiles` | id AUTOINCREMENT + UNIQUE(exchange, symbol, timeframe, strategy_mode, target_metric) | `idx_optuna_profiles_lookup` on (exchange, symbol, timeframe) |
| `portfolio_balances` | currency | - |
| `alerts` | id AUTOINCREMENT | `idx_alerts_created_at`, `idx_alerts_type`, `idx_alerts_level` |
| `background_tasks` | task_id | `idx_background_tasks_created` on (created_at DESC) |
| `settings` | key | - |

**Journal Mode:** DELETE (for Docker/Windows compatibility), WAL otherwise  
**PRAGMAs:** `synchronous=NORMAL`, `cache_size=-64000` (64MB), `temp_store=MEMORY`

---

## 15. MISSING / BROKEN MAPPINGS

| Missing Method | Called From | Impact |
|----------------|-------------|--------|
| `set_batch_result_deployed(result_id, deployed)` | `app.py:1449` (PATCH /api/batch_results/{id}/deployed) | ❌ **CRITICAL** - Endpoint will fail with AttributeError |
| `prune_background_tasks(keep_limit)` | `tasks.py:96` (TaskManager.complete_task) | ⚠️ **WARNING** - Silently caught, no pruning occurs |

---

## 16. PARAMETER NORMALIZATION AUDIT

| Parameter | Normalization Applied | Consistent? |
|-----------|----------------------|-------------|
| `exchange` | `.lower()` | ✅ All methods |
| `symbol` | `normalize_symbol_key()` (upper, / standardization) | ✅ All methods |
| `timeframe` | `.lower()` | ✅ All methods |
| `strategy_mode` | `.lower()` | ✅ Most methods |
| `side` (position) | `.upper()` | ✅ Position methods |
| `tx_type` (ledger) | `.upper()` | ✅ Ledger methods |
| `exit_reason` | `.upper()` | ✅ Trade history |

---

## 17. TRANSACTION HANDLING SUMMARY

| Pattern | Methods Using | Safe? |
|---------|---------------|-------|
| `@retry_on_db_lock()` + `with self.get_connection() as conn: ... conn.commit()` | Most write methods | ✅ |
| Read-only `with self.get_connection() as conn: ...` | All SELECT methods | ✅ |
| Explicit `conn = get_connection(); try: ... finally: conn.close()` | get_db_info, export_*, repair_database | ⚠️ No retry |
| Single transaction multi-statement | save_batch_result, reset_paper_account | ✅ |
| No transaction (DDL) | init_db (uses context manager) | ✅ |

---

## 18. INDEX COVERAGE VERIFICATION

| Query Pattern | Index Exists? | Notes |
|---------------|---------------|-------|
| `candles` by (exchange, symbol, timeframe, timestamp) | ✅ `idx_candles_lookup` | Covers load_candles, save_candles upsert |
| `batch_results` by target_metric + best_value | ✅ `idx_batch_results_metric` | Covers leaderboard sort |
| `paper_ledger` by (exchange, symbol, timestamp) | ✅ `idx_paper_ledger_lookup` | Covers load_paper_trades |
| `paper_profiles` by (exchange, symbol, is_active) | ✅ `idx_paper_profiles_lookup` | Covers list_profiles |
| `paper_positions` by (exchange, symbol, account_id) | ✅ `idx_paper_positions_sym` | Covers get/list/close position |
| `paper_ledger_transactions` by (account_id, timestamp DESC) | ✅ `idx_paper_ledger_tx_time` | Covers list_transactions, get_balance |
| `paper_trade_history` by (exchange, symbol, exit_time DESC) | ✅ `idx_paper_trade_hist_sym` | Covers list_trade_history |
| `optuna_profiles` by (exchange, symbol, timeframe) | ✅ `idx_optuna_profiles_lookup` | Covers load_optuna_profile |
| `alerts` by created_at DESC | ✅ `idx_alerts_created_at` | Covers get_alerts |
| `alerts` by type | ✅ `idx_alerts_type` | Filter support |
| `alerts` by level | ✅ `idx_alerts_level` | Filter support |
| `background_tasks` by created_at DESC | ✅ `idx_background_tasks_created` | Covers list_tasks |

---

## 19. VALIDATION SUMMARY

| Category | Total Methods | PASS | WARN | FAIL |
|----------|--------------|------|------|------|
| Candle Operations | 5 | 5 | 0 | 0 |
| Batch Optimizer | 7 | 6 | 0 | 1 |
| Alerting | 4 | 4 | 0 | 0 |
| Paper Ledger (Legacy) | 2 | 2 | 0 | 0 |
| Paper Profiles | 6 | 6 | 0 | 0 |
| Paper Positions | 4 | 4 | 0 | 0 |
| Double-Entry Ledger | 3 | 2 | 1 | 0 |
| Trade History | 3 | 3 | 0 | 0 |
| Optuna Profiles | 2 | 2 | 0 | 0 |
| Portfolio Balances | 2 | 2 | 0 | 0 |
| Background Tasks | 6 | 5 | 1 | 0 |
| Settings | 3 | 3 | 0 | 0 |
| Maintenance | 3 | 3 | 0 | 0 |
| **TOTAL** | **50** | **47** | **2** | **1** |

---

## 20. RECOMMENDED FIXES

### Critical (Blocking)
1. **Add `set_batch_result_deployed(result_id: int, deployed: bool) -> bool`** to CandleDatabase
   - SQL: `UPDATE batch_results SET deployed = ? WHERE id = ?`
   - Called from `app.py:1449`

### High Priority
2. **Add `prune_background_tasks(keep_limit: int = 50) -> int`** to CandleDatabase
   - SQL: `DELETE FROM background_tasks WHERE task_id NOT IN (SELECT task_id FROM background_tasks ORDER BY created_at DESC LIMIT ?)`
   - Called from `tasks.py:96`

### Medium Priority
3. **Push LIMIT into SQL for `load_candles()`** - Currently applies `.tail(limit)` in Python after loading all rows
   - Issue: `ema_vwap-7dp.3` already addresses this
   - SQL: Add `LIMIT ?` to query

4. **Add `account_id` to `paper_trade_history`** - Currently `reset_paper_account()` clears ALL accounts' trade history
   - Requires migration to add column + index

---

## 21. SCHEMA MIGRATION STATUS

All migrations in `init_db()` are **complete and idempotent**:

| Migration | Status |
|-----------|--------|
| `batch_results`: favorite, validation_status, validation_json | ✅ Applied via ALTER TABLE |
| `paper_profiles`: deployed, deployed_at, validation_report_json | ✅ Applied via ALTER TABLE |
| `paper_profiles`: UNIQUE constraint rebuild | ✅ Rebuild via rename/recreate |
| `paper_positions`: account_id, last_bar_time + UNIQUE rebuild | ✅ Rebuild via rename/recreate |
| `optuna_profiles`: rebuild to match DDL | ✅ DROP + CREATE |
| `portfolio_balances`: rebuild to per-currency schema | ✅ DROP + CREATE |
| `alerts`: alert_type→type + missing columns | ✅ ALTER TABLE + UPDATE |

---

**Report Complete.** All 50 public CandleDatabase methods traced to business callers with SQL, parameters, return types, transaction handling, and validation status.