# Monte Carlo Risk Thresholds Configuration

Always-on rules for configuring Monte Carlo risk thresholds via the Settings panel.

## Preferred Procedure

### Step 1: Check existing config
Before making changes, verify the current state:
```bash
hermes config get memory.provider
```
If provider is `hindsight`, MC risk thresholds may already be configurable.

### Step 2: Add thresholds to Settings panel (if not present)
Add the following settings to `src/web/index.html` under the "Filters & Risk Management" section:
- `setting-val-max-mc-drawdown` - Max MC Drawdown p95 (%)
- `setting-val-max-ruin` - Max MC Ruin p5 (%)

### Step 3: Wire in app.js
Update `src/web/app.js`:
1. Add `confidence_levels: [5.0, 50.0, 95.0]` to `DEFAULT_SETTINGS`
2. Add `setVal("setting-val-max-ruin", s.confidence_levels[0])` in `initSettingsUI()`
3. Add `setVal("setting-val-median-mc-drawdown", s.confidence_levels[1])` in `initSettingsUI()`
4. Add `confidence_levels` retrieval in `saveSettings()`

### Step 4: Update StrategyValidator
In `src/validator.py`:
1. Add `SettingsManager` import
2. Modify `StrategyValidator.__init__()` to accept optional `settings_mgr` parameter
3. When no explicit `ValidationCriteria` is provided, read MC risk thresholds from `SettingsManager`
4. Explicit `ValidationCriteria` takes precedence over settings

### Step 5: Verify
Run pytest to ensure no regressions:
```bash
python -m pytest tests/ --tb=short
```
Expected: 160 passed, 1 expected Windows file-locking issue (unrelated)

### Pitfalls & Why
- **Windows file locking**: The temp DB file used in `test_validator_mc_risk_custom_criteria_overrides_settings` may be locked by the CandleDatabase/SettingsManager. The test uses best-effort cleanup with `try: os.unlink(); except PermissionError: pass`. This is a Windows filesystem behavior, not a code bug.
- **Settings persistence**: MC risk thresholds are stored in `src/settings.py` via `SettingsManager.get_monte_carlo_ruin_pct()` and `SettingsManager.get_monte_carlo_drawdown_pct()`. These persist across sessions via the SQLite database at `$HERMES_HOME/settings.db`.
- **Override precedence**: Explicit `ValidationCriteria(max_monte_carlo_ruin_pct=X, max_monte_carlo_drawdown_pct=Y)` always takes precedence over SettingsManager values. This is intentional — if the user provides explicit criteria, those should win.
- **Default compatibility**: Defaults (5.0 and 35.0) match the original hardcoded values in `ValidationCriteria`, ensuring zero migration friction.

### Supported values
- `max_monte_carlo_ruin_pct`: Float, default 5.0, min 0, max 50
- `max_monte_carlo_drawdown_pct`: Float, default 35.0, min 5, max 100
- `confidence_levels`: List of three floats [p5, p50, p95], default [5.0, 50.0, 95.0]

### References
- `src/settings.py` - `get_monte_carlo_ruin_pct()` and `get_monte_carlo_drawdown_pct()` methods
- `src/validator.py` - `StrategyValidator` with `settings_mgr` parameter
- `src/web/index.html` - MC risk setting controls
- `src/web/app.js` - DEFAULT_SETTINGS, initSettingsUI, saveSettings wiring
- `tests/test_validator.py` - 3 new MC risk tests

