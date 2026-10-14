"""Tests for data providers (src/providers/*): base, crypto, alpaca, polygon, synthetic, stock_synthetic.
Tests cover normalize_symbol, format_symbol, fetch_historical_candles, pagination, error handling."""

import json
from typing import ClassVar
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from src.providers.alpaca import ALPACA_TIMEFRAMES, AlpacaStockAdapter
from src.providers.base import (
    STANDARD_TF_ORDER,
    BaseDataProvider,
    normalize_symbol,
)
from src.providers.crypto import GeminiAdapter, KuCoinAdapter
from src.providers.polygon import PolygonStockAdapter
from src.providers.registry import get_provider, registry
from src.providers.stock_synthetic import SyntheticStockAdapter
from src.providers.synthetic import SyntheticAdapter
from src.providers.timezone import to_utc_series

list_providers = registry.list_providers


class TestNormalizeSymbol:
    """Tests for the normalize_symbol utility function."""

    def test_basic_normalization(self):
        assert normalize_symbol("BTC/USD") == "BTC/USD"
        assert normalize_symbol("BTC-USD") == "BTC/USD"
        assert normalize_symbol("BTC_USD") == "BTC/USD"
        assert normalize_symbol("btcusd") == "BTC/USD"
        assert normalize_symbol("BTC-USDT") == "BTC/USDT"
        assert normalize_symbol("btc-usdt") == "BTC/USDT"
        assert normalize_symbol("ETH-USD") == "ETH/USD"
        assert normalize_symbol("AAPL") == "AAPL/USD"
        assert normalize_symbol("AAPL/USD") == "AAPL/USD"

    def test_stablecoin_quotes(self):
        assert normalize_symbol("BTC-USDC") == "BTC/USDC"
        assert normalize_symbol("BTC-USDT") == "BTC/USDT"
        assert normalize_symbol("ETH-BUSD") == "ETH/BUSD"

    def test_edge_cases(self):
        # Empty or whitespace
        assert normalize_symbol("  BTC/USD  ") == "BTC/USD"
        # Multiple separators
        assert (
            normalize_symbol("BTC--USD") == "BTC//USD"
        )  # Double hyphen becomes double slash
        assert normalize_symbol("BTC__USD") == "BTC/USD"


class TestStandardTFOrder:
    """Tests for STANDARD_TF_ORDER constant."""

    def test_order_ascending(self):
        """Verify timeframes are ordered from smallest to largest."""
        # Just check the order makes sense
        assert STANDARD_TF_ORDER[0] == "1m"
        assert STANDARD_TF_ORDER[-1] == "1d"
        assert len(STANDARD_TF_ORDER) == 10


