"""Unit tests for Walk-Forward Optimization Engine."""

import pandas as pd
import pytest

from src.config import OptunaConfig, StrategyParams, WFOConfig
from src.data_loader import DataLoader
from src.wfo import (
    WalkForwardEngine,
    compute_wfo_auto_params,
    derive_bars_per_day,
    get_default_target_oos_days,
)


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
    df["timestamp"] = pd.to_datetime(df["timestamp"])
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
    # With minimum 25 trials per window and 3 windows, minimum total is 75
    params = compute_wfo_auto_params(n_bars=5000, n_trials=100)

    assert params["trials_per_window"] >= 25
    assert params["trials_per_window"] * params["num_windows"] <= 100


def test_wfo_auto_trials_with_increased_budget():
    # With 250 trial budget, trials per window should be >= 50
    params = compute_wfo_auto_params(n_bars=5000, n_trials=250)
    assert params["trials_per_window"] >= 50
    assert params["trials_per_window"] * params["num_windows"] <= 250

    # With 500 trial budget (thorough preset)
    params_thorough = compute_wfo_auto_params(n_bars=5000, n_trials=500)
    assert params_thorough["trials_per_window"] >= 100
    assert params_thorough["trials_per_window"] * params_thorough["num_windows"] <= 500


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
def test_wfo_aggregate_and_average_oos_returns_are_distinct(monkeypatch):
    """WFO must report both average and aggregate OOS return, not a single conflated metric."""
    df = DataLoader.generate_synthetic_candles(num_bars=500, seed=42)
    params = StrategyParams()
    wfo_config = WFOConfig(num_windows=2, trials_per_window=1)

    oos_values = iter([12.5, -8.0])

    def fake_optimize(self, df_is):
        return {
            "best_params": {
                "fast_ema": 10,
                "slow_ema": 20,
                "trend_ema": 30,
                "volume_multiplier": 1.0,
                "atr_multiplier": 2.0,
                "risk_reward_ratio": 1.5,
            },
            "best_backtest_result": {
                "total_return_pct": 5.0,
                "win_rate": 0.5,
                "sharpe_ratio": 1.0,
            },
            "best_value": 1.0,
        }

    class DummyOOSResult:
        def __init__(self, total_return_pct):
            self.total_return_pct = total_return_pct
            self.win_rate = 0.5
            self.profit_factor = 1.2
            self.max_drawdown_pct = 10.0
            self.sharpe_ratio = 0.8
            self.total_trades = 1
            self.trades = []

    def fake_backtest_run(self, df_window, warmup_bars=None):
        return DummyOOSResult(next(oos_values))

    monkeypatch.setattr("src.wfo.OptunaOptimizer.optimize", fake_optimize)
    monkeypatch.setattr("src.wfo.BacktestEngine.run", fake_backtest_run)

    wfo_engine = WalkForwardEngine(base_params=params, wfo_config=wfo_config)
    res = wfo_engine.run(df)

    assert "avg_out_of_sample_return_pct" in res
    assert "oos_total_return_pct" in res
    assert "window_details" in res

    expected_avg = 2.25
    expected_total = (1.125 * 0.92 - 1.0) * 100.0
    assert abs(res["avg_out_of_sample_return_pct"] - expected_avg) < 1e-6
    assert abs(res["oos_total_return_pct"] - expected_total) < 1e-6
    assert abs(res["oos_total_return_pct"] - res["avg_out_of_sample_return_pct"]) > 1e-6


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


def test_wfo_time_normalized_efficiency(monkeypatch):
    """Verify WFE is time-normalized by the ratio of IS bars to OOS bars."""
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    params = StrategyParams()
    wfo_config = WFOConfig(num_windows=2, trials_per_window=1, in_sample_ratio=0.7)

    class DummyOOSResult:
        def __init__(self, ret):
            self.total_return_pct = ret
            self.win_rate = 50.0
            self.profit_factor = 1.5
            self.max_drawdown_pct = 5.0
            self.sharpe_ratio = 1.2
            self.total_trades = 5
            self.trades = []

    def fake_optimize(self, df_is):
        return {
            "best_params": params.to_dict(),
            "best_backtest_result": {
                "total_return_pct": 7.0,
                "win_rate": 50.0,
                "sharpe_ratio": 1.2,
            },
            "best_value": 1.2,
        }

    def fake_backtest(self, df_window, warmup_bars=None):
        return DummyOOSResult(3.0)

    monkeypatch.setattr("src.wfo.OptunaOptimizer.optimize", fake_optimize)
    monkeypatch.setattr("src.wfo.BacktestEngine.run", fake_backtest)

    wfo_engine = WalkForwardEngine(base_params=params, wfo_config=wfo_config)
    res = wfo_engine.run(df)

    # In 2 windows with 0.7 IS ratio:
    # avg_is_bars / avg_oos_bars is time_scale > 1.5
    # Raw ratio was 3.0 / 7.0 = 42.86%.
    # Time-normalized WFE should be (3.0 / 7.0) * time_scale * 100 > 90%.
    assert res["time_scale"] > 1.5
    assert res["walk_forward_efficiency_pct"] > 90.0
    assert "oos_cumulative_return_pct" in res


