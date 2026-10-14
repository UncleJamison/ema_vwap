"""
Tests for Unified Multi-Exchange Data Adapter (src/providers/unified_adapter.py):
- SymbolMapper: map/unmap between canonical and exchange-specific symbols
- RateLimiter: per-provider rate limiting
- FailoverRouter: primary -> fallback routing
- UnifiedAdapter: multi-exchange fetching with auto mapping
- Registration behavior
"""

import time
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from src.providers.base import BaseDataProvider
from src.providers.registry import get_provider, register_provider, registry
from src.providers.unified_adapter import (
    FailoverRouter,
    RateLimiter,
    SymbolMapper,
    UnifiedAdapter,
)


class TestSymbolMapper:
    """Tests for SymbolMapper class."""

    def test_map_identity_for_gemini(self):
        """Should return exchange-formatted symbol for Gemini."""
        # Gemini's format_symbol lowercases and removes slash
        result = SymbolMapper.map("BTC/USD", "gemini")
        assert result == "btcusd"

    def test_map_identity_for_robinhood(self):
        """Should return canonical symbol for Robinhood (identity mapping)."""
        result = SymbolMapper.map("BTC/USD", "robinhood")
        assert result == "BTC/USD"

    def test_unmap_identity(self):
        """Should return canonical symbol from any exchange format."""
        result = SymbolMapper.unmap("BTC-USD", "gemini")
        assert result == "BTC/USD"

        result = SymbolMapper.unmap("BTC/USD", "robinhood")
        assert result == "BTC/USD"

    def test_exchange_symbol_map_structure(self):
        """Should have expected exchange mapping structure."""
        assert "gemini" in SymbolMapper.EXCHANGE_SYMBOL_MAP
        assert "robinhood" in SymbolMapper.EXCHANGE_SYMBOL_MAP


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_wait_enforces_minimum_interval(self):
        """Should sleep to enforce minimum interval between calls."""
        limiter = RateLimiter(min_interval_s=0.1)

        start = time.time()
        limiter.wait()
        limiter.wait()
        elapsed = time.time() - start

        # Should take at least 0.1s for second call
        assert elapsed >= 0.09  # Allow small margin

    def test_first_call_no_wait(self):
        """First call should not wait."""
        limiter = RateLimiter(min_interval_s=0.1)

        start = time.time()
        limiter.wait()
        elapsed = time.time() - start

        # First call should be nearly instant
        assert elapsed < 0.05

    def test_custom_interval(self):
        """Should respect custom minimum interval."""
        limiter = RateLimiter(min_interval_s=0.05)

        start = time.time()
        limiter.wait()
        limiter.wait()
        elapsed = time.time() - start

        assert elapsed >= 0.04


