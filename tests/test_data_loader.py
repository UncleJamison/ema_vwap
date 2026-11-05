"""
Unit tests for Data Loader Module.
"""

import asyncio
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from src.data_loader import DataLoader


def test_generate_synthetic_candles():
    df = DataLoader.generate_synthetic_candles(num_bars=100)
    assert len(df) == 100
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    # High should be >= low always
    assert (df["high"] >= df["low"]).all()
    # Volume should be positive
    assert (df["volume"] > 0).all()


def test_format_symbols():
    from src.data_loader import normalize_symbol

    assert normalize_symbol("btcusd") == "BTC/USD"
    assert normalize_symbol("BTC-USDT") == "BTC/USDT"
    assert normalize_symbol("eth_usd") == "ETH/USD"
    assert normalize_symbol("bonkusdt") == "BONK/USDT"

    assert DataLoader.format_gemini_symbol("BTC/USD") == "btcusd"
    assert DataLoader.format_gemini_symbol("ETH/USDT") == "ethusd"
    # KuCoin uses dash format
    assert DataLoader.format_kucoin_symbol("BTC/USD") == "BTC-USDT"
    assert DataLoader.format_kucoin_symbol("ETH/USDT") == "ETH-USDT"


def test_load_candles_fallback(monkeypatch):
    # Mock the PostgreSQL functions to avoid needing a real DB
    mock_save = AsyncMock(return_value=50)
    mock_load = AsyncMock(return_value=pd.DataFrame())
    monkeypatch.setattr("src.data_loader.save_candles_pg", mock_save)
    monkeypatch.setattr("src.data_loader.load_candles_pg", mock_load)

    # Should cleanly load synthetic candles if exchange is synthetic
    df = DataLoader.load_candles_sync(exchange="synthetic", limit=50)
    assert len(df) == 50


def test_resample_and_upsert(tmp_path, monkeypatch):
    from src.data_loader import DataLoader

    # Mock the PostgreSQL functions
    mock_save = AsyncMock(return_value=100)
    mock_load = AsyncMock(return_value=pd.DataFrame())
    monkeypatch.setattr("src.data_loader.save_candles_pg", mock_save)
    monkeypatch.setattr("src.data_loader.load_candles_pg", mock_load)

    df_base = DataLoader.generate_synthetic_candles(
        "TEST/USD", timeframe="5m", num_bars=300
    )

    # Test the async method directly
    count = asyncio.run(
        DataLoader.resample_and_upsert(
            "test_ex", "TEST/USD", df_base, base_timeframe="5m"
        )
    )
    assert count > 0

    # Verify save_candles_pg was called
    assert mock_save.called


def test_multi_timeframe_resampling_all_tfs(tmp_path, monkeypatch):
    import asyncio

    from src.data_loader import DataLoader

    # Mock the PostgreSQL functions
    mock_save = AsyncMock(return_value=100)
    mock_load = AsyncMock(return_value=pd.DataFrame())
    monkeypatch.setattr("src.data_loader.save_candles_pg", mock_save)
    monkeypatch.setattr("src.data_loader.load_candles_pg", mock_load)

    df_1m = DataLoader.generate_synthetic_candles(
        "MTF/USD", timeframe="1m", num_bars=720
    )
    total_upserted = asyncio.run(
        DataLoader.resample_and_upsert("test_ex", "MTF/USD", df_1m, base_timeframe="1m")
    )
    assert total_upserted > 0

    for tf in ["3m", "5m", "15m", "30m", "1h", "2h", "4h"]:
        # In a real test with DB, we would check the resampled data
        pass


def test_kucoin_3m_cold_cache_fetches_only_one_minute_donor(monkeypatch):
    now = pd.Timestamp.now(tz="UTC")
    target_df = pd.DataFrame(
        {
            "timestamp": [now - pd.Timedelta(minutes=3 * i) for i in range(3, -1, -1)],
            "open": [1.0, 2.0, 3.0, 4.0],
            "high": [1.5, 2.5, 3.5, 4.5],
            "low": [0.5, 1.5, 2.5, 3.5],
            "close": [1.0, 2.0, 3.0, 4.0],
            "volume": [10.0, 20.0, 30.0, 40.0],
        }
    )
    donor_df = pd.DataFrame(
        {
            "timestamp": [now - pd.Timedelta(minutes=i) for i in range(3, -1, -1)],
            "open": [1.0, 2.0, 3.0, 4.0],
            "high": [1.5, 2.5, 3.5, 4.5],
            "low": [0.5, 1.5, 2.5, 3.5],
            "close": [1.0, 2.0, 3.0, 4.0],
            "volume": [10.0, 20.0, 30.0, 40.0],
        }
    )
    target_available = False
    load_calls = []
    fetch_calls = []

    async def fake_load_candles_pg(exchange, symbol, timeframe, limit=None, **kwargs):
        nonlocal target_available
        load_calls.append((timeframe, limit))
        if timeframe == "3m" and target_available:
            return target_df
        return pd.DataFrame()

    async def fake_fetch_and_store(cls, **kwargs):
        nonlocal target_available
        fetch_calls.append(kwargs)
        target_available = True
        return donor_df

    monkeypatch.setattr("src.data_loader.load_candles_pg", fake_load_candles_pg)
    monkeypatch.setattr(
        DataLoader, "_fetch_and_store", classmethod(fake_fetch_and_store)
    )

    result = asyncio.run(
        DataLoader.load_candles(
            exchange="kucoin",
            symbol="ZEC/USD",
            timeframe="3m",
            limit=4,
        )
    )

    assert len(result) == 4
    assert result["timestamp"].is_monotonic_increasing
    assert fetch_calls == [
        {
            "exchange": "kucoin",
            "symbol": "ZEC/USD",
            "timeframe": "1m",
            "days": None,
            "limit": 4,
            "target_tfs": ["3m"],
        }
    ]
    assert [timeframe for timeframe, _ in load_calls] == ["3m", "1m", "3m"]