def test_derive_bars_per_day():
    # String lookups
    assert derive_bars_per_day(timeframe="1m") == 1440.0
    assert derive_bars_per_day(timeframe="3m") == 480.0
    assert derive_bars_per_day(timeframe="5m") == 288.0
    assert derive_bars_per_day(timeframe="15m") == 96.0
    assert derive_bars_per_day(timeframe="1h") == 24.0
    assert derive_bars_per_day(timeframe="4h") == 6.0
    assert derive_bars_per_day(timeframe="6h") == 4.0
    assert derive_bars_per_day(timeframe="12h") == 2.0
    assert derive_bars_per_day(timeframe="1d") == 1.0

    # Default fallback
    assert derive_bars_per_day() == 288.0

    # DataFrame timestamp diff derivation
    import pandas as pd

    # 1h spaced timestamps
    ts_1h = pd.date_range("2025-01-01", periods=10, freq="1h")
    df_1h = pd.DataFrame({"timestamp": ts_1h, "close": [100.0] * 10})
    assert derive_bars_per_day(df=df_1h) == 24.0

    # 15m spaced timestamps
    ts_15m = pd.date_range("2025-01-01", periods=10, freq="15min")
    df_15m = pd.DataFrame({"timestamp": ts_15m, "close": [100.0] * 10})
    assert derive_bars_per_day(df=df_15m) == 96.0


def test_get_default_target_oos_days():
    assert get_default_target_oos_days(timeframe="5m") == 15.0
    assert get_default_target_oos_days(timeframe="15m") == 25.0
    assert get_default_target_oos_days(timeframe="1h") == 45.0
    assert get_default_target_oos_days(timeframe="4h") == 60.0
    assert get_default_target_oos_days(timeframe="1d") == 120.0

    # Test via bars_per_day
    assert get_default_target_oos_days(bars_per_day=288.0) == 15.0
    assert get_default_target_oos_days(bars_per_day=96.0) == 25.0
    assert get_default_target_oos_days(bars_per_day=24.0) == 45.0
    assert get_default_target_oos_days(bars_per_day=6.0) == 60.0


def test_compute_wfo_auto_params_timeframe_scaling():
    # 5m dataset with 20,000 bars
    res_5m = compute_wfo_auto_params(n_bars=20000, n_trials=300, timeframe="5m")
    assert res_5m["num_windows"] >= 3
    assert res_5m["trials_per_window"] >= 25

    # 15m dataset with 10,000 bars
    res_15m = compute_wfo_auto_params(n_bars=10000, n_trials=300, timeframe="15m")
    assert res_15m["num_windows"] >= 3
    assert res_15m["trials_per_window"] >= 25

    # 1h dataset with 10,000 bars
    res_1h = compute_wfo_auto_params(n_bars=10000, n_trials=300, timeframe="1h")
    assert res_1h["num_windows"] == 6
    assert res_1h["trials_per_window"] == 50

    # 4h dataset with 3,000 bars
    res_4h = compute_wfo_auto_params(n_bars=3000, n_trials=300, timeframe="4h")
    assert res_4h["num_windows"] == 6
    assert res_4h["trials_per_window"] == 50


def test_walk_forward_engine_timeframe_awareness():
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    params = StrategyParams()
    wfo_config = WFOConfig(num_windows=3, trials_per_window=3)
    opt_config = OptunaConfig(n_trials=3)

    # With explicit timeframe="1h"
    engine = WalkForwardEngine(
        base_params=params,
        wfo_config=wfo_config,
        opt_config=opt_config,
        timeframe="1h",
    )
    assert engine.bars_per_day == 24.0
    res = engine.run(df)
    assert res["bars_per_day"] == 24.0
    assert res["timeframe"] == "1h"