class TestBaseDataProvider:
    """Tests for BaseDataProvider abstract base class."""

    def test_abstract_methods(self):
        """Verify abstract methods are defined."""
        abstract_methods = [
            "format_symbol",
            "fetch_historical_candles",
        ]
        for method in abstract_methods:
            assert hasattr(BaseDataProvider, method)
            assert getattr(BaseDataProvider, method).__isabstractmethod__

    def test_class_vars(self):
        """Test class variable defaults."""
        assert BaseDataProvider.name == "base"
        assert BaseDataProvider.asset_type == "crypto"
        assert "1m" in BaseDataProvider.native_timeframes
        assert "1d" in BaseDataProvider.native_timeframes

    def test_normalize_symbol_delegation(self):
        """Test that instance method delegates to module function."""

        class TestProvider(BaseDataProvider):
            name = "test"

            def format_symbol(self, symbol):
                return symbol

            def fetch_historical_candles(self, *args, **kwargs):
                return None

        p = TestProvider()
        assert p.normalize_symbol("btc-usd") == "BTC/USD"

    def test_get_donor_timeframe(self):
        """Test donor timeframe logic."""

        class TestProvider(BaseDataProvider):
            name = "test"
            native_timeframes: ClassVar[set[str]] = {"1m", "5m", "15m", "1h"}

            def format_symbol(self, symbol):
                return symbol

            def fetch_historical_candles(self, *args, **kwargs):
                return None

        p = TestProvider()
        # Target is native -> None
        assert p.get_donor_timeframe("5m") is None
        # Target not native, smaller donor exists
        assert p.get_donor_timeframe("3m") == "1m"
        # Target not native, larger donor
        assert p.get_donor_timeframe("30m") == "15m"
        # Target not native, no smaller donor
        assert p.get_donor_timeframe("1m") is None

    def test_validate_and_normalize_df(self):
        """Test DataFrame validation and normalization."""
        df = pd.DataFrame(
            {
                "timestamp": [1704067200000, 1704067260000],  # ms timestamps
                "open": [50000, 50100],
                "high": [50200, 50300],
                "low": [49900, 50000],
                "close": [50100, 50200],
                "volume": [100, 200],
            }
        )
        normalized = BaseDataProvider.validate_and_normalize_df(df)
        assert "timestamp" in normalized.columns
        assert pd.api.types.is_datetime64_any_dtype(normalized["timestamp"])
        assert normalized["timestamp"].dt.tz is not None
        for col in ["open", "high", "low", "close", "volume"]:
            assert normalized[col].dtype == float

    def test_validate_and_normalize_df_empty(self):
        """Test empty DataFrame handling."""
        df = pd.DataFrame()
        result = BaseDataProvider.validate_and_normalize_df(df)
        assert list(result.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
        assert len(result) == 0


class TestGeminiAdapter:
    """Tests for GeminiAdapter."""

    def test_class_vars(self):
        assert GeminiAdapter.name == "gemini"
        assert GeminiAdapter.asset_type == "crypto"
        assert "1m" in GeminiAdapter.native_timeframes
        assert "6h" in GeminiAdapter.native_timeframes

    def test_format_symbol(self):
        adapter = GeminiAdapter()
        assert adapter.format_symbol("BTC/USD") == "btcusd"
        assert adapter.format_symbol("BTC/USDT") == "btcusd"
        assert adapter.format_symbol("ETH/USD") == "ethusd"

    @patch("src.providers.crypto.requests.get")
    def test_fetch_historical_candles_success(self, mock_get):
        adapter = GeminiAdapter()
        mock_response = Mock()
        mock_response.json.return_value = [
            [1704067200000, "50000", "50200", "49900", "50100", "100"],
            [1704067260000, "50100", "50300", "50000", "50200", "200"],
        ]
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        df = adapter.fetch_historical_candles(symbol="BTC/USD", timeframe="5m", days=1)
        assert len(df) == 2
        assert list(df.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
        assert (
            df["close"].iloc[0] == 50100.0
        )  # Gemini returns chronological, first candle is oldest

    @patch("src.providers.crypto.requests.get")
    def test_fetch_historical_candles_empty(self, mock_get):
        adapter = GeminiAdapter()
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        df = adapter.fetch_historical_candles()
        assert len(df) == 0

    @patch("src.providers.crypto.requests.get")
    def test_fetch_historical_candles_error(self, mock_get):
        adapter = GeminiAdapter()
        mock_get.side_effect = Exception("Network error")
        df = adapter.fetch_historical_candles()
        assert len(df) == 0


class TestKuCoinAdapter:
    """Tests for KuCoinAdapter."""

    def test_class_vars(self):
        assert KuCoinAdapter.name == "kucoin"
        assert KuCoinAdapter.asset_type == "crypto"
        assert "1m" in KuCoinAdapter.native_timeframes
        assert "4h" in KuCoinAdapter.native_timeframes

    def test_format_symbol(self):
        adapter = KuCoinAdapter()
        assert adapter.format_symbol("BTC/USD") == "BTC-USDT"
        assert adapter.format_symbol("BTC/USDT") == "BTC-USDT"
        assert adapter.format_symbol("ETH/USD") == "ETH-USDT"

    @patch("src.providers.crypto.requests.get")
    def test_fetch_historical_candles_pagination(self, mock_get):
        adapter = KuCoinAdapter()
        # First page
        resp1 = Mock()
        resp1.json.return_value = {
            "code": "200000",
            "data": [
                ["1704067200", "50000", "50200", "49900", "50100", "100", "0"],
                ["1704067260", "50100", "50300", "50000", "50200", "200", "0"],
            ],
        }
        # Second page (empty - ends pagination)
        resp2 = Mock()
        resp2.json.return_value = {"code": "200000", "data": []}
        mock_get.side_effect = [resp1, resp2]

        df = adapter.fetch_historical_candles(symbol="BTC/USD", timeframe="5m", days=1)
        assert len(df) == 2
        assert (
            df["close"].iloc[0] == 50200.0
        )  # KuCoin returns reverse chronological, normalized sorts ascending

    @patch("src.providers.crypto.requests.get")
    def test_fetch_historical_candles_error(self, mock_get):
        adapter = KuCoinAdapter()
        mock_get.side_effect = Exception("Network error")
        df = adapter.fetch_historical_candles()
        assert len(df) == 0


class TestAlpacaStockAdapter:
    """Tests for AlpacaStockAdapter."""

    def test_class_vars(self):
        assert AlpacaStockAdapter.name == "alpaca"
        assert AlpacaStockAdapter.asset_type == "stock"
        assert "1Min" in ALPACA_TIMEFRAMES.values()

    def test_format_symbol(self):
        adapter = AlpacaStockAdapter()
        assert adapter.format_symbol("AAPL/USD") == "AAPL"
        assert adapter.format_symbol("AAPL/USD") == "AAPL"
        assert adapter.format_symbol("TSLA-USD") == "TSLA"
        assert adapter.format_symbol("SPY") == "SPY"

    def test_get_headers(self):
        with patch("src.providers.alpaca.SettingsManager") as mock_settings:
            mock_settings_instance = Mock()
            mock_settings_instance.get_alpaca_credentials.return_value = {
                "api_key": "test_key",
                "secret_key": "test_secret",
                "is_paper": True,
            }
            mock_settings.return_value = mock_settings_instance

            adapter = AlpacaStockAdapter()
            headers = adapter._get_headers()
            assert headers["APCA-API-KEY-ID"] == "test_key"
            assert headers["APCA-API-SECRET-KEY"] == "test_secret"

    def test_fetch_historical_candles_no_credentials(self):
        with patch("src.providers.alpaca.SettingsManager") as mock_settings:
            mock_settings_instance = Mock()
            mock_settings_instance.get_alpaca_credentials.return_value = {}
            mock_settings.return_value = mock_settings_instance

            adapter = AlpacaStockAdapter()
            df = adapter.fetch_historical_candles(symbol="AAPL/USD")
            assert len(df) == 0

    @patch("src.providers.alpaca.urllib.request.urlopen")
    def test_fetch_historical_candles_success(self, mock_urlopen):
        with patch("src.providers.alpaca.SettingsManager") as mock_settings:
            mock_settings_instance = Mock()
            mock_settings_instance.get_alpaca_credentials.return_value = {
                "api_key": "test_key",
                "secret_key": "test_secret",
                "is_paper": True,
            }
            mock_settings.return_value = mock_settings_instance

            # Mock response
            mock_response = Mock()
            mock_response.status = 200
            mock_response.read.return_value = json.dumps(
                {
                    "bars": [
                        {
                            "t": "2024-01-01T09:30:00Z",
                            "o": 150.0,
                            "h": 151.0,
                            "l": 149.0,
                            "c": 150.5,
                            "v": 100000,
                        },
                        {
                            "t": "2024-01-01T09:31:00Z",
                            "o": 150.5,
                            "h": 151.5,
                            "l": 150.0,
                            "c": 151.0,
                            "v": 200000,
                        },
                    ],
                    "next_page_token": None,
                }
            ).encode("utf-8")
            mock_response.status = 200
            mock_urlopen.return_value.__enter__ = Mock(return_value=mock_response)
            mock_urlopen.return_value.__exit__ = Mock(return_value=False)

            adapter = AlpacaStockAdapter()
            df = adapter.fetch_historical_candles(
                symbol="AAPL/USD", timeframe="5m", days=1
            )
            assert len(df) == 2
            assert df["close"].iloc[0] == 150.5


class TestPolygonStockAdapter:
    """Tests for PolygonStockAdapter."""

    def test_class_vars(self):
        assert PolygonStockAdapter.name == "polygon"
        assert PolygonStockAdapter.asset_type == "stock"
        assert "1m" in PolygonStockAdapter.native_timeframes

    def test_format_symbol(self):
        adapter = PolygonStockAdapter()
        assert adapter.format_symbol("TSLA/USD") == "TSLA"
        assert adapter.format_symbol("SPY/USD") == "SPY"

    @patch("src.providers.polygon.urllib.request.urlopen")
    def test_fetch_historical_candles_success(self, mock_urlopen):
        with patch("src.providers.polygon.SettingsManager") as mock_settings:
            mock_settings_instance = Mock()
            mock_settings_instance.get_polygon_api_key.return_value = "test_key"
            mock_settings.return_value = mock_settings_instance

            mock_response = Mock()
            mock_response.status = 200
            mock_response.read.return_value = json.dumps(
                {
                    "results": [
                        {
                            "t": 1704067200000,
                            "o": 150.0,
                            "h": 151.0,
                            "l": 149.0,
                            "c": 150.5,
                            "v": 100000,
                        },
                        {
                            "t": 1704067260000,
                            "o": 150.5,
                            "h": 151.5,
                            "l": 150.0,
                            "c": 151.0,
                            "v": 200000,
                        },
                    ],
                    "next_url": None,
                }
            ).encode("utf-8")
            mock_response.status = 200
            mock_urlopen.return_value.__enter__ = Mock(return_value=mock_response)
            mock_urlopen.return_value.__exit__ = Mock(return_value=False)

            adapter = PolygonStockAdapter()
            df = adapter.fetch_historical_candles(
                symbol="AAPL/USD", timeframe="5m", days=1
            )
            assert len(df) == 2


class TestSyntheticAdapter:
    """Tests for SyntheticAdapter."""

    def test_class_vars(self):
        assert SyntheticAdapter.name == "synthetic"
        assert SyntheticAdapter.asset_type == "synthetic"
        assert "1m" in SyntheticAdapter.native_timeframes

    def test_validate_symbol(self):
        adapter = SyntheticAdapter()
        assert adapter.validate_symbol("BTC/USD") == "BTC/USD"
        assert adapter.validate_symbol("ETH/USD") == "ETH/USD"
        with pytest.raises(ValueError):
            adapter.validate_symbol("UNKNOWN/USD")

    def test_generate_candles(self):
        adapter = SyntheticAdapter()
        df = adapter.generate_candles(
            symbol="BTC/USD", timeframe="5m", num_bars=100, seed=42
        )
        assert len(df) == 100
        assert list(df.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
        assert df["close"].iloc[0] > 0

    def test_generate_candles_deterministic(self):
        adapter = SyntheticAdapter()
        df1 = adapter.generate_candles(symbol="BTC/USD", num_bars=50, seed=42)
        df2 = adapter.generate_candles(symbol="BTC/USD", num_bars=50, seed=42)
        pd.testing.assert_frame_equal(df1, df2)

    def test_fetch_historical_candles(self):
        adapter = SyntheticAdapter()
        df = adapter.fetch_historical_candles(symbol="BTC/USD", timeframe="5m", days=1)
        assert len(df) > 0
        assert "close" in df.columns


class TestSyntheticStockAdapter:
    """Tests for SyntheticStockAdapter."""

    def test_class_vars(self):
        assert SyntheticStockAdapter.name == "synthetic_stock"
        assert SyntheticStockAdapter.asset_type == "stock"

    def test_generate_candles_not_implemented(self):
        adapter = SyntheticStockAdapter()
        # SyntheticStockAdapter doesn't implement generate_candles
        assert not hasattr(adapter, "generate_candles")


class TestRegistry:
    """Tests for provider registry."""

    def test_get_provider(self):
        provider = get_provider("gemini")
        assert isinstance(provider, GeminiAdapter)

        provider = get_provider("kucoin")
        assert isinstance(provider, KuCoinAdapter)

        provider = get_provider("alpaca")
        assert isinstance(provider, AlpacaStockAdapter)

        provider = get_provider("polygon")
        assert isinstance(provider, PolygonStockAdapter)

        provider = get_provider("synthetic")
        assert isinstance(provider, SyntheticAdapter)

        provider = get_provider("synthetic_stock")
        assert isinstance(provider, SyntheticStockAdapter)

    def test_get_provider_unknown(self):
        # Registry falls back to synthetic provider for unknown exchanges
        provider = get_provider("unknown")
        assert isinstance(provider, SyntheticAdapter)

    def test_list_providers(self):
        providers = list_providers()
        assert "gemini" in providers
        assert "kucoin" in providers
        assert "alpaca" in providers
        assert "polygon" in providers
        assert "synthetic" in providers
        assert "synthetic_stock" in providers


class TestTimezoneUtils:
    """Tests for timezone utilities."""

    def test_to_utc_series(self):
        # Integer timestamp (ms)
        ts = to_utc_series(pd.Series([1704067200000, 1704067260000]))
        assert ts.dt.tz is not None
        assert str(ts.dt.tz) == "UTC"
        assert ts.iloc[0].year == 2024

        # String ISO
        ts = to_utc_series(pd.Series(["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"]))
        assert ts.dt.tz is not None
        assert str(ts.dt.tz) == "UTC"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
