# Monte Carlo UI Controls Wiring

Always-on rules for wiring Monte Carlo confidence level controls into the Settings panel and app.js.

## Preferred Procedure

### Step 1: Add HTML controls in index.html
Add three new form-group entries in the "Filters & Risk Management" section of `src/web/index.html`:
- `setting-val-max-ruin` - Max MC Ruin p5 (%)
- `setting-val-median-mc-drawdown` - Max MC Drawdown p50 (%)
- These go after the existing `setting-val-max-mc-drawdown` control

### Step 2: Wire in app.js DEFAULT_SETTINGS
Add to the `DEFAULT_SETTINGS` object in `src/web/app.js`:
```javascript
confidence_levels: [5.0, 50.0, 95.0],
```

### Step 3: Wire in initSettingsUI()
Add three `setVal()` calls after the existing `setting-val-max-mc-drawdown` call:
```javascript
setVal("setting-val-max-ruin", s.confidence_levels[0]);
setVal("setting-val-median-mc-drawdown", s.confidence_levels[1]);
```

### Step 4: Wire in saveSettings()
Add `confidence_levels` to the saved settings object:
```javascript
confidence_levels: [getNum("setting-val-max-ruin", DEFAULT_SETTINGS.confidence_levels[0]), getNum("setting-val-median-mc-drawdown", DEFAULT_SETTINGS.confidence_levels[1])],
```

### Step 5: Verify
Run pytest to confirm no regressions:
```bash
python -m pytest tests/ --tb=short
```
Expected: 160 passed, 1 expected Windows file-locking issue

### Pitfalls & Why
- **Windows file locking**: Same as mc-risk skill - temp DB files may be locked. Best-effort cleanup with `try: os.unlink(); except PermissionError: pass`.
- **HTML structure**: The new form-groups must be inserted inside the "Filters & Risk Management" `<div class="sidebar-section">` block, after the existing `setting-val-max-mc-drawdown` group and before the next `<div class="form-row">`.
- **JSON parsing**: `confidence_levels` is stored as a JSON array in the web UI's `localStorage`. Ensure `JSON.parse()` and `JSON.stringify()` are used correctly when reading/writing.

### Supported values
- `max_monte_carlo_ruin_pct`: Float, default 5.0, min 0, max 50 (from `setting-val-max-ruin`)
- `max_monte_carlo_drawdown_pct`: Float, default 35.0, min 5, max 100 (from `setting-val-max-mc-drawdown`)
- `confidence_levels`: List of three floats [p5, p50, p95], default [5.0, 50.0, 95.0], from three new UI controls

### References
- `src/web/index.html` - MC confidence level HTML controls
- `src/web/app.js` - DEFAULT_SETTINGS, initSettingsUI, saveSettings wiring
- `src/settings.py` - `get_monte_carlo_ruin_pct()` and `get_monte_carlo_drawdown_pct()` methods
- `src/validator.py` - `StrategyValidator` with `settings_mgr` parameter
- `tests/test_validator.py` - 3 new MC risk tests

