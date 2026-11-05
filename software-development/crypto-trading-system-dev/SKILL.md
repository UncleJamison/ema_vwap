---
name: crypto-trading-system-dev
description: "Develop crypto trading systems with EMA/VWAP strategies."
version: 1.0.0
---

# Crypto Trading System Development

This skill covers the end-to-end workflow for building and maintaining institutional-grade cryptocurrency trading systems using Python/FastAPI with EMA+VWAP strategies, Monte Carlo robustness validation, portfolio risk budgeting, and real-time WebSocket dashboards.

## Scope

- Strategy development: EMA crossover, multi-EMA, VWAP pullback, custom indicators
- Robustness validation: 3-gate pipeline (backtest → walk-forward → Monte Carlo)
- Portfolio risk: risk parity weights, per-asset max drawdown constraints, correlation-aware position sizing, portfolio-level Monte Carlo stress testing
- Real-time dashboard: WebSocket streaming P&L, positions, risk metrics, paper/live engine integration
- Tear-sheet generation: PDF/HTML/JSON export with KPIs, equity curves, drawdown histograms
- Task tracking: bd (beads) for all issue tracking

## Development Workflow

### 1. Environment Setup

```bash
# Project root
cd /c/crypto/ema_vwap

# Activate venv (if not already active)
source .venv/bin/activate  # Linux/macOS
# or
.venv\Scripts\activate  # Windows

# Install deps
pip install -r requirements.txt

# Run tests
python -m pytest tests/ -k "not test_validator_mc_risk_custom_criteria_overrides_settings" --tb=short

# Lint
python -m ruff check src/ tests/
python -m ruff check --fix src/ tests/
```

### 2. Task Tracking with bd (beads)

```bash
# Find available work
bd ready

# View issue details
bd show <id>

# Claim work atomically
bd update <id> --claim

# Complete work
bd close <id> --reason="Description of what was done"

# List all issues
bd list

# Create new issue
bd create --title="Title" --description="Details" --type=feature --priority=2
```

**Rules:**
- Use `bd` for ALL task tracking — never markdown TODOs
- Run `bd prime` for detailed command reference
- Use `bd remember` for persistent knowledge
- Close beads with `--reason` summarizing what was done

**Canonical Symbol Mapping Beads** — when implementing the canonical symbol mapping system, create a single bead for the full implementation (settings, data_loader, routes, API integration, tests) rather than splitting across multiple beads. The system is tightly coupled across settings, data_loader, and multiple route files.

**Stop Loss Management Fix Beads** — when fixing stop loss management, include all related fixes in one bead: serialization bug, regime awareness, trailing stop cap, paper engine heuristic, and backtester hardcoded cap. These are interdependent — fixing one without the others leaves the system in a broken state.

### 3. Code Changes — Non-Destructive with Checkpoints

Hermes checkpoints are enabled globally (`hermes config set checkpoints.enabled true`). When modifying code:

1. Read the file first with `read_file`
2. Make targeted edits with `patch` (preferred) or `write_file`
3. Run tests immediately after each change
4. If tests fail, fix iteratively — don't batch multiple failing changes

### 4. Testing Protocol

```bash
# Full test suite (excludes known Windows file-lock flake)
python -m pytest tests/ -k "not test_validator_mc_risk_custom_criteria_overrides_settings" --tb=short

# Specific test file
python -m pytest tests/test_portfolio_risk_budget.py -v

# Single test
python -m pytest tests/test_validator.py::test_validator_full_pipeline_robust -v
```

**Gates:** All new code must pass:
- `ruff check src/ tests/` — zero new warnings
- `pytest` — 100% pass rate (excluding known flakes)

