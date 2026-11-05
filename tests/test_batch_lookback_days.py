"""
Test to verify the actual lookback days (candle count) used during batch optimization.
This test mocks DataLoader.load_candles to capture the `days` parameter passed
and verifies it matches the expected lookback for each timeframe.

For crypto timeframes, the expected defaults are:
- 1m: 30 days (native)
- 3m: 60 days (donor: 1m)
- 5m: 90 days (native)
- 15m: 180 days (native)
- 30m: 250 days (donor: 15m)
- 1h: 900 days (native)
- 2h: 540 days (donor: 1h)
- 4h: 350 days (donor: 1h)
- 6h: 540 days (donor: 1h)
- 8h: 540 days (native)
- 12h: 350 days (native)
- 1d: 1800 days (native)

Key behavior: For KuCoin, specific timeframes use explicit donors:
- 3m -> 1m donor
- 30m -> 15m donor  
- 2h -> 1h donor
- 4h -> 1h donor
- 6h -> 1h donor

Native (no donor): 1m, 5m, 15m, 1h, 8h, 12h, 1d
"""

import asyncio
from unittest.mock import Mock, patch

import pytest

from src.batch_optimizer import BatchOptimizer, BatchOptimizerConfig
from src.config import get_optimal_lookback_days
from src.data_loader import get_donor_timeframe


