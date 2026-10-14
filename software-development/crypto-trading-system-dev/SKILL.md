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

## References

### Regime-Detection Integration (adaptive strategy switching)

- Three-regime classifier: `trending` (ADX ≥ 25 + ER ≥ 0.5), `ranging` (ADX < 25 + low ATR%), `high_vol` (elevated ATR%). Built from scratch in `src/regime.py` using only pandas/numpy (no TA-Lib).
- Config-driven mapping via `REGIME_MAP`: `trending→multi_ema`, `ranging→pullback`, `high_vol→crossover`; `RegimeConfig` dataclass holds thresholds (ADX=25, ER=0.5, ATR mult=1.5, hysteresis=3 bars).
- Hysteresis is mandatory: regime only flips after 3 consecutive bars in new regime (prevents whipsaw at ADX≈25). Implemented via `apply_hysteresis()` vectorized over the series.
- `get_current_regime()` wrapper for live paper engine; `classify_regime()` returns `INSUFFICIENT_DATA` for warmup (< 60 bars).
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
| WebSocket `\r` vs `\n` issues | Use `WebSocketDisconnect` for clean disconnect; `RuntimeError` for closed connections |
| `float('inf')` in JSON response | Convert to `None` or large finite number before serialization |
| Docker entrypoint deletes DB on integrity check | Quarantine the database and sidecar files instead of deleting them; fail fast and require an explicit, verified recovery step so batch sweeps and paper positions are not silently erased |
| Entrypoint removes SQLite sidecar files before checking lifecycle state | Never delete `-wal`, `-shm`, or `-journal` files until all writers have stopped and the database has been checkpointed or the sidecars are known to be inert; otherwise committed data can be lost |
| Ruff reports >0 non-zero exit codes (even with --fix) | Document BLE001 and DTZ003 cleanup pipeline; preserve intentional resilience patterns; re-run tests after each lint pass; report exact remaining count and scope instead of silently continuing |
| **`apply_hysteresis` returns `INSUFFICIENT_DATA` on first regime flip** | When `last_confirmed` is `None` and a new regime appears, the function overwrites `last_confirmed` with INSUFFICIENT_DATA before a confirmed state exists; fix: track `last_value` separately from `last_confirmed` so the first valid regime label is preserved until a confirmed flip occurs |
| **Synthetic test fixture cannot trigger `high_vol` regime** | In `test_classify_high_vol`, random-walk close prices make `ATR%` track its 60-day rolling median proportionally, so `ATR% >= 1.5 * median` can never be satisfied. Fix: augment fixture with a sustained volatility spike (e.g., widened spreads in the final bars) rather than uniformly scaled noise |