**Test Reliability Patterns:**
- **Flaky optimizer tests**: Increase `n_trials` from 5 to 15+ in basic optimizer tests. With only 5 trials, Optuna may not explore enough to find valid params (especially `fast_ema >= 5` from min 3), causing intermittent failures. Use `n_trials=15` in `tests/test_optimizer.py::test_optuna_optimizer_basic`.
- **Windows temp file cleanup**: `PermissionError` on temp DB files is common. Use `try/except PermissionError: pass` for best-effort cleanup in test fixtures. Don't assert cleanup success.
- **WeasyPrint mocking for Windows**: GTK libraries unavailable on Windows. Add module-level mock in `tests/conftest.py` before any test imports `pdf_renderer`:
  ```python
  import sys
  from unittest.mock import MagicMock
  weasyprint_mock = MagicMock()
  weasyprint_mock.HTML = MagicMock()
  weasyprint_mock.text = MagicMock()
  weasyprint_mock.text.fonts = MagicMock()
  weasyprint_mock.text.fonts.FontConfiguration = MagicMock()
  weasyprint_mock.css = MagicMock()
  sys.modules["weasyprint"] = weasyprint_mock
  sys.modules["weasyprint.text"] = weasyprint_mock.text
  sys.modules["weasyprint.text.fonts"] = weasyprint_mock.text.fonts
  sys.modules["weasyprint.css"] = weasyprint_mock.css
  ```
- **Test expectation consistency**: When adding validation for multi-timeframe requests (e.g., `all_metrics`, `all_combinations`), ensure existing tests expecting 200 are updated to expect 400 if the validation now rejects multi-timeframe. Consistency between frontend (app.js line 3515) and backend (batch.py line 149/187) is critical.

### 5. Linting

```bash
# Check
python -m ruff check src/ tests/

# Auto-fix
python -m ruff check --fix src/ tests/

# Format
python -m ruff format src/ tests/
```

### Ruff Cleanup Workflow

1. Run `python -m ruff check . --fix` from the project root and preserve the exact output.
2. Re-run `python -m ruff check .` after auto-fixing. Separate remaining issues by file, code, and whether they are new or pre-existing.
3. For `DTZ003`, replace `datetime.utcnow()` with `datetime.now(timezone.utc)` and verify `timezone` is imported.
4. For `F821` introduced by that change, search every affected reference and add the missing import (for example, `json` for `json.loads`).
5. For `E402`, place imports immediately after the module docstring. Never leave executable code, including `if __name__ == "__main__"` blocks, between import groups.
6. For `I001`, organize the import block; rerun the check after each file change.
7. Treat `BLE001` as a policy decision: prefer specific exceptions in new code, but preserve intentional resilience boundaries when the user asks to leave residual warnings. Report the remaining count and scope instead of silently refactoring them.
8. After lint edits, run tests and report exact pass/skip/deselect counts. Never report a run as clean until the final Ruff command exits successfully.

See `references/ruff-cleanup.md` for the triage recipe, `references/regime-classifier.md` for the three-regime design spec.

### 6. Database & Environment Patterns

**PostgreSQL SSL Connection** — Local development often fails with `ConnectionError: PostgreSQL server at "localhost:5432" rejected SSL upgrade`. Fix with:
```bash
PG_SSL_MODE=disable python -m uvicorn src.app:app --host 127.0.0.1 --port 8080
```
Or ensure PostgreSQL is configured with SSL disabled for local connections.

**FastAPI Test Client vs Uvicorn Server** — The FastAPI `TestClient` works with mocked PostgreSQL (via pytest `mock_pg_database` fixture). The uvicorn server requires a real, running PostgreSQL instance. If tests pass but the browser gets 500 errors, check PostgreSQL connectivity.

**Docker Desktop** — `restart.bat` and `docker compose` commands fail if Docker Desktop is not running. Verify with `docker ps` before running restart scripts.

**PYTHONPATH Shadowing** — On Windows, shell PYTHONPATH may point to Python 3.14 site-packages that shadow the 3.11 venv and break numpy/pandas imports (`No module named numpy._core._multiarray_umath`). Fix:
```bash
# Prefix commands to clear PYTHONPATH
env PYTHONPATH= python -m pytest tests/test_canonical_symbol_mapping.py
```
Or reinstall numpy with `--ignore-installed` if corrupted.

**Docker Missing Dependencies** — OpenTelemetry instrumentation errors at startup often indicate missing Python packages in the container. Example: `HTTPXClientInstrumentor only instruments "httpx >= 0.18.0", but no installed version was found` → add `httpx>=0.28.0` to `requirements.txt` and rebuild.

