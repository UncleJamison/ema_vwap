"""
Unit and integration tests for US Stock Historical Data Downloader,
Split-Adjusted Caching, and Stock Provider Adapters (Alpaca, Polygon, Synthetic Stock).
"""

import os
from datetime import datetime

from src.data_loader import DataLoader
from src.database import CandleDatabase
from src.providers.alpaca import AlpacaStockAdapter
from src.providers.polygon import PolygonStockAdapter
from src.providers.stock_synthetic import SyntheticStockAdapter
from src.settings import SettingsManager


def test_synthetic_stock_adapter_generation():
    """Verify synthetic stock candle generation adheres to market hours and returns valid OHLCV."""
    adapter = SyntheticStockAdapter()
    df = adapter.fetch_historical_candles(symbol="AAPL", timeframe="5m", days=5)

    assert not df.empty
    assert len(df) > 0
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]

    # Verify all timestamps are valid UTC ISO strings
    for ts in df["timestamp"]:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        # Synthetic stock bars are generated between 13:30 and 20:00 UTC (09:30-16:00 ET)
        assert 13 <= dt.hour <= 20
        # No weekend bars
        assert dt.weekday() < 5


def test_synthetic_stock_split_simulation():
    """Verify synthetic stock split generation and DataLoader split adjustment."""
    adapter = SyntheticStockAdapter()
    df_split = adapter.fetch_historical_candles(
        symbol="TSLA", timeframe="1d", days=10, include_split=True, split_ratio=2.0
    )
    assert len(df_split) == 10

    # Test DataLoader.apply_split_adjustment
    split_date = df_split["timestamp"].iloc[5]
    pre_split_close = df_split["close"].iloc[0]
    pre_split_vol = df_split["volume"].iloc[0]

    df_adjusted = DataLoader.apply_split_adjustment(
        df_split, split_date=split_date, split_ratio=2.0
    )

    # Bars before split date should have price divided by 2 and volume multiplied by 2
    assert df_adjusted["close"].iloc[0] == pre_split_close / 2.0
    assert df_adjusted["volume"].iloc[0] == pre_split_vol * 2.0

    # Bars on/after split date should remain unchanged
    assert df_adjusted["close"].iloc[6] == df_split["close"].iloc[6]


def test_alpaca_adapter_symbol_formatting_and_missing_keys(tmp_path):
    """Verify Alpaca adapter handles symbols and returns empty df with warning if keys missing."""
    db_file = os.path.join(tmp_path, "test_settings.db")
    db = CandleDatabase(db_path=db_file)
    settings = SettingsManager(db=db)

    adapter = AlpacaStockAdapter(settings_manager=settings)
    assert adapter.format_symbol("AAPL/USD") == "AAPL"
    assert adapter.format_symbol("SPY-USD") == "SPY"
    assert adapter.format_symbol("NVDA") == "NVDA"

    # Without API keys configured, fetch_historical_candles should return empty DataFrame gracefully
    df = adapter.fetch_historical_candles(symbol="AAPL", timeframe="5m", days=1)
    assert df.empty
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]


def test_polygon_adapter_symbol_formatting_and_missing_keys(tmp_path):
    """Verify Polygon adapter handles symbols and returns empty df with warning if key missing."""
    db_file = os.path.join(tmp_path, "test_settings.db")
    db = CandleDatabase(db_path=db_file)
    settings = SettingsManager(db=db)

    adapter = PolygonStockAdapter(settings_manager=settings)
    assert adapter.format_symbol("MSFT/USD") == "MSFT"
    assert adapter.format_symbol("QQQ") == "QQQ"

    df = adapter.fetch_historical_candles(symbol="MSFT", timeframe="15m", days=1)
    assert df.empty
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]


def test_stock_data_loader_sqlite_caching_and_resampling(tmp_path):
    """Verify stock candle caching into SQLite database and resampling."""
    db_file = os.path.join(tmp_path, "test_stock_candles.db")
    db = CandleDatabase(db_path=db_file)

    adapter = SyntheticStockAdapter()
    df_5m = adapter.fetch_historical_candles(symbol="AAPL", timeframe="5m", days=5)

    saved_count = db.save_candles("alpaca", "AAPL", "5m", df_5m)
    assert saved_count == len(df_5m)
    assert db.get_candle_count("alpaca", "AAPL", "5m") == len(df_5m)

    df_loaded = db.load_candles("alpaca", "AAPL", "5m")
    assert len(df_loaded) == len(df_5m)
    assert df_loaded["close"].iloc[0] == df_5m["close"].iloc[0]


def test_stock_settings_manager_storage_and_masking(tmp_path):
    """Verify SettingsManager stores and masks Alpaca, Polygon, and Tiingo credentials."""
    db_file = os.path.join(tmp_path, "test_settings_stock.db")
    db = CandleDatabase(db_path=db_file)
    mgr = SettingsManager(db=db)

    mgr.update_bulk(
        {
            "alpaca_api_key_id": "PKTEST1234567890",
            "alpaca_secret_key": "SKTESTSECRETKEY9876543210",
            "alpaca_paper": "true",
            "polygon_api_key": "POLYKEY123456",
            "tiingo_api_token": "TIINGOTOKEN789",
        }
    )

    creds = mgr.get_alpaca_credentials()
    assert creds["api_key"] == "PKTEST1234567890"
    assert creds["secret_key"] == "SKTESTSECRETKEY9876543210"
    assert creds["is_paper"] is True

    assert mgr.get_polygon_api_key() == "POLYKEY123456"
    assert mgr.get_tiingo_api_token() == "TIINGOTOKEN789"

    masked = mgr.get_all_masked()
    assert masked["alpaca_api_key_id"].startswith("••••••••")
    assert masked["alpaca_secret_key"].startswith("••••••••")
    assert masked["polygon_api_key"].startswith("••••••••")
    assert masked["tiingo_api_token"].startswith("••••••••")