def test_wfo_intelligent_auto_balancing_large_dataset():
    """Verify that large datasets on small budgets auto-scale window counts

    rather than starving windows to 2-3 trials (ema_vwap-5u1t).
    """
    # 100k bars (e.g. 1 year of 5m candles) with 50 total budget
    res_50 = compute_wfo_auto_params(n_bars=100000, n_trials=50, timeframe="5m")
    assert res_50["num_windows"] == 3
    assert res_50["trials_per_window"] >= 25

    # 100k bars with 100 total budget
    res_100 = compute_wfo_auto_params(n_bars=100000, n_trials=100, timeframe="5m")
    assert res_100["num_windows"] >= 3
    assert res_100["trials_per_window"] >= 25
    assert res_100["trials_per_window"] * res_100["num_windows"] <= 100

    # 100k bars with 250 total budget
    res_250 = compute_wfo_auto_params(n_bars=100000, n_trials=250, timeframe="5m")
    assert res_250["num_windows"] == 5
    assert res_250["trials_per_window"] == 50
    assert res_250["trials_per_window"] * res_250["num_windows"] <= 250

    # 100k bars with 500 total budget
    res_500 = compute_wfo_auto_params(n_bars=100000, n_trials=500, timeframe="5m")
    assert res_500["num_windows"] == 10
    assert res_500["trials_per_window"] == 50
    assert res_500["trials_per_window"] * res_500["num_windows"] <= 500


def test_wfo_explicit_num_windows_preserves_trial_floor():
    """Verify explicit_num_windows preserves minimum trial floor."""
    res = compute_wfo_auto_params(
        n_bars=50000, n_trials=100, explicit_num_windows=5, timeframe="15m"
    )
    assert res["num_windows"] == 5
    assert res["trials_per_window"] >= 25


@pytest.mark.asyncio
async def test_run_walk_forward_defaults_to_optimal_lookback():
    """Verify run_walk_forward loads candles with optimal lookback when days is None or 0."""
    from unittest.mock import AsyncMock, patch

    from src.config import get_optimal_lookback_days
    from src.routes.backtest_opt import WFORequest, run_walk_forward

    req = WFORequest(
        symbol="BTC/USD",
        timeframe="5m",
        days=None,
        limit=500,
        num_windows=2,
        trials_per_window=2,
    )
    with patch(
        "src.routes.backtest_opt.DataLoader.load_candles", new_callable=AsyncMock
    ) as mock_load, patch(
        "src.routes.backtest_opt.DataLoader.aresolve_canonical_symbol",
        new_callable=AsyncMock,
    ) as mock_resolve:
        mock_resolve.return_value = ("kucoin", "BTC/USDT")
        mock_load.return_value = DataLoader.generate_synthetic_candles(
            num_bars=300, seed=42
        )

        res = await run_walk_forward(req)
        assert res["status"] == "success"
        mock_load.assert_called_once()
        call_kwargs = mock_load.call_args[1]
        assert call_kwargs["days"] == get_optimal_lookback_days(
            "5m"
        )  # 90 days, NOT 365!
        assert call_kwargs["limit"] is None


@pytest.mark.asyncio
async def test_run_walk_forward_respects_explicit_days():
    """Verify run_walk_forward respects explicit days parameter."""
    from unittest.mock import AsyncMock, patch

    from src.routes.backtest_opt import WFORequest, run_walk_forward

    req = WFORequest(
        symbol="BTC/USD",
        timeframe="5m",
        days=180,
        limit=500,
        num_windows=2,
        trials_per_window=2,
    )
    with patch(
        "src.routes.backtest_opt.DataLoader.load_candles", new_callable=AsyncMock
    ) as mock_load, patch(
        "src.routes.backtest_opt.DataLoader.aresolve_canonical_symbol",
        new_callable=AsyncMock,
    ) as mock_resolve:
        mock_resolve.return_value = ("kucoin", "BTC/USDT")
        mock_load.return_value = DataLoader.generate_synthetic_candles(
            num_bars=300, seed=42
        )

        res = await run_walk_forward(req)
        assert res["status"] == "success"
        mock_load.assert_called_once()
        call_kwargs = mock_load.call_args[1]
        assert call_kwargs["days"] == 180
