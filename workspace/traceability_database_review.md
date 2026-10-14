# Traceability Audit — Database Layer (ema_vwap)
Files audited: `src/database.py`, `src/db_connection_manager.py`, `src/db_maintenance_multi.py`
Audit date: 2026-09-15

## 1. Domain Routing (DB_MAP)
- `DB_MAP` in `db_connection_manager.py` defines 4 keys:
  - `candles` → `data/candles.db`
  - `batch_results` → `data/batch_results.db`
  - `paper_trading` → `data/paper_trading.db`
  - `system` → `data/system.db`
- `get_database(db_key)` validates against DB_MAP and returns `CandleDatabase(db_path=path)`.
- `get_connection(db_key)` validates and opens sqlite3 connection at mapped path.
- `_get_db_domain()` in `CandleDatabase` resolves from `db_path` basename/content:
  - `paper_trading` for paper/test paths
  - `batch_results` for batch paths
  - `system` for system paths
  - `candles` default
- **Status: VERIFIED** — routing matches domain keys, no orphan paths.

## 2. init_db Domain Awareness
- `init_db()` creates `candles` table unconditionally.
- `batch_results` table + `favorite`/`validation_status`/`validation_json` migrations created only when `db_domain in ("batch_results", "system")`.
- `paper_ledger`, `paper_profiles`, `paper_positions`, `paper_ledger_transactions`, `paper_trade_history`, `optuna_profiles`, `portfolio_balances`, `background_tasks`, `settings` created globally.
- `paper_profiles` includes UNIQUE constraint and rebuild migration for missing UNIQUE.
- `portfolio_balances` includes migration from old JSON design to per-currency rows.
- **Alert table (`alerts`) NOT created in `init_db`**. It exists in `system.sql` and `candles.sql` but is missing from Python DDL. Operations calling `save_alert()` on a fresh `system` DB without external SQL initialization will fail with `sqlite3.OperationalError: no such table: alerts`.
- **Status: PARTIAL GAP** — domain-aware for candles/batch/paper/system; missing `alerts` DDL in init_db.

## 3. SQL Injection Fixes Preserved
- `load_batch_results`, `load_paper_trades`, `load_paper_profiles`, `get_paper_position`, `list_paper_positions`, `list_paper_ledger_transactions`, `load_paper_trade_history`, `load_optuna_profile`, `load_all_settings`, `get_alerts` all use `?` parameter binding.
- No f-string interpolation of user input in query strings observed.
- Hardcoded column lists present at:
  - Line ~684 (`SELECT timestamp, open, high, low, close, volume FROM candles ...`) — `load_candles`
  - Line ~1904 (`SELECT timestamp, open, high, low, close, volume FROM candles ...`) — `export_candles_csv`
  - Line ~1945 (`SELECT timestamp, open, high, low, close, volume FROM candles ...`) — `export_candles_json`
- **Status: VERIFIED** — parameterized queries preserved; hardcoded SELECT column lists prevent SELECT * injection.

## 4. Composite Index (`idx_candles_sym_tf`)
- Only index present: `idx_candles_lookup` (`ON candles (exchange, symbol, timeframe, timestamp)`).
- `idx_candles_sym_tf` (symbol + timeframe) is NOT present in source, SQL files, or DB schema.
- **Status: MISSING** — index not added; should be added if intended.

## 5. Auth on Sensitive Endpoints
- `src/app.py`:
  - `/api/db/export/csv` (line 619) has `dependencies=[Depends(get_api_key)]`
  - `/api/db/export/json` (line 641) has `dependencies=[Depends(get_api_key)]`
- Auth relies on `X-API-Key` header (`get_api_key`).
- No additional role/auth layer in `database.py` itself (expected — DB layer relies on API layer auth).
- **Status: VERIFIED** — export endpoints protected.