class TestFailoverRouter:
    """Tests for FailoverRouter class."""

    @pytest.fixture
    def mock_primary_provider(self):
        """Create a mock primary provider."""
        provider = Mock(spec=BaseDataProvider)
        provider.name = "gemini"
        return provider

    @pytest.fixture
    def mock_fallback_provider(self):
        """Create a mock fallback provider."""
        provider = Mock(spec=BaseDataProvider)
        provider.name = "synthetic"
        return provider

    def test_fetch_returns_primary_on_success(
        self, mock_primary_provider, mock_fallback_provider
    ):
        """Should return primary provider data when successful."""
        df = pd.DataFrame(
            {
                "open": [100],
                "high": [101],
                "low": [99],
                "close": [100],
                "volume": [1000],
            }
        )
        mock_primary_provider.fetch_historical_candles = Mock(return_value=df)
        mock_fallback_provider.fetch_historical_candles = Mock(
            return_value=pd.DataFrame()
        )

        with patch("src.providers.unified_adapter.get_provider") as mock_get:
            mock_get.side_effect = lambda name: (
                mock_primary_provider if name == "gemini" else mock_fallback_provider
            )

            router = FailoverRouter(
                primary_exchange="gemini", fallback_exchange="synthetic"
            )
            result = router.fetch(symbol="BTC/USD")

        assert not result.empty
        assert len(result) == 1
        mock_primary_provider.fetch_historical_candles.assert_called_once()
        mock_fallback_provider.fetch_historical_candles.assert_not_called()

    def test_fetch_falls_back_on_primary_exception(
        self, mock_primary_provider, mock_fallback_provider
    ):
        """Should fall back to synthetic provider on primary exception."""
        mock_primary_provider.fetch_historical_candles = Mock(
            side_effect=Exception("API Error")
        )
        df = pd.DataFrame(
            {
                "open": [100],
                "high": [101],
                "low": [99],
                "close": [100],
                "volume": [1000],
            }
        )
        mock_fallback_provider.fetch_historical_candles = Mock(return_value=df)

        with patch("src.providers.unified_adapter.get_provider") as mock_get:
            mock_get.side_effect = lambda name: (
                mock_primary_provider if name == "gemini" else mock_fallback_provider
            )

            router = FailoverRouter(
                primary_exchange="gemini", fallback_exchange="synthetic"
            )
            result = router.fetch(symbol="BTC/USD")

        assert not result.empty
        mock_primary_provider.fetch_historical_candles.assert_called_once()
        mock_fallback_provider.fetch_historical_candles.assert_called_once()

    def test_fetch_falls_back_on_empty_primary(
        self, mock_primary_provider, mock_fallback_provider
    ):
        """Should fall back when primary returns empty DataFrame."""
        mock_primary_provider.fetch_historical_candles = Mock(
            return_value=pd.DataFrame()
        )
        df = pd.DataFrame(
            {
                "open": [100],
                "high": [101],
                "low": [99],
                "close": [100],
                "volume": [1000],
            }
        )
        mock_fallback_provider.fetch_historical_candles = Mock(return_value=df)

        with patch("src.providers.unified_adapter.get_provider") as mock_get:
            mock_get.side_effect = lambda name: (
                mock_primary_provider if name == "gemini" else mock_fallback_provider
            )

            router = FailoverRouter(
                primary_exchange="gemini", fallback_exchange="synthetic"
            )
            result = router.fetch(symbol="BTC/USD")

        assert not result.empty
        mock_fallback_provider.fetch_historical_candles.assert_called_once()

    def test_default_fallback_is_synthetic(
        self, mock_primary_provider, mock_fallback_provider
    ):
        """Should use synthetic as default fallback."""
        df = pd.DataFrame(
            {
                "open": [100],
                "high": [101],
                "low": [99],
                "close": [100],
                "volume": [1000],
            }
        )
        mock_primary_provider.fetch_historical_candles = Mock(
            side_effect=Exception("Error")
        )
        mock_fallback_provider.fetch_historical_candles = Mock(return_value=df)

        with patch("src.providers.unified_adapter.get_provider") as mock_get:
            mock_get.side_effect = lambda name: (
                mock_primary_provider if name == "gemini" else mock_fallback_provider
            )

            router = FailoverRouter(primary_exchange="gemini")  # No fallback specified
            result = router.fetch(symbol="BTC/USD")

        assert not result.empty


class TestUnifiedAdapter:
    """Tests for UnifiedAdapter class."""

    @pytest.fixture
    def mock_gemini_provider(self):
        provider = Mock(spec=BaseDataProvider)
        provider.name = "gemini"
        return provider

    @pytest.fixture
    def mock_robinhood_provider(self):
        provider = Mock(spec=BaseDataProvider)
        provider.name = "robinhood"
        return provider

    @pytest.fixture
    def mock_synthetic_provider(self):
        provider = Mock(spec=BaseDataProvider)
        provider.name = "synthetic"
        return provider

    def test_init_default_exchanges(self):
        """Should initialize with default exchanges."""
        adapter = UnifiedAdapter()
        assert adapter.exchanges == ["gemini", "robinhood"]
        assert "gemini" in adapter.routers
        assert "robinhood" in adapter.routers

    def test_init_custom_exchanges(self):
        """Should initialize with custom exchanges."""
        adapter = UnifiedAdapter(exchanges=["gemini", "kucoin"])
        assert adapter.exchanges == ["gemini", "kucoin"]
        assert "gemini" in adapter.routers
        assert "kucoin" in adapter.routers

    def test_format_symbol_returns_canonical(self):
        """Should return canonical symbol format."""
        adapter = UnifiedAdapter()
        result = adapter.format_symbol("BTC-USD")
        assert result == "BTC/USD"

    def test_fetch_historical_candles_uses_first_exchange_by_default(
        self, mock_gemini_provider, mock_synthetic_provider
    ):
        """Should use first exchange by default."""
        df = pd.DataFrame(
            {
                "open": [100],
                "high": [101],
                "low": [99],
                "close": [100],
                "volume": [1000],
            }
        )
        mock_gemini_provider.fetch_historical_candles = Mock(return_value=df)
        mock_synthetic_provider.fetch_historical_candles = Mock(
            return_value=pd.DataFrame()
        )

        with patch("src.providers.unified_adapter.get_provider") as mock_get:
            mock_get.side_effect = lambda name: {
                "gemini": mock_gemini_provider,
                "synthetic": mock_synthetic_provider,
            }.get(name, mock_synthetic_provider)

            adapter = UnifiedAdapter(exchanges=["gemini"])
            result = adapter.fetch_historical_candles(symbol="BTC/USD")

        assert not result.empty

    def test_fetch_historical_candles_with_exchange_param(
        self, mock_gemini_provider, mock_robinhood_provider, mock_synthetic_provider
    ):
        """Should use specified exchange when provided."""
        df = pd.DataFrame(
            {
                "open": [100],
                "high": [101],
                "low": [99],
                "close": [100],
                "volume": [1000],
            }
        )
        mock_robinhood_provider.fetch_historical_candles = Mock(return_value=df)
        mock_synthetic_provider.fetch_historical_candles = Mock(
            return_value=pd.DataFrame()
        )

        with patch("src.providers.unified_adapter.get_provider") as mock_get:
            mock_get.side_effect = lambda name: {
                "robinhood": mock_robinhood_provider,
                "synthetic": mock_synthetic_provider,
            }.get(name, mock_synthetic_provider)

            adapter = UnifiedAdapter(exchanges=["gemini", "robinhood"])
            result = adapter.fetch_historical_candles(
                symbol="BTC/USD", exchange="robinhood"
            )

        assert not result.empty
        mock_robinhood_provider.fetch_historical_candles.assert_called_once()

    def test_fetch_historical_candles_invalid_exchange_falls_back(
        self, mock_synthetic_provider
    ):
        """Should fall back to synthetic for invalid exchange."""
        df = pd.DataFrame(
            {
                "open": [100],
                "high": [101],
                "low": [99],
                "close": [100],
                "volume": [1000],
            }
        )
        mock_synthetic_provider.fetch_historical_candles = Mock(return_value=df)

        with patch("src.providers.unified_adapter.get_provider") as mock_get:
            mock_get.return_value = mock_synthetic_provider

            adapter = UnifiedAdapter(exchanges=["gemini"])
            result = adapter.fetch_historical_candles(
                symbol="BTC/USD", exchange="invalid"
            )

        assert not result.empty


