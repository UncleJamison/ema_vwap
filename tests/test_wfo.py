"""Unit tests for Walk-Forward Optimization Engine."""

import pytest

from src.config import OptunaConfig, StrategyParams, WFOConfig
from src.data_loader import DataLoader
from src.wfo import WalkForwardEngine, compute_wfo_auto_params


def test_walk_forward_engine_basic():
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    params = StrategyParams()
    wfo_config = WFOConfig(num_windows=3, trials_per_window=3)
    opt_config = OptunaConfig(n_trials=3)

    wfo_engine = WalkForwardEngine(
        base_params=params, wfo_config=wfo_config, opt_config=opt_config
    )
    res = wfo_engine.run(df)

    assert "walk_forward_efficiency_pct" in res
    assert "window_count" in res
    assert res["window_count"] >= 2
    assert "window_details" in res
    assert len(res["window_details"]) == res["window_count"]
    assert "param_stability_std" in res
    # Regime-aware fields should be present
    assert "window_regimes" in res
    assert "regime_change_count" in res
    assert "trigger_reoptimize" in res
    assert isinstance(res["window_regimes"], list)
    assert len(res["window_regimes"]) == res["window_count"]
    assert isinstance(res["trigger_reoptimize"], bool)


def test_walk_forward_window_ranges_rolling():
    """Verify that rolling windows produce contiguous, non-overlapping OOS ranges."""
    df = DataLoader.generate_synthetic_candles(num_bars=500, seed=42)
    params = StrategyParams()
    wfo_config = WFOConfig(num_windows=5, in_sample_ratio=0.7, window_type="rolling")
    wfo_engine = WalkForwardEngine(base_params=params, wfo_config=wfo_config)

    windows = wfo_engine.generate_windows(df)
    assert len(windows) == 5

    for i in range(len(windows) - 1):
        curr_oos = windows[i]["df_oos"]
        next_oos = windows[i + 1]["df_oos"]
        # Out-of-sample start of next window must immediately follow out-of-sample end of current window
        curr_last_ts = curr_oos["timestamp"].iloc[-1]
        next_first_ts = next_oos["timestamp"].iloc[0]
        # In synthetic 5m candles, consecutive candles differ by 5 minutes
        assert (next_first_ts - curr_last_ts).total_seconds() == 300

    # Ensure last OOS window covers all the way to the end of the dataframe
    assert windows[-1]["df_oos"]["timestamp"].iloc[-1] == df["timestamp"].iloc[-1]


def test_walk_forward_window_ranges_anchored():
    """Verify that anchored windows expand IS from bar 0 while keeping OOS contiguous."""
    df = DataLoader.generate_synthetic_candles(num_bars=500, seed=42)
    params = StrategyParams()
    wfo_config = WFOConfig(num_windows=4, in_sample_ratio=0.6, window_type="anchored")
    wfo_engine = WalkForwardEngine(base_params=params, wfo_config=wfo_config)

    windows = wfo_engine.generate_windows(df)
    assert len(windows) == 4

    for i, win in enumerate(windows):
        # All anchored windows start from index 0
        assert win["df_is"]["timestamp"].iloc[0] == df["timestamp"].iloc[0]
        if i > 0:
            assert len(win["df_is"]) > len(windows[i - 1]["df_is"])


def test_walk_forward_insufficient_data():
    df = DataLoader.generate_synthetic_candles(num_bars=50, seed=42)
    params = StrategyParams()
    wfo_engine = WalkForwardEngine(base_params=params)

    with pytest.raises(ValueError, match="minimum 100 bars required"):
        wfo_engine.run(df)


def test_wfo_auto_trials_do_not_exceed_budget():
    params = compute_wfo_auto_params(n_bars=5000, n_trials=12)

    assert params["trials_per_window"] >= 1
    assert params["trials_per_window"] * params["num_windows"] <= 12


# --- New tests for regime-aware WFO ---
def test_wfo_returns_window_regimes():
    """WFO run should populate window_regimes list with valid regime labels."""
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    params = StrategyParams()
    wfo_config = WFOConfig(num_windows=3, trials_per_window=3)
    opt_config = OptunaConfig(n_trials=3)

    wfo_engine = WalkForwardEngine(
        base_params=params, wfo_config=wfo_config, opt_config=opt_config
    )
    res = wfo_engine.run(df)

    valid_labels = {"trending", "ranging", "high_vol", "insufficient_data"}
    assert len(res["window_regimes"]) == res["window_count"]
    for regime in res["window_regimes"]:
        assert regime in valid_labels