**Multi-Database Architecture** — The project uses separate PostgreSQL databases for different domains (configured in `docker-compose.yml` and `src/pg_database.py`):
- `ema_vwap` — main app settings, users, auth
- `ema_vwap_candles` — candle data (TimescaleDB hypertables)
- `ema_vwap_batch_results` — batch optimization leaderboard
- `ema_vwap_paper_trading` — paper profiles, positions, ledger
- `ema_vwap_system` — audit logs, migrations
When querying via `asyncpg`, connect to the correct database name. The `get_pool(db_name)` in `pg_database.py` manages pools per database.

**Canonical Symbol Mapping System** — Implemented in `src/settings.py`, `src/data_loader.py`, and route files:
- `DEFAULT_CANONICAL_SYMBOL_MAPPING` in settings.py maps canonical symbols ("BTC/USD") to exchange-specific symbols
- `SettingsManager.resolve_exchange_symbol(canonical, live_mode)` and `resolve_all_canonical(live_mode)` methods
- `DataLoader.resolve_canonical_symbol(canonical, live_mode)` static method used by route handlers
- History/backtest mode (`live_mode=False`) → KuCoin ("BTC/USD")
- Live/paper mode (`live_mode=True`) → Gemini ("BTC/USDT")
- Stock symbols ("AAPL") → synthetic_stock for history, Gemini for live
- Unknown symbols fall back to default exchanges
- All 6 API endpoints integrated: backtest, optimize, WFO, Monte Carlo, sync_history, tear_sheet
- UI-editable via `PUT /api/settings/canonical_symbols` (stored in PostgreSQL settings table)

## References

### Regime-Detection Integration (adaptive strategy switching)

- Three-regime classifier: `trending` (ADX ≥ 25 + ER ≥ 0.5), `ranging` (ADX < 25 + low ATR%), `high_vol` (elevated ATR%). Built from scratch in `src/regime.py` using only pandas/numpy (no TA-Lib).
- Config-driven mapping via `REGIME_MAP`: `trending→multi_ema`, `ranging→pullback`, `high_vol→crossover`; `RegimeConfig` dataclass holds thresholds (ADX=25, ER=0.5, ATR mult=1.5, hysteresis=3 bars).
- Hysteresis is mandatory: regime only flips after 3 consecutive bars in new regime (prevents whipsaw at ADX≈25). Implemented via `apply_hysteresis()` vectorized over the series.
- `get_current_regime()` wrapper for live paper engine; `classify_regime()` returns `INSUFFICIENT_DATA` for warmup (< 60 bars).
- Integration with optimizer's `"auto"` mode: `OptunaConfig` gains `regime_sweep_enabled`, `regime_sweep_days` (default 180), `regime_sweep_n_jobs`; optimizer trial space picks `strategy_mode` per trial (existing `optimizer.py` lines 284–289 already do this).
- **Phase 4: Multi-timeframe regime classification (Option A — 1m for classification, 5m for trading):**
  - `RegimeConfig` gains `min_regime_persistence_bars` (default 3, overrides `hysteresis_bars` when set) — persistence smoothing reduces regime whipsaw in WFO windows.
  - `data_loader.py`: `fetch_1m_for_regime_classify(exchange, symbol, days)` — fetches 1m candles (cached after first fetch; acceptable overhead confirmed).
  - `backtester.py`: In `run()`, when `params.use_multi_timeframe_regime=True`, fetches 1m via `fetch_1m_for_regime_classify()` and passes to strategy via `params._regime_df_1m`.
  - `strategy.py`: In `generate_signals()`, checks `params._regime_df_1m`; if present, runs `classify_regime()` on 1m data, resamples to 5m via `.resample("5min").last().ffill()`, aligns to 5m index via `.reindex(df.index, method="ffill")`.
  - Verified: LTC/USD 15m backtest with `use_multi_timeframe_regime=True` completes successfully (17 trades, no errors).
  - 1m data cached after first fetch — subsequent runs use cached data (acceptable overhead per user approval).
