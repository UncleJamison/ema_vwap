# Regime Detection — Design Spec (ema_vwap-nfh)

Status: DESIGN COMPLETE. Implementation verified (src/regime.py + tests/test_regime.py: 16/16 pass).

## Regimes (3)
- trending  → ADX ≥ 25, ER ≥ 0.5 → strategy_mode="multi_ema"
- ranging   → ADX < 25, ATR% < 1.5× median → strategy_mode="pullback"
- high_vol  → ADX < 25 or ER < 0.5, ATR% ≥ 1.5× median → strategy_mode="crossover"

## Indicators (vectorized, pandas/numpy only)
- ADX(14): Wilder smoothed directional movement
- ER(10): Kaufman Efficient Ratio = |Close_t - Close_{t-10}| / Σ|ΔClose|
- ATR%(14): ATR(14)/close × 100; rolling median over 60 bars

## Hysteresis
- 3 consecutive bars required before regime flip (apply_hysteresis)
- INSUFFICIENT_DATA preserved through warmup (< valid_from_idx bars)

## Integration Points (verified via grep, per audit protocol)
1. src/strategy.py — strategy_mode="auto" branches via get_current_regime(); regime column added
2. src/app.py — /api/regime/current calls DataLoader + get_current_regime()
3. src/risk_manager.py — regime parameter applied to position size / trailing stop
4. src/optimizer.py — OptunaConfig.regime_sweep_enabled gates per-regime sweep; RegimeConfig passed to objective

## Config (RegimeConfig)
- adx_threshold=25.0, er_threshold=0.5, atr_multiplier=1.5, hysteresis_bars=3
- Swept by Optuna; no dedicated regime-modeler sweep needed (per user's preference)

No structural blockers; module standalone (no dependency on 3kt or execution algorithms).