def test_wfo_regime_change_fields_present():
    """WFO result should include regime change tracking fields."""
    df = DataLoader.generate_synthetic_candles(num_bars=400, seed=42)
    params = StrategyParams()
    wfo_config = WFOConfig(num_windows=4, trials_per_window=5)
    opt_config = OptunaConfig(n_trials=5)

    wfo_engine = WalkForwardEngine(
        base_params=params, wfo_config=wfo_config, opt_config=opt_config
    )
    res = wfo_engine.run(df)

    assert "regime_change_count" in res
    assert "regime_change_ratio" in res
    assert "trigger_reoptimize" in res
    assert isinstance(res["regime_change_count"], int)
    assert isinstance(res["regime_change_ratio"], float)
    assert isinstance(res["trigger_reoptimize"], bool)


def test_wfo_window_detail_includes_regime():
    """Each window detail dict should include a 'window_regime' field."""
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    params = StrategyParams()
    wfo_config = WFOConfig(num_windows=3, trials_per_window=3)
    opt_config = OptunaConfig(n_trials=3)

    wfo_engine = WalkForwardEngine(
        base_params=params, wfo_config=wfo_config, opt_config=opt_config
    )
    res = wfo_engine.run(df)

    for detail in res["window_details"]:
        assert "window_regime" in detail
        assert "regime_change_trigger" in detail


def test_wfo_regime_change_ratio_calculation():
    """Regime change ratio is the fraction of consecutive windows with different regimes.

    Test with a controlled scenario where we know the regime assignments.
    """
    from src.wfo import WalkForwardEngine

    df = DataLoader.generate_synthetic_candles(num_bars=400, seed=42)
    params = StrategyParams()
    wfo_config = WFOConfig(num_windows=4, trials_per_window=5)
    opt_config = OptunaConfig(n_trials=5)

    wfo_engine = WalkForwardEngine(
        base_params=params, wfo_config=wfo_config, opt_config=opt_config
    )
    res = wfo_engine.run(df)

    # Compute expected ratio manually
    regimes = res["window_regimes"]
    changes = 0
    for i in range(1, len(regimes)):
        prev = regimes[i - 1]
        cur = regimes[i]
        if prev != "insufficient_data" and cur != "insufficient_data" and prev != cur:
            changes += 1

    expected_ratio = round(changes / len(regimes), 2) if regimes else 0.0
    assert res["regime_change_count"] == changes
    assert res["regime_change_ratio"] == expected_ratio


def test_wfo_trigger_reoptimize_logic():
    """trigger_reoptimize should be True when regime change ratio > 0.5."""
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    params = StrategyParams()
    wfo_config = WFOConfig(num_windows=3, trials_per_window=3)
    opt_config = OptunaConfig(n_trials=3)

    wfo_engine = WalkForwardEngine(
        base_params=params, wfo_config=wfo_config, opt_config=opt_config
    )
    res = wfo_engine.run(df)

    n_windows = res["window_count"]
    regime_changes = res["regime_change_count"]
    # trigger_reoptimize is True iff regime_changes / n_windows > 0.5
    expected_trigger = (regime_changes / n_windows) > 0.5 if n_windows > 0 else False
    assert res["trigger_reoptimize"] == expected_trigger

    if expected_trigger:
        assert res["regime_change_ratio"] > 0.5
    else:
        assert res["regime_change_ratio"] <= 0.5


# --- Auto-scaled tests ---
def test_wfo_auto_scaling_with_regime():
    """Auto-scaled WFO should still populate regime-aware fields."""
    df = DataLoader.generate_synthetic_candles(num_bars=1000, seed=42)
    params = StrategyParams()
    wfo_config = WFOConfig(
        num_windows=0,  # auto
        in_sample_ratio=0.7,
        window_type="rolling",
        trials_per_window=0,  # auto
    )

    wfo_engine = WalkForwardEngine(
        base_params=params, wfo_config=wfo_config, n_trials_total=12
    )
    res = wfo_engine.run(df)

    assert res["auto_scaled_params"] is not None
    # The auto_scaled_params dict has num_windows and trials_per_window keys
    assert "num_windows" in res["auto_scaled_params"]
    assert "trials_per_window" in res["auto_scaled_params"]
    # Regime fields present regardless of scaling mode
    assert "window_regimes" in res
    assert len(res["window_regimes"]) == res["window_count"]
