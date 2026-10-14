# Monte Carlo Configuration Management

Always-on rules for managing Monte Carlo configuration across the settings/validator/app pipeline.

## Preferred Procedure

### Step 1: Check current MonteCarloConfig state
```python
from src.config import MonteCarloConfig
cfg = MonteCarloConfig()
print(cfg.confidence_levels)  # [5.0, 50.0, 95.0]
print(cfg.num_simulations)    # 1000
```

### Step 2: Update MonteCarloConfig if needed
If custom confidence levels are required, update `src/config.py`:
```python
confidence_levels: list[float] = field(default_factory=lambda: [5.0, 50.0, 95.0])
```
This is the default - custom values would be user-configured via the Settings panel, not by modifying this default.

### Step 3: Update SettingsManager if needed
In `src/settings.py`, ensure `get_monte_carlo_ruin_pct()` and `get_monte_carlo_drawdown_pct()` return the correct defaults:
```python
def get_monte_carlo_ruin_pct(self) -> float:
    return float(self.get("max_monte_carlo_ruin_pct") or 5.0)

def get_monte_carlo_drawdown_pct(self) -> float:
    return float(self.get("max_monte_carlo_drawdown_pct") or 35.0)
```

### Step 3: Update StrategyValidator
In `src/validator.py`, ensure `StrategyValidator.__init__()` reads from `SettingsManager` when no explicit `ValidationCriteria` is provided:
```python
def __init__(self, criteria=None, settings_mgr=None):
    if criteria is not None:
        self.criteria = criteria
    else:
        self.criteria = ValidationCriteria()
        if settings_mgr is not None:
            self.criteria.max_monte_carlo_ruin_pct = settings_mgr.get_monte_carlo_ruin_pct()
            self.criteria.max_monte_carlo_drawdown_pct = settings_mgr.get_monte_carlo_drawdown_pct()
```

### Step 3: Update app.js DEFAULT_SETTINGS
In `src/web/app.js`, ensure `confidence_levels` is in `DEFAULT_SETTINGS`:
```javascript
confidence_levels: [5.0, 50.0, 95.0],
```

### Step 4: Verify across the pipeline
1. Run `python -m pytest tests/ --tb=short` - should pass (160 passed)
2. Manually test the Settings panel - three new controls should appear and persist
3. Test that explicit `ValidationCriteria` overrides SettingsManager values

### Pitfalls & Why
- **MonteCarloConfig vs SettingsManager**: `MonteCarloConfig` has `confidence_levels` as a class default - this is the code-level default. `SettingsManager` persists user-configured values to SQLite. They serve different purposes: code default vs. user preference.
- **Persistence**: SettingsManager values persist via `$HERMES_HOME/settings.db`. They survive session restarts.
- **Override precedence**: Explicit `ValidationCriteria` always wins. This is by design - if the user provides explicit criteria in an API request, those should take precedence over stored preferences.

### Supported values
- `max_monte_carlo_ruin_pct`: Float, default 5.0
- `max_monte_carlo_drawdown_pct`: Float, default 35.0
- `confidence_levels`: List of three floats [p5, p50, p95], default [5.0, 50.0, 95.0]

### References
- `src/config.py` - `MonteCarloConfig` class with `confidence_levels`
- `src/settings.py` - `SettingsManager` with `get_monte_carlo_ruin_pct()` and `get_monte_carlo_drawdown_pct()`
- `src/validator.py` - `StrategyValidator` with `settings_mgr` parameter
- `src/web/app.js` - `DEFAULT_SETTINGS` with `confidence_levels`
- `src/web/index.html` - MC confidence level UI controls
- `tests/test_validator.py` - 3 new MC risk tests

