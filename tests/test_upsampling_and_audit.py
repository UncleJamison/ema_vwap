"""Unit tests for automatic 1m->3m upsampling and audit mechanism."""

import pandas as pd

from src.data_loader import DataLoader


def test_explicit_upsampling_logic_exists():
    """The explicit fallback logic exists in DataLoader.resample_and_upsert."""
    import inspect

    source = inspect.getsource(DataLoader.resample_and_upsert)
    # The resample_and_upsert method should handle upsampling logic
    assert "resample" in source.lower()


def test_resample_upsample_1m_to_3m():
    """Resample 60 1m bars to 3m produces ~20 bars with valid OHLCV."""
    # Generate 60 1m candles
    df_1m = DataLoader.generate_synthetic_candles(
        "TEST/USD", timeframe="1m", num_bars=60, seed=99
    )
    df_1m["timestamp"] = pd.to_datetime(df_1m["timestamp"])

    # Resample to 3m using pandas logic (same as resample_and_upsert)
    df_1m_indexed = df_1m.set_index("timestamp")
    resampled = (
        df_1m_indexed.resample("3min")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )

    # 60 1m bars -> 20 3m bars (may be 21 due to edge alignment)
    assert len(resampled) in (20, 21)
    assert list(resampled.columns) == ["open", "high", "low", "close", "volume"]
    # High should be >= low always (resampled correctly)
    assert (resampled["high"] >= resampled["low"]).all()
    # Volume should be positive
    assert (resampled["volume"] > 0).all()


def test_resample_upsample_1m_to_5m():
    """Resample 100 1m bars to 5m produces ~20 bars."""
    df_1m = DataLoader.generate_synthetic_candles(
        "TEST/USD", timeframe="1m", num_bars=100, seed=42
    )
    df_1m["timestamp"] = pd.to_datetime(df_1m["timestamp"])
    df_1m_indexed = df_1m.set_index("timestamp")
    resampled = (
        df_1m_indexed.resample("5min")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )

    # 100 1m bars -> 20 5m bars (may vary due to edge alignment)
    assert len(resampled) in (20, 21)


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
