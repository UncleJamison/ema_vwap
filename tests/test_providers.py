"""
Unit tests for Data Providers, Adapters, Registry, and Timezone Pipeline.
"""

import pandas as pd
import pytest

from src.providers import (
    BaseDataProvider,
    GeminiAdapter,
    KuCoinAdapter,
    ProviderRegistry,
    SyntheticAdapter,
    get_provider,
    get_session_anchor_utc,
    is_market_hours,
    normalize_symbol,
    register_provider,
    to_utc_series,
    utc_to_market_tz,
)


def test_normalize_symbol():
    assert normalize_symbol("btcusd") == "BTC/USD"
    assert normalize_symbol("BTC-USDT") == "BTC/USDT"
    assert normalize_symbol("eth_usd") == "ETH/USD"
    assert normalize_symbol("bonkusdt") == "BONK/USDT"
    assert normalize_symbol("AAPL") == "AAPL/USD"
    assert normalize_symbol("sol/usdc") == "SOL/USDC"


def test_base_data_provider_validation():
    # Empty DataFrame
    empty_df = BaseDataProvider.validate_and_normalize_df(pd.DataFrame())
    assert list(empty_df.columns) == [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    assert empty_df.empty

    # Missing column raises ValueError
    bad_df = pd.DataFrame({"timestamp": [1700000000], "open": [100.0]})
    with pytest.raises(ValueError, match="Missing required candle column"):
        BaseDataProvider.validate_and_normalize_df(bad_df)

    # Valid raw data with unix timestamps in seconds and duplicates
    raw_df = pd.DataFrame(
        {
            "timestamp": [
                1700000060,
                1700000000,
                1700000000,
            ],  # out of order & duplicate
            "open": ["100.5", "99.0", "99.0"],
            "high": ["101.0", "100.0", "100.0"],
            "low": ["100.0", "98.5", "98.5"],
            "close": ["100.8", "99.5", "99.5"],
            "volume": ["10.2", "15.0", "15.0"],
        }
    )
    norm_df = BaseDataProvider.validate_and_normalize_df(raw_df)
    assert len(norm_df) == 2  # duplicate removed
    assert norm_df["open"].dtype == float
    assert pd.api.types.is_datetime64_any_dtype(norm_df["timestamp"])
    assert norm_df["timestamp"].iloc[0] < norm_df["timestamp"].iloc[1]  # sorted


def test_gemini_adapter():
    gemini = GeminiAdapter()
    assert gemini.name == "gemini"
    assert gemini.asset_type == "crypto"
    assert gemini.format_symbol("BTC/USD") == "btcusd"
    assert gemini.format_symbol("ETH/USDT") == "ethusd"

    # Supported timeframes & donors
    assert "1m" in gemini.get_supported_timeframes()
    assert "5m" in gemini.get_supported_timeframes()
    assert gemini.get_donor_timeframe("5m") is None
    # 2h is unsupported on Gemini, should donor from 1h
    assert gemini.get_donor_timeframe("2h") == "1h"
    # 3m is unsupported, should donor from 1m
    assert gemini.get_donor_timeframe("3m") == "1m"


def test_kucoin_adapter():
    kucoin = KuCoinAdapter()
    assert kucoin.name == "kucoin"
    assert kucoin.asset_type == "crypto"
    assert kucoin.format_symbol("BTC/USD") == "BTC-USDT"
    assert kucoin.format_symbol("ETH/USDT") == "ETH-USDT"

    assert "1m" in kucoin.get_supported_timeframes()
    assert "3m" in kucoin.get_supported_timeframes()
    assert "2h" in kucoin.get_supported_timeframes()
    assert kucoin.get_donor_timeframe("2h") is None


def test_synthetic_adapter():
    synth = SyntheticAdapter()
    assert synth.name == "synthetic"
    assert synth.asset_type == "synthetic"
    assert synth.format_symbol("BTC/USDT") == "BTC/USDT"

    # Seed reproducibility
    end_time = "2024-01-15T12:00:00Z"
    df1 = synth.generate_candles(
        "BTC/USD", timeframe="5m", num_bars=50, seed=123, end_time=end_time
    )
    df2 = synth.generate_candles(
        "BTC/USD", timeframe="5m", num_bars=50, seed=123, end_time=end_time
    )
    assert df1.equals(df2)
    assert len(df1) == 50

    # Multi-asset initial prices
    assert synth.get_initial_price("BONK/USD") == 0.000022
    assert synth.get_initial_price("ETH/USD") == 3400.0
    assert synth.get_initial_price("AAPL/USD") == 220.0
    assert synth.get_initial_price("CUSTOM", start_price=500.0) == 500.0


def test_provider_registry():
    custom_reg = ProviderRegistry()
    assert "gemini" in custom_reg.list_providers()
    assert "kucoin" in custom_reg.list_providers()
    assert "synthetic" in custom_reg.list_providers()

    gemini = custom_reg.get("GEMINI")
    assert isinstance(gemini, GeminiAdapter)

    # Fallback to synthetic
    unknown = custom_reg.get("unknown_exchange")
    assert isinstance(unknown, SyntheticAdapter)

    # Custom provider registration
    class CustomProvider(BaseDataProvider):
        name = "mock_exchange"

        def format_symbol(self, symbol: str) -> str:
            return symbol.lower()

        def fetch_historical_candles(
            self, symbol="BTC/USD", timeframe="5m", days=180, limit=None
        ):
            return pd.DataFrame()

    custom_reg.register(CustomProvider())
    assert "mock_exchange" in custom_reg.list_providers()
    assert isinstance(custom_reg.get("mock_exchange"), CustomProvider)

    # Test global convenience functions
    assert isinstance(get_provider("gemini"), GeminiAdapter)
    register_provider(CustomProvider())
    assert isinstance(get_provider("mock_exchange"), CustomProvider)


def test_timezone_pipeline_to_utc_and_market_tz():
    # Unix seconds
    s_sec = to_utc_series([1700000000, 1700000060])
    assert s_sec.dt.tz is not None
    assert str(s_sec.dt.tz) == "UTC"

    # String ISO
    s_iso = to_utc_series(["2024-01-15T14:30:00Z"])
    assert s_iso.iloc[0].hour == 14

    # Convert UTC to US/Eastern market time
    ny_s = utc_to_market_tz(s_iso, target_tz="America/New_York")
    # Jan 15 is Standard Time (EST = UTC-5), so 14:30 UTC -> 09:30 EST
    assert ny_s.iloc[0].hour == 9
    assert ny_s.iloc[0].minute == 30

    # DataFrame conversion
    df = pd.DataFrame({"timestamp": ["2024-01-15T14:30:00Z"], "open": [100.0]})
    df_ny = utc_to_market_tz(df, target_tz="America/New_York")
    assert df_ny["timestamp"].iloc[0].hour == 9


def test_timezone_is_market_hours():
    # Monday 2024-01-15 14:30:00 UTC == 09:30:00 EST (RTH Open)
    rth_open_utc = pd.Timestamp("2024-01-15 14:30:00", tz="UTC")
    assert is_market_hours(rth_open_utc, session_type="rth") is True
    assert is_market_hours(rth_open_utc, session_type="pre") is False

    # Monday 2024-01-15 21:00:00 UTC == 16:00:00 EST (RTH Closed, Post-Market Open)
    post_utc = pd.Timestamp("2024-01-15 21:00:00", tz="UTC")
    assert is_market_hours(post_utc, session_type="rth") is False
    assert is_market_hours(post_utc, session_type="post") is True

    # Monday 2024-01-15 10:00:00 UTC == 05:00:00 EST (Pre-Market)
    pre_utc = pd.Timestamp("2024-01-15 10:00:00", tz="UTC")
    assert is_market_hours(pre_utc, session_type="pre") is True
    assert is_market_hours(pre_utc, session_type="rth") is False

    # Weekend (Sunday 2024-01-14)
    sunday_utc = pd.Timestamp("2024-01-14 15:00:00", tz="UTC")
    assert is_market_hours(sunday_utc, session_type="rth") is False
    assert is_market_hours(sunday_utc, session_type="extended") is False

    # Crypto 24/7 is always true
    assert is_market_hours(sunday_utc, session_type="crypto") is True


def test_session_anchor_utc():
    # Crypto anchor: midnight UTC
    crypto_ts = pd.Timestamp("2024-01-15 18:45:00", tz="UTC")
    crypto_anchor = get_session_anchor_utc(crypto_ts, asset_type="crypto")
    assert crypto_anchor == pd.Timestamp("2024-01-15 00:00:00", tz="UTC")

    # US Stock anchor: 09:30 AM ET (EST is UTC-5 in Jan -> 14:30 UTC)
    stock_ts = pd.Timestamp("2024-01-15 18:45:00", tz="UTC")
    stock_anchor = get_session_anchor_utc(stock_ts, asset_type="stock")
    assert stock_anchor == pd.Timestamp("2024-01-15 14:30:00", tz="UTC")