def test_kucoin_3m_fresh_target_cache_skips_donor_fetch(monkeypatch):
    now = pd.Timestamp.now(tz="UTC")
    cached_df = pd.DataFrame(
        {
            "timestamp": [now - pd.Timedelta(minutes=3 * i) for i in range(3, -1, -1)],
            "open": [1.0] * 4,
            "high": [2.0] * 4,
            "low": [0.5] * 4,
            "close": [1.5] * 4,
            "volume": [10.0] * 4,
        }
    )
    load_calls = []

    async def fake_load_candles_pg(exchange, symbol, timeframe, limit=None, **kwargs):
        load_calls.append(timeframe)
        return cached_df if timeframe == "3m" else pd.DataFrame()

    async def unexpected_fetch(cls, **kwargs):
        raise AssertionError("fresh target cache must not trigger a donor fetch")

    monkeypatch.setattr("src.data_loader.load_candles_pg", fake_load_candles_pg)
    monkeypatch.setattr(DataLoader, "_fetch_and_store", classmethod(unexpected_fetch))

    result = asyncio.run(
        DataLoader.load_candles(
            exchange="kucoin",
            symbol="ZEC/USD",
            timeframe="3m",
            limit=4,
        )
    )

    assert len(result) == 4
    assert load_calls == ["3m"]


def test_kucoin_empty_provider_result_is_not_replaced_with_synthetic(monkeypatch):
    class EmptyKucoinProvider:
        def fetch_historical_candles(self, **kwargs):
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

    monkeypatch.setattr(
        "src.data_loader.registry.get", lambda exchange: EmptyKucoinProvider()
    )

    result = asyncio.run(
        DataLoader.fetch_kucoin_historical(
            symbol="ZEC/USD",
            timeframe="1m",
            days=1,
        )
    )

    assert result.empty
    assert list(result.columns) == [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]


def test_gemini_empty_provider_result_fails_without_synthetic_persistence(monkeypatch):
    class EmptyGeminiProvider:
        def fetch_historical_candles(self, **kwargs):
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

    save_mock = AsyncMock()
    monkeypatch.setattr(
        "src.data_loader.registry.get", lambda exchange: EmptyGeminiProvider()
    )
    monkeypatch.setattr("src.data_loader.get_donor_timeframe", lambda *args: None)
    monkeypatch.setattr("src.data_loader.save_candles_pg", save_mock)

    with pytest.raises(RuntimeError, match="synthetic data was not substituted"):
        asyncio.run(
            DataLoader.fetch_gemini_historical(
                symbol="ZEC/USD",
                timeframe="5m",
                days=1,
            )
        )

    save_mock.assert_not_awaited()


def test_empty_provider_fetch_fails_before_persistence(monkeypatch):
    async def empty_fetch(cls, **kwargs):
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

    save_mock = AsyncMock()
    monkeypatch.setattr(DataLoader, "fetch_kucoin_historical", classmethod(empty_fetch))
    monkeypatch.setattr("src.data_loader.save_candles_pg", save_mock)

    with pytest.raises(RuntimeError, match="data was not synthesized"):
        asyncio.run(
            DataLoader._fetch_and_store(
                exchange="kucoin",
                symbol="ZEC/USD",
                timeframe="5m",
                days=1,
            )
        )

    save_mock.assert_not_awaited()


def test_explicit_synthetic_fetch_persists_synthetic_origin(monkeypatch):
    save_mock = AsyncMock(return_value=10)
    monkeypatch.setattr("src.data_loader.save_candles_pg", save_mock)

    result = asyncio.run(
        DataLoader._fetch_and_store(
            exchange="synthetic",
            symbol="ZEC/USD",
            timeframe="5m",
            days=1,
            limit=10,
        )
    )

    assert len(result) == 10
    assert save_mock.await_args.kwargs["data_origin"] == "synthetic"
    assert save_mock.await_args.kwargs["source_timeframe"] == "5m"


def test_resampled_mixed_provenance_is_marked_unknown(monkeypatch):
    timestamps = pd.date_range("2026-01-01", periods=6, freq="min", tz="UTC")
    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100.0] * 6,
            "high": [102.0] * 6,
            "low": [99.0] * 6,
            "close": [101.0] * 6,
            "volume": [10.0] * 6,
            "data_origin": [
                "provider",
                "synthetic",
                "provider",
                "provider",
                "provider",
                "provider",
            ],
            "source_timeframe": ["1m", "1m", "1m", "1m", "1m", "1m"],
        }
    )
    save_mock = AsyncMock(side_effect=lambda **kwargs: len(kwargs["df"]))
    monkeypatch.setattr("src.data_loader.save_candles_pg", save_mock)

    count = asyncio.run(
        DataLoader.resample_and_upsert(
            exchange="kucoin",
            symbol="ZEC/USD",
            df=df,
            base_timeframe="1m",
            target_tfs=["3m"],
        )
    )

    assert count == 2
    saved_provenance = {
        (
            call.kwargs["data_origin"],
            call.kwargs["source_timeframe"],
        )
        for call in save_mock.await_args_list
    }
    assert saved_provenance == {("unknown", "mixed"), ("provider", "1m")}


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