- Persistence smoothing: `RegimeConfig.min_regime_persistence_bars` (default 3) — regime only flips after N consecutive bars in new regime. Used in `classify_regime()` via `persistence_bars = getattr(config, "min_regime_persistence_bars", config.hysteresis_bars)`.
- Integration with optimizer's `"auto"` mode: `OptunaConfig` gains `regime_sweep_enabled`, `regime_sweep_days` (default 180), `regime_sweep_n_jobs`; optimizer trial space picks `strategy_mode` per trial (existing `optimizer.py` lines 284–289 already do this).
- Pitfall: synthetic test data thresholds differ from real crypto — new `tests/test_regime.py` tests may fail on synthetic fixtures; fix by adjusting `RegimeConfig` thresholds or synthetic noise rather than structural changes. Always verify `compute_adx()` / `compute_efficient_ratio()` independently with a pure linear-trend fixture before adjusting thresholds.
- Design bead drives feature bead: always create/confirm design bead before coding feature bead; maintain `bd` tracking for both.

### Key Architectural Patterns

### Key Architectural Patterns

### FastAPI Endpoints
- All endpoints in `src/app.py`
- Use Pydantic models for request/response validation
- Catch specific exceptions, not bare `Exception` (BLE001)
- Use `datetime.now(timezone.utc)` not `datetime.utcnow()` (DTZ003)
- **Long-running operations must be async**: endpoints that take >1s (optimization, batch sweep, Monte Carlo) must return `{"status": "started", "task_id": ...}` immediately and dispatch via `task_manager.run_in_background()`. Updating existing tests to expect the new async response shape is part of the fix.
- **Enqueue failure propagation**: When `OptunaConfig.seed_base_params=True`, the optimizer's `optimize()` captures `enqueue_failure` in its result dict (lines 612–616 of `optimizer.py`). This flows end-to-end as: `optimize()` → `result_json` (task DB) → `/api/tasks/{task_id}` response → UI poll. The UI can surface `enqueue_failure` status to the user; previously only `status: "completed"` was visible. **Rule**: Never let `except Exception: pass` silently swallow errors when the UI layer needs visibility.
- **Batch sweep multi-metric expansion**: The `/api/batch_optimize` endpoint handles special target metrics by expanding them into multiple `BatchOptimizerConfig` objects:
  - `all_metrics` → expands to one config per metric in `ALL_SINGLE_METRICS` (12 metrics), each with `enable_multi_objective=False`
  - `all_combinations` → expands to one config per pair in `ALL_CURATED_MULTI_PAIRS` (13 curated pairs), each with `enable_multi_objective=True` and `multi_objective_metrics=pair`
  - Both require exactly one timeframe (validated at line 149 and 187 of `src/routes/batch.py`); multi-timeframe requests return HTTP 400. The frontend validates this at `src/web/app.js` line 3515.
  - Each expanded config is dispatched via `_run_all_metrics_batch_task` (not the regular `_run_batch_task`) to track combo labels correctly.
  - **Pitfall**: Passing `all_combinations` directly to `OptunaOptimizer` fails — it's not in `_TARGET_METRICS`. The expansion must happen at the route level before creating `OptunaConfig`.

### Validation Pipeline
- Gate 1: In-sample backtest viability
- Gate 2: Walk-forward out-of-sample efficiency
- Gate 3: Monte Carlo sequence stress & tail risk of ruin
- `StrategyValidator` in `src/validator.py` orchestrates all gates

### Database Migrations (SQLite)
- **Add-column migrations**: Use `PRAGMA table_info(table_name)` to check column existence before `ALTER TABLE ADD COLUMN`. Run inside `init_db()` so existing deployments auto-migrate on startup.
  ```python
  pp_cols = {row[1] for row in cursor.execute("PRAGMA table_info(paper_profiles)").fetchall()}
  if "validation_report_json" not in pp_cols:
      cursor.execute("ALTER TABLE paper_profiles ADD COLUMN validation_report_json TEXT")
  ```
- **Upsert with new columns**: When adding columns, update the `INSERT ... ON CONFLICT DO UPDATE SET` clause to include the new column in both the INSERT values and the UPDATE SET list (see `save_paper_profile` in `database.py` line 1665).
- **Migration ordering**: Run migrations before any queries that depend on new columns. Wrap in `try/except` and log completion.
- **Pitfall**: Forgetting to add the column to the ON CONFLICT UPDATE SET clause means existing rows won't get the new column on update, only on insert.