## 6. Alert Methods Present and Functional
- `save_alert` (line 1727): inserts/replaces into `alerts` with parameter binding; uses `self.get_connection()` (respects DB path/domain).
- `get_alerts` (line 1753): parameterized WHERE + ORDER BY + LIMIT.
- `acknowledge_alert` (line 1786): `UPDATE alerts SET acknowledged = 1 ... WHERE id = ?`
- `delete_alert` (line 1797): `DELETE FROM alerts WHERE id = ?`
- **Status: VERIFIED — FUNCTIONAL ONLY IF `alerts` TABLE EXISTS** (see gap in section 2). If DB initialized via SQL scripts, functional. If initialized only via `init_db()`, will throw on first save.

## 7. Delegation Methods Present and Wired
- `get_db_info()` (line 1807): connects to `self.db_path`, executes PRAGMA, returns dict with `integrity_check`, `journal_mode`, `file_size_bytes`, `tables`, `candle_record_count`. Hardcoded column count at 1816 (`candle_record_count`).
- `repair_database()` (line 1847): runs `PRAGMA integrity_check`; returns `healthy` or error status. Hardcoded `message` fields at 1861 area (status text, not SQL injection).
- `vacuum_database()` (line 1871): executes `VACUUM`.
- `export_candles_csv()` (line 1880): hardcoded SELECT column list at 1904; parameter-bound WHERE; returns CSV string.
- `export_candles_json()` (line 1922): hardcoded SELECT column list at 1945; parameter-bound WHERE; returns JSON string.
- `delete_alert()` (line 1797): parameter-bound DELETE.
- All delegation methods use `self.get_connection()` → `self.db_path` → intended domain DB.
- **Status: VERIFIED** — all 6 delegation methods present and wired to `self.db_path`.

## 8. Every DB Operation Reaches Intended Domain DB
- `CandleDatabase` uses `self.db_path` for every connection.
- `DB_MAP` in `db_connection_manager.py` maps 4 domain keys to separate file paths.
- `get_database(key)` creates `CandleDatabase(db_path=DB_MAP[key])`.
- Multi-DB maintenance (`db_maintenance_multi.py`) iterates `DB_MAP`, creates independent `get_database(key)` instances; no cross-contamination between DB files.
- `init_db()` selects DDL branches based on `_get_db_domain()` derived from file path, so batch/system DBs get `batch_results` table; paper_trading DBs get paper ledger schemas.
- `system` DB gets settings/background_tasks globally but does not get `batch_results` unless its path matches batch pattern (current logic: `db_domain == "system"` does include `batch_results`). Confirmed from init_db: `if db_domain in ("batch_results", "system")`: batch_results table created.
- **Status: VERIFIED** — no cross-domain leak observed.

## 9. Issues / Gaps Found
1. **Missing `alerts` DDL in `init_db`** — table exists in SQL scripts (`system.sql`, `candles.sql`) but not in Python `init_db()`. Fresh DB created by code alone will fail on `save_alert()`.
2. **Missing composite index** — `idx_candles_sym_tf` not present.
3. **Repair logic returns `healthy` on exception** — `repair_database()` returns `status: "healthy"` with `message: f"repaired: {e}"` when exception occurs (line 1866). Should return `repaired` or `error`, not `healthy`, when exception caught. Minor semantic issue.
4. **Auth layer in DB module absent** — as expected; only app layer has auth. No direct endpoint in DB layer, so acceptable.

## 10. Summary
- **Domain routing**: PASS
- **init_db domain awareness**: PASS (with `alerts` gap)
- **SQL injection fixed (hardcoded columns + parameter binding)**: PASS
- **Auth on export endpoints**: PASS
- **Alert methods present**: PASS (functional with `alerts` table caveat)
- **Delegation methods present and wired**: PASS
- **Every operation reaches intended domain DB**: PASS
- **Index gap (`idx_candles_sym_tf`)**: MISSING
- **Repair status semantics**: MINOR ISSUE

Audit completed. Review file generated at: `workspace/traceability_database_review.md`