class TestBatchOptimizeLookbackDays:
    """Test the lookback days used during batch optimization."""

    def test_get_optimal_lookback_days_defaults(self):
        """Verify the default lookback days for each crypto timeframe."""
        # These are the defaults from config.py tf_lookbacks
        expected = {
            "1m": 30,
            "3m": 60,
            "5m": 90,
            "15m": 180,
            "30m": 250,
            "1h": 900,
            "2h": 540,
            "4h": 350,
            "6h": 540,
            "8h": 540,
            "12h": 350,
            "1d": 1800,
        }
        for tf, expected_days in expected.items():
            actual = get_optimal_lookback_days(tf, "crypto")
            assert (
                actual == expected_days
            ), f"{tf}: expected {expected_days}, got {actual}"

    def test_donor_timeframes(self):
        """Verify the donor timeframes used for resampling."""
        # KuCoin native timeframes that DON'T need resampling
        assert get_donor_timeframe("kucoin", "1m") is None  # native
        assert get_donor_timeframe("kucoin", "5m") is None  # native
        assert get_donor_timeframe("kucoin", "15m") is None  # native
        assert get_donor_timeframe("kucoin", "1h") is None  # native
        assert get_donor_timeframe("kucoin", "8h") is None  # native
        assert get_donor_timeframe("kucoin", "12h") is None  # native
        assert get_donor_timeframe("kucoin", "1d") is None  # native

        # Timeframes that USE donors (resampled)
        assert get_donor_timeframe("kucoin", "3m") == "1m"  # donor: 1m
        assert get_donor_timeframe("kucoin", "30m") == "15m"  # donor: 15m
        assert get_donor_timeframe("kucoin", "2h") == "1h"  # donor: 1h
        assert get_donor_timeframe("kucoin", "4h") == "1h"  # donor: 1h
        assert get_donor_timeframe("kucoin", "6h") == "1h"  # donor: 1h

    def _run_lookback_test(
        self, timeframe, expected_days, mock_optimizer, mock_dataloader
    ):
        """Helper to test lookback for a specific timeframe."""
        mock_optimizer_instance = Mock()
        mock_optimizer_instance.optimize.return_value = {
            "best_params": {"fast_ema": 9, "slow_ema": 21},
            "best_value": 1.5,
            "target_metric": "sharpe_ratio",
            "trade_count": 10,
            "sharpe_ratio": 1.5,
            "calmar_ratio": 2.0,
            "win_rate": 0.6,
            "max_drawdown_pct": 10.0,
            "total_return_pct": 50.0,
        }
        mock_optimizer.return_value = mock_optimizer_instance

        captured_calls = []

        def capture_load_candles(
            exchange, symbol, timeframe, limit=None, days=None, use_cache=True, **kwargs
        ):
            captured_calls.append(
                {
                    "exchange": exchange,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "limit": limit,
                    "days": days,
                    "use_cache": use_cache,
                    "lookback_days": kwargs.get("lookback_days"),
                }
            )
            import pandas as pd

            # Return enough candles to pass min_candles check (default 500)
            freq_map = {
                "1m": "1min",
                "3m": "3min",
                "5m": "5min",
                "15m": "15min",
                "30m": "30min",
                "1h": "1h",
                "2h": "2h",
                "4h": "4h",
                "6h": "6h",
                "8h": "8h",
                "12h": "12h",
                "1d": "1D",
            }
            freq = freq_map.get(timeframe, "1min")
            df = pd.DataFrame(
                {
                    "timestamp": pd.date_range(
                        "2024-01-01", periods=1000, freq=freq, tz="UTC"
                    ),
                    "open": [100] * 1000,
                    "high": [101] * 1000,
                    "low": [99] * 1000,
                    "close": [100] * 1000,
                    "volume": [1000] * 1000,
                }
            )
            df["timestamp"] = df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
            return df

        mock_dataloader.load_candles.side_effect = capture_load_candles
        mock_dataloader.load_candles_sync.side_effect = capture_load_candles

        config = BatchOptimizerConfig(
            exchange="kucoin",
            symbols=["BTC/USD"],
            timeframes=[timeframe],
            strategy_mode="crossover",
            target_metric="sharpe_ratio",
            n_trials=10,
            days=None,
        )

        optimizer = BatchOptimizer(config)

        async def _fake_save_pg(**kwargs):
            return None

        async def _fake_load_pg(**kwargs):
            return []

        import src.batch_optimizer as _bm

        with patch.object(_bm, "save_batch_result_pg", _fake_save_pg), patch.object(
            _bm, "load_batch_results_pg", _fake_load_pg
        ):
            asyncio.run(optimizer.run())

        print(f"Captured {len(captured_calls)} load_candles calls:")
        for call in captured_calls:
            print(
                f"  {call['symbol']} ({call['timeframe']}): days={call['days']}, limit={call['limit']}"
            )

        # For natively supported timeframes, it fetches directly with its optimal lookback
        # For donor-based timeframes, it fetches the donor with the TARGET's optimal lookback
        donor = get_donor_timeframe("kucoin", timeframe)
        if donor is None:
            # Native - fetches target timeframe directly
            calls_tf = [c for c in captured_calls if c["timeframe"] == timeframe]
            assert len(calls_tf) > 0, f"Should have fetched {timeframe} natively"
            expected_tf = timeframe
        else:
            # Uses donor - fetches donor timeframe with target's lookback days
            calls_tf = [c for c in captured_calls if c["timeframe"] == donor]
            assert (
                len(calls_tf) > 0
            ), f"Should have fetched donor {donor} for {timeframe}"
            expected_tf = donor

        for call in calls_tf:
            assert (
                call["days"] == expected_days
            ), f"For {timeframe} (fetched as {expected_tf}), expected {expected_days} days, got {call['days']}"

    @patch("src.batch_optimizer.DataLoader")
    @patch("src.batch_optimizer.OptunaOptimizer")
    def test_batch_optimizer_uses_correct_lookback_for_all_timeframes(
        self, mock_optimizer, mock_dataloader
    ):
        """Verify batch optimizer uses correct lookback for all KuCoin timeframes."""
        test_cases = [
            ("1m", 30),  # native
            ("3m", 60),  # donor: 1m
            ("5m", 90),  # native
            ("15m", 180),  # native
            ("30m", 250),  # donor: 15m
            ("1h", 900),  # native
            ("2h", 540),  # donor: 1h
            ("4h", 350),  # donor: 1h
            ("6h", 540),  # donor: 1h
            ("8h", 540),  # native
            ("12h", 350),  # native
            ("1d", 1800),  # native
        ]

        for timeframe, expected_days in test_cases:
            print(f"--- Testing {timeframe} (expected {expected_days} days) ---")
            self._run_lookback_test(
                timeframe, expected_days, mock_optimizer, mock_dataloader
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
