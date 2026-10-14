# Regime Classifier Reference

## Three-Regime Detection Design

Industry-standard thresholds using only pandas/numpy (no TA-Lib external dependencies):

| Regime | Condition |
|--------|-----------|
| **trending** | ADX(14) ≥ 25 **and** ER(10) ≥ 0.5 **and** ATR% < 1.5 × 60-day median |
| **ranging** | ADX(14) < 25 **and** ATR% < 1.5 × 60-day median |
| **high_vol** | ATR% ≥ 1.5 × 60-day median (regardless of ADX/ER) |

### Hysteresis

- 3-bar hysteresis applied via `apply_hysteresis()` to prevent whipsaws at regime boundaries
- Regime only flips after 3 consecutive bars in the new regime
- `INSUFFICIENT_DATA` label used during warmup period (< 60 bars for all indicators)

### Strategy Mapping

```python
REGIME_MAP = {
    "trending": "multi_ema",
    "ranging": "pullback",
    "high_vol": "crossover",
}
```

## Implementation Notes

### ADX Calculation (Wilder 1978 Method)

Calculated without external TA libraries:
1. Compute True Range TR and Direction Change DC
2. Smooth: `ATR` and `DI+`/`DI-` using Wilder's smoothing (previous value × 13 + current value) / 14
3. ADX = smoothing of |DI+ - DI-| / (DI+ + DI-)

### ER (Efficient Ratio / Kaufman KAMA)

```python
change = abs(close - close.shift(10))
volatility = close.rolling(10).apply(lambda x: x.max() - x.min())
er = change / volatility
```

### ATR% (Normalized Volatility)

```python
atr = compute_atr(df, period=14)
atr_pct = (atr / close.replace(0, np.nan)) * 100.0
```

## Testing Pattern

### Synthetic Fixture Pitfalls

When creating synthetic data for regime tests, note:
- Random-walk close prices make `ATR%` track its rolling median proportionally
- Therefore `ATR% >= multiplier × median` cannot be satisfied with uniform scaling
- For `high_vol` tests, inject a **volatility spike**: widen the high-low spread in the final bars or use a mean-reverting price series with a sudden gap

Example:
```python
def make_high_vol_fixture():
    df = _make_candles(80, trend=0.0, noise=0.1, vol_mult=2.0)
    # Spike volatility in final 10 bars
    df.loc[df.index[-10:], 'high'] = df['close'] * 1.02
    df.loc[df.index[-10:], 'low'] = df['close'] * 0.98
    return df
```

### Hysteresis Test Pattern

```python
def test_hysteresis_no_flip():
    # 2 bars < hysteresis_bars means no flip
    regimes = pd.Series(["trending"] * 2 + ["ranging"] * 2 + ["trending"] * 2)
    result = apply_hysteresis(regimes, confirmed_bars=3)
    # First 2 bars of each regime are INSUFFICIENT_DATA or initial state
    ...
```