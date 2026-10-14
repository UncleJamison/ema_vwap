# Regime Classifier Design Spec (ema_vwap-0tj)

## Indicators (no external TA libs — pandas/numpy only)

ADX (Wilder 14), Efficient Ratio (Kaufman 10), ATR% (14 vs 60-day median).

## Thresholds & Hysteresis
Trending: ADX≥25 + ER≥0.5. Ranging: ADX<25 + low ATR%. High_vol: ATR%≥1.5×median.
3-bar hysteresis mandatory; `INSUFFICIENT_DATA` for <60 bars; flat price → `"ranging"`.
Strategy mapping (`REGIME_MAP`): `trending→multi_ema`, `ranging→pullback`, `high_vol→crossover`.
Synthetic fixture tuning rule: adjust `RegimeConfig` thresholds before restructuring code; verify ADX/ER independently on linear-trend fixtures (pure close=100+0.01*i) before changing logic.