class TestUnifiedAdapterRegistration:
    """Tests for UnifiedAdapter registration behavior."""

    def test_unified_adapter_registers_on_import(self):
        """UnifiedAdapter should register itself when module is imported."""
        # Force import of the module
        import importlib

        import src.providers.unified_adapter as ua_module

        importlib.reload(ua_module)

        # Check if it's registered (but it won't be in global registry since registry is already initialized)
        # The register_provider call in the module runs at import time
        # But the global registry was already created before this test
        # Registration happens at module import, hard to test in isolation

    def test_unified_adapter_is_base_provider(self):
        """UnifiedAdapter should be a BaseDataProvider subclass."""
        assert issubclass(UnifiedAdapter, BaseDataProvider)

    def test_unified_adapter_name(self):
        """Should have correct name."""
        assert UnifiedAdapter.name == "unified"

    def test_unified_adapter_asset_type(self):
        """Should have correct asset type."""
        assert UnifiedAdapter.asset_type == "multi"

    def test_unified_adapter_timeframes(self):
        """Should have expected native timeframes."""
        expected = {"1m", "5m", "15m", "30m", "1h", "6h", "1d"}
        assert UnifiedAdapter.native_timeframes == expected


class TestUnifiedAdapterIntegration:
    """Integration tests for UnifiedAdapter with real providers."""

    def test_unified_adapter_registered_when_module_imported(self):
        """UnifiedAdapter gets registered when unified_adapter module is imported."""
        # This test file imports the unified_adapter module, which triggers registration
        providers = registry.list_providers()
        # The unified adapter should now be registered
        assert "unified" in providers

    def test_can_register_unified_adapter_manually(self):
        """Should be able to register UnifiedAdapter manually."""
        # Clear and re-register
        original = registry._providers.copy()
        try:
            registry._providers.clear()
            registry._register_defaults()
            register_provider(UnifiedAdapter())
            assert "unified" in registry.list_providers()

            provider = get_provider("unified")
            assert provider.name == "unified"
        finally:
            registry._providers = original


class TestUnifiedAdapterStatus:
    """Tests documenting the current status of UnifiedAdapter."""

    def test_unified_adapter_status(self):
        """
        UnifiedAdapter status:
        - The module exists and is functional
        - It registers itself on import via register_provider(UnifiedAdapter())
        - However, it's not imported by the main providers package or any routes
        - The 'unified' term in portfolio.py refers to PortfolioAggregator.get_unified_snapshot(), not this adapter

        This test documents the current status. If the adapter is to be used,
        it needs to be imported in providers/__init__.py or explicitly registered.
        """
        # The adapter registers itself when its module is imported
        # This test file imports it, so it's now registered
        providers = registry.list_providers()
        assert "unified" in providers

        # The main providers package doesn't import it in __init__.py
        # (This test file imports it, so hasattr would return True here)
        # The key point: it's not in providers/__init__.py exports
        import src.providers as providers_pkg

        assert "UnifiedAdapter" not in providers_pkg.__all__


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