### Portfolio Risk Budgeting
- `PortfolioRiskBudgetEngine` in `src/portfolio/portfolio_risk_budget.py`
- Risk parity via iterative proportional fitting on covariance matrix
- Per-asset max drawdown constraints with proportional scaling
- Portfolio-level Monte Carlo via Cholesky-decorrelated multivariate simulation

### WebSocket Real-Time Dashboard
- Endpoint: `/ws/dashboard` in `src/app.py`
- Manager: `WebSocketManager` in `src/ws_server.py`
- Client: `src/web/ws_dashboard.js` with auto-reconnect
- Message types: `init`, `status_update`, `positions_update`, `engine_step_result`, `pong`
- **Frontend event listener registration**: Button click handlers for deploy buttons (`deploy-opt-paper-btn`, `deploy-backtest-paper-btn`) must be registered **inside** the `DOMContentLoaded` handler where the button elements are retrieved. The variables are block-scoped (`const`) and inaccessible outside the callback. Duplicate listeners registered outside the callback (where variables don't exist) will never fire.
  - Correct pattern (inside `DOMContentLoaded`):
    ```javascript
    const deployOptBtn = document.getElementById("deploy-opt-paper-btn");
    if (deployOptBtn) {
      deployOptBtn.addEventListener("click", () => deployOptunaToPaper());
    }
    ```
  - **Pitfall**: Registering listeners outside `DOMContentLoaded` where `const` variables are out of scope — event listeners never attach, buttons appear "broken".

### Tear-Sheet Generation
- Template: `src/web/tear_sheet_template.html` (Jinja2)
- Renderer: `src/web/pdf_renderer.py` (WeasyPrint)
- Endpoints: `/api/tear-sheet/pdf`, `/html`, `/json`
- Client tab: `src/web/ws_dashboard.js` → Tear Sheet tab
- **Validation report persistence**: The `paper_profiles` table includes `validation_report_json` column to store the full validation report (Gate 1 + Gate 3 results) when deploying from backtest page. This enables the deployed profile to carry its validation context for audit and re-deployment. The column is populated via `save_paper_profile` (database.py line 1665) and read back via `get_paper_profile`. The frontend sends `validation_report` in the deploy payload (app.js line 4367).
- **Pitfall**: Missing `validation_report_json` column in schema or missing it in the ON CONFLICT UPDATE SET clause causes silent data loss on update.

## Common Pitfalls

| Pitfall | Fix |
|---------|-----|
| `AttributeError: 'CandleDatabase' object has no attribute 'conn'` | CandleDatabase manages connections internally via `get_connection()` — no persistent `conn` attribute |
| `PermissionError` on temp file cleanup (Windows) | File locked by process; use `try/except PermissionError: pass` for best-effort cleanup |
| `BLE001: Do not catch blind exception` | Catch specific exceptions: `(RuntimeError, ValueError, TypeError)` not bare `Exception` |
| `DTZ003: datetime.utcnow() deprecated` | Use `datetime.now(timezone.utc)` everywhere |
| `PLR0124: v != v for NaN check` | Use `math.isnan(v)` instead |
| `F841: Unused variable` | Remove or prefix with `_` |
| `RUF012: Mutable default` | Annotate class attributes with `ClassVar` |
| `RUF034: Useless if-else` | Simplify `x if cond else x` to just `x` |
| WebSocket `
` vs `\n` issues | Use `WebSocketDisconnect` for clean disconnect; `RuntimeError` for closed connections |
| `float('inf')` in JSON response | Convert to `None` or large finite number before serialization |
| Docker entrypoint deletes DB on integrity check | Quarantine the database and sidecar files instead of deleting them; fail fast and require an explicit, verified recovery step so batch sweeps and paper positions are not silently erased |
| Entrypoint removes SQLite sidecar files before checking lifecycle state | Never delete `-wal`, `-shm`, or `-journal` files until all writers have stopped and the database has been checkpointed or the sidecars are known to be inert; otherwise committed data can be lost |
| Ruff reports >0 non-zero exit codes (even with --fix) | Document BLE001 and DTZ003 cleanup pipeline; preserve intentional resilience patterns; re-run tests after each lint pass; report exact remaining count and scope instead of silently continuing |
| **`apply_hysteresis` returns `INSUFFICIENT_DATA` on first regime flip** | When `last_confirmed` is `None` and a new regime appears, the function overwrites `last_confirmed` with INSUFFICIENT_DATA before a confirmed state exists; fix: track `last_value` separately from `last_confirmed` so the first valid regime label is preserved until a confirmed flip occurs |
| **Synthetic test fixture cannot trigger `high_vol` regime** | In `test_classify_high_vol`, random-walk close prices make `ATR%` track its 60-day rolling median proportionally, so `ATR% >= 1.5 * median` can never be satisfied. Fix: augment fixture with a sustained volatility spike (e.g., widened spreads in the final bars) rather than uniformly scaled noise |
| **Asyncpg "too many clients already" during concurrent batch sweeps** | Each metric in a multi-metric batch sweep created its own event loop + connection pool. Fix: reuse a single event loop for the entire sweep in `_run_all_metrics_batch_task` (src/routes/batch.py:725) so pools are shared across metrics. |
| **Auto-validate validates ALL saved results instead of recent sweep** | Endpoint called with empty filters `{}`. Fix: pass sweep context `{symbols, timeframes, target_metric}` from frontend and filter `load_batch_results_pg` by those fields. |
| **Re-validation score not saved (COALESCE bug)** | `UPDATE ... SET col = COALESCE(old_col, $new)` preserves old NULLs instead of overwriting. Fix: `SET col = $new` to always write the new validation metrics. |
| **Re-validation score not saved (wrong gate key)** | Validation JSON uses `gate1_backtest` but code looked up `gate1`. Fix: use `val.get("gate_results", {}).get("gate1_backtest", {}).get("metrics", {})` in `_heal_batch_result_metrics`, `auto_validate_batch_results`, and re-run validation UI. |
| **Missing metrics on page refresh for validated profiles** | Same gate1 vs gate1_backtest bug in `_heal_batch_result_metrics()` (pg_database.py:829). Fix: correct the key lookup. |
| **Batch sweep progress shows 108% / "Complete" before finishing** | `total = len(configs)` (metrics count) but inner loop runs `symbols × timeframes` combos per metric. Fix: `total_all = len(configs) * symbols_per_metric * timeframes_per_metric` and `done_offset = i * combos_per_metric`. |
| **Validation score disappears after column sorting** | Re-validation updates DOM cell but not `table._cachedRows`. Sorting re-renders from stale cache. Fix: after PATCH save, update `table._cachedRows[rowIdx].validation_status` and `validation_json`. |
| **Frontend shows "Complete!" prematurely** | Only checked `!state.running` but stale state could have `running=false` from previous sweep. Fix: check `!state.running && (state.done >= state.total || state.error)`. |
| **Syntax error from edit conflict (extra closing brace)** | When modifying nested try/catch blocks, verify brace balance. Use a linter or manual count after edits. |
| **StrategyParams.to_dict() missing fields** | `trailing_stop_pct` and `max_position_pct` were omitted from serialization. Optuna optimizes them but paper profiles got `None`. Fix: add both fields to `to_dict()` in `src/config.py`. |
| **Regime not passed to RiskManager calls** | Backtester and paper engine defaulted to `"trending"` (1.0x) for `calculate_stop_and_target`, `calculate_position_size`, and `update_trailing_stop`. Fix: pass regime from `df_signals["regime"]` (computed by `strategy.generate_signals()`) to all three RiskManager methods. |
| **Paper engine used naive regime heuristic** | Engine used crude ATR%/VWAP check instead of full ADX/ER/ATR% classifier. Fix: read regime from `df_signals["regime"].iloc[-1]` after `strategy.generate_signals()`. |
| **Backtester hardcoded portfolio cap** | Used `equity * 3.0` instead of `params.max_position_pct`. Fix: default to 300% for backward compatibility but prefer `params.max_position_pct` when explicitly set. |
| **Position sizing hardcoded 300% leverage for cash accounts** | Backtester had `max_position_pct = 300.0` hardcoded (line 351 in `backtester.py`), overriding `StrategyParams.max_position_pct` default of 80%. This allowed 3x leverage even for `long_only` (cash) mode. For micro-cap assets (PEPE at $0.0000037), risk-based sizing created billions of units; with 300% cap, a $12,402 position was allowed on $10,000 equity. Fix: use `params.max_position_pct` with 100% cap for crypto spot/cash accounts: `max_position_pct = min(params.max_position_pct, 100.0)` when `asset_type == "crypto" and trade_direction == "long_only"`. |
| **Batch sweep checkpoint cross-contamination** | `BatchOptimizer._load_checkpoint()` only filtered by `target_metric`, causing cross-contamination between setups with different symbols/timeframes/exchanges but same metric. Setup 1 (BTC/ETH on 5m/15m) completed 10 combos; Setup 2 (SOL/AVAX on 1h/4h) with same `sharpe_ratio` metric incorrectly loaded Setup 1's checkpoint and skipped all combos. Fix: filter by all setup-defining params — `target_metric`, `exchange`, `symbols`, `timeframes`, and `strategy_mode` (the DB unique key is `(exchange, symbol, timeframe, strategy_mode, target_metric)`). |
| **Pruner not wired across entry points** | `OptunaConfig.pruner` and `BatchOptimizerConfig.pruner` fields existed but `batch_optimizer.py` did not forward `cfg.pruner` to `OptunaConfig`. Fix: add `pruner=cfg.pruner` to `OptunaConfig()` call in `batch_optimizer.py` (line ~395). Now Batch Sweep, Direct Optuna API, and WFO all inherit pruner. Recommended: `\"median\"` for 10-30 params, `\"hyperband\"` for >30 params. |
| **BacktestEngine `self.config` AttributeError** | `BacktestEngine` uses `self.params`, not `self.config`. Phase 4 framework code in `backtester.py` referenced `self.config.regime_aware_params` — caused `AttributeError: 'BacktestEngine' object has no attribute 'config'`. Fix: use `hasattr(self, "params") and getattr(getattr(self, "params", None), "regime_aware_params", False)` pattern. Always check `self.params` in `BacktestEngine`, never `self.config`. |
|| **Regime indexing for entry vs trailing** | For entry (initial stop/sizing), use signal-bar regime (`regimes[i-1]`). For trailing stop updates, use current-bar regime (`regimes[i]`). Match the bar used for signal_vwap/signal_atr. ||
|| **Batch sweep tab Setup 3 HTML value mismatch** | Batch sweep tab dropdown had `value=\"vwap_pullback\"` for Setup 3 but optimizer/strategy/backtest page all expect `\"pullback\"`. When clicking \"Apply\", `applyBatchRow()` tried to set dropdown to non-existent value → defaulted to first option (Setup 1). Fix: change `src/web/index.html` batch sweep tab option value from `vwap_pullback` to `pullback`. ||
|| **Backtest dashboard shows trades on chart but empty trade log** | Risk-based sizing with tight VWAP stop created positions exceeding portfolio cap (80% equity). Backtester rejected oversized positions entirely instead of scaling them down. Result: 0 trades logged despite signals on chart. Fix: in `src/backtester.py`, scale position to fit `max_position_value` cap (match paper engine behavior at `engine.py:725`) instead of rejecting. ||
|| **Async endpoint `run_async()` corruption (Monte Carlo, WFO, Tear Sheet)** | Calling sync wrappers (`load_candles_sync`, `resolve_canonical_symbol`) from the main event loop creates new `asyncio.run()` loops, corrupting asyncpg pool state (`"another operation is in progress"`). Fix: use async variants (`await DataLoader.load_candles`, `await DataLoader.aresolve_canonical_symbol`) in all async endpoints (`run_monte_carlo`, `run_walk_forward`, `generate_tear_sheet_pdf/html/json`). Keep sync wrappers (`load_candles_sync`, `resolve_canonical_symbol`) only for sync endpoints (`sync_history`, `_optimize_worker`). See `references/async-run_async-pattern.md`. ||
|| **`clear_cache` `TRUNCATE TABLE` parsing error** | `TRUNCATE TABLE candles` returns `"TRUNCATE TABLE"` (not a count), so `int(result.split()[-1])` raises `ValueError`. Fix: count before truncate (`SELECT COUNT(*)`) and return that. See reference file for verification checklist. ||
|| **`clear_cache` API missing method** | `CandleDatabase` class lacked `clear_cache()` method. Endpoint `/api/clear_cache` called `_st()._batch_db.clear_cache()` but method didn't exist. Fix: add `clear_cache()` method to `CandleDatabase` that counts rows then `TRUNCATE TABLE candles`. ||
|| **Frontend timeout message stale (20s → 60s)** | Frontend `AbortController` timeout was 60s but warning message said "20s". Updated `src/web/app.js` line 1266 message to match actual 60s timeout. Match backend `load_candles_sync` 600s timeout with 60s frontend AbortController for large datasets. ||
|| **Sync Database "Auto" loads 500 candles, not optimal lookback** | "Sync Database" with data range "Auto" sends `days: null, limit: 500` → fetches 500 candles (~1 day for 3m). Backtest "Auto" uses `get_optimal_lookback_days()` (e.g., 60 days for 3m = 28,800 candles). After clearing cache, sync must use explicit day range (e.g., "60_days") to match backtest range. UI should clarify this difference. ||

## Risk Management & Stop Loss Patterns

### Regime-Aware Stop Loss Management

The three-regime classifier (`src/regime.py`) drives stop distance multipliers via `_REGIME_STOP_MULTIPLIER`:
- `trending`: 1.0x (baseline)
- `ranging`: 1.15x (wider stops in chop)
- `high_vol`: 1.4x (much wider stops in high volatility)

**Integration points** (all must pass regime):
1. **Initial stop calculation** — `RiskManager.calculate_stop_and_target(entry_price, side, vwap, atr, regime)`
2. **Position sizing** — `RiskManager.calculate_position_size(equity, entry_price, stop_loss, regime)` (reduces size in high_vol)
3. **Trailing stop updates** — `RiskManager.update_trailing_stop(side, price, stop, atr, regime, entry_price)` (caps distance by regime multiplier and `trailing_stop_pct`)

**Data flow**: `strategy.generate_signals()` computes regime and stores in `df["regime"]` → backtester/paper engine extract `regimes = df["regime"].to_numpy()` → pass appropriate regime to each RiskManager call.

### Trailing Stop Cap (`trailing_stop_pct`)

Acts as a MAXIMUM distance cap on trailing stops:
```python
atr_distance = atr * atr_multiplier * regime_stop_mult
pct_distance = current_price * (trailing_stop_pct / 100.0)
distance = min(atr_distance, pct_distance)  # ATR base, capped by %
```

Default 1.5% prevents "run up 1-2% → give it all back" pattern. Must be serialized via `StrategyParams.to_dict()` to survive Optuna → batch results → paper profile pipeline.

### Portfolio Position Cap (`max_position_pct`)

Limits total position value as % of equity. Default 80% in StrategyParams, but backtester defaults to 300% for backward compatibility with original `equity * 3.0` behavior. Paper engine uses `getattr(params, "max_position_pct", 80.0) / 100.0`.

## Debugging Patterns

### Indentation-Safe Patching Workflow

When modifying Python files with `patch`:
1. Read exact current content with `read_file` (not `cat`/`sed`)
2. Apply minimal patch with surrounding context
3. Immediately run `python -m py_compile <file>` to catch IndentationError
4. Run `ruff check <file>` for style
5. Then run relevant tests

For multi-line block replacements, prefer `execute_code` to rewrite the exact block rather than chained `patch` calls.

### Debugging No-Trades in Backtester

If backtester produces 0 trades:
1. Add debug prints at each gate: signal → can_enter → direction → margin → position_size → max_position_value
2. Check `max_position_value` vs `val_usd` — position may exceed cap
3. Verify `signal` column has non-zero values (strategy may suppress all signals)
4. Verify `regime` array is not all `"trending"` (affects stop distance and position size)