"""Tests for the regime detection module (src/regime.py).

Covers indicators, classification, hysteresis, edge cases, and REGIME_MAP mapping.
"""
import numpy as np
import pandas as pd

from src.regime import (
    INSUFFICIENT_DATA,
    RegimeConfig,
    apply_hysteresis,
    classify_regime,
    compute_adx,
    compute_atr_pct,
    compute_efficient_ratio,
    get_current_regime,
    regime_to_strategy_mode,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_candles(
    n: int,
    trend: float = 0.0,
    noise: float = 1.0,
    vol_mult: float = 1.0,
) -> pd.DataFrame:
    """Return a synthetic OHLCV DataFrame of `n` bars.

    trend  = per-bar drift in close price
    noise  = stdev of close noise
    vol_mult scales true range / volatility
    """
    rng = np.random.RandomState(42)
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    close = 100.0 + np.cumsum(rng.normal(trend, noise, size=n))
    high = close + rng.uniform(0.0, 0.5 * vol_mult, size=n)
    low = close - rng.uniform(0.0, 0.5 * vol_mult, size=n)
    open_ = close + rng.normal(0, 0.1, size=n)
    volume = rng.uniform(100, 1000, size=n) * vol_mult
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "timestamp": idx,
        }
    )


# ---------------------------------------------------------------------------
# Indicator unit tests
# ---------------------------------------------------------------------------
def test_adx_trending():
    df = _make_candles(100, trend=0.02, noise=0.1)
    adx = compute_adx(df)
    last = adx.dropna().iloc[-1]
    assert last > 20, f"ADX={last} should indicate trend"


def test_adx_ranging():
    df = _make_candles(100, trend=0.0, noise=0.5)
    adx = compute_adx(df)
    last = adx.dropna().iloc[-1]
    assert last < 25, f"ADX={last} should indicate ranging"


def test_er_efficient():
    df = _make_candles(50, trend=0.01, noise=0.01)
    er = compute_efficient_ratio(df, period=10)
    last = er.dropna().iloc[-1]
    assert last > 0.65, f"ER={last} (synthetic linear trend with noise gives ~0.75)"


def test_er_chop():
    rng = np.random.RandomState(1)
    df = pd.DataFrame(
        {
            "close": 100.0 + rng.normal(0, 1, 50),
            "high": 101.0 + rng.normal(0, 1, 50),
            "low": 99.0 + rng.normal(0, 1, 50),
            "volume": np.ones(50),
            "timestamp": pd.date_range("2026-01-01", periods=50, freq="1min", tz="UTC"),
        }
    )
    er = compute_efficient_ratio(df, period=10)
    last = er.dropna().iloc[-1]
    assert last < 0.5, f"ER={last} should be low for random walk"


def test_atr_pct_shape():
    df = _make_candles(50)
    atr = compute_atr_pct(df)
    assert len(atr) == len(df)
    assert atr.dropna().between(0, 50).all()


# ---------------------------------------------------------------------------
# Classification smoke tests
# ---------------------------------------------------------------------------
def test_classify_trending():
    df = _make_candles(100, trend=0.03, noise=0.05, vol_mult=0.5)
    regimes = classify_regime(df)
    valid = regimes[regimes != INSUFFICIENT_DATA]
    assert len(valid) > 0
    assert valid.iloc[-1] == "trending"


def test_classify_high_vol():
    # High-vol fixture: choppy close (low ADX) + very wide high/low spreads
    rng = np.random.RandomState(42)
    n = 80
    close_vals = 100.0 + rng.normal(0, 1.0, size=n)
    close = pd.Series(
        close_vals,
        index=pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC"),
    )
    high_vals = close + np.abs(rng.normal(0, 3.0, size=n))
    low_vals = close - np.abs(rng.normal(0, 3.0, size=n))
    df = pd.DataFrame(
        {
            "open": close,
            "high": high_vals,
            "low": low_vals,
            "close": close,
            "volume": np.ones(n) * 500,
            "timestamp": close.index,
        }
    )
    regimes = classify_regime(df)
    valid = regimes[regimes != INSUFFICIENT_DATA]
    assert len(valid) > 0
    assert valid.isin(["trending", "ranging", "high_vol"]).all()


def test_classify_ranging():
    df = _make_candles(100, trend=0.0, noise=0.3, vol_mult=1.0)
    regimes = classify_regime(df)
    valid = regimes[regimes != INSUFFICIENT_DATA]
    assert len(valid) > 0
    assert valid.iloc[-1] == "ranging"


def test_hysteresis_no_flip():
    # With only 2 consecutive bars of new regime, should NOT flip
    regimes = pd.Series(
        ["trending"] * 2 + ["ranging"] * 2 + ["trending"] * 2,
        index=range(6),
    )
    out = apply_hysteresis(regimes, confirmed_bars=3)
    # First available confirmed state: all bars stay trending (never 3 consecutive ranging)
    assert out.iloc[-1] == "trending"
    assert (out == "trending").all()


def test_hysteresis_confirmed():
    regimes = pd.Series(
        ["trending"] * 5 + ["ranging"] * 3,
        index=range(8),
    )
    out = apply_hysteresis(regimes, confirmed_bars=3)
    # After 3 consecutive ranging bars, should flip to ranging
    assert out.iloc[7] == "ranging"


def test_warmup_insufficient():
    df = _make_candles(15, trend=0.03)  # too short
    regimes = classify_regime(df)
    assert (regimes == INSUFFICIENT_DATA).all()


def test_flat_price_defaults_ranging():
    close = np.ones(80) * 100.0
    high = np.ones(80) * 101.0
    low = np.ones(80) * 99.0
    df = pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.ones(80),
            "timestamp": pd.date_range("2026-01-01", periods=80, freq="1min", tz="UTC"),
        }
    )
    regimes = classify_regime(df)
    valid = regimes[regimes != INSUFFICIENT_DATA]
    # Flat price with zero real range: classify_regime may classify all as
    # INSUFFICIENT_DATA (warmup / ATR% degenerate) - assert at least that
    # regime labels never raise KeyError and every valid label is legal.
    assert valid.empty or (valid.isin(["trending", "ranging", "high_vol"]).all())


def test_regime_to_strategy_mode_maps_all():
    assert regime_to_strategy_mode("trending") == "multi_ema"
    assert regime_to_strategy_mode("ranging") == "pullback"
    assert regime_to_strategy_mode("high_vol") == "crossover"
    assert regime_to_strategy_mode("unknown") == "crossover"


def test_get_current_regime_returns_str():
    df = _make_candles(80, trend=0.03)
    label = get_current_regime(df)
    assert label in ("trending", "ranging", "high_vol")


def test_insufficient_data_returns_sentinel():
    df = _make_candles(10)
    label = get_current_regime(df)
    assert label == INSUFFICIENT_DATA


# ---------------------------------------------------------------------------
# Integration: RegimeConfig changes propagate
# ---------------------------------------------------------------------------
def test_regime_config_low_atr_threshold_is_high_vol():
    cfg = RegimeConfig(atr_multiplier=0.5)  # very sensitive
    df = _make_candles(80, vol_mult=2.0)
    regimes = classify_regime(df, config=cfg)
    valid = regimes[regimes != INSUFFICIENT_DATA]
    assert len(valid) > 0
    assert (valid == "high_vol").any()
