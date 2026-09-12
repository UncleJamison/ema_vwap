"""
Dynamic Provider Registry for Exchange and Market Data Providers.
"""

from src.providers.alpaca import AlpacaStockAdapter
from src.providers.base import BaseDataProvider
from src.providers.crypto import GeminiAdapter, KuCoinAdapter
from src.providers.polygon import PolygonStockAdapter
from src.providers.stock_synthetic import SyntheticStockAdapter
from src.providers.synthetic import SyntheticAdapter


class ProviderRegistry:
    """Registry holding instantiated BaseDataProvider adapters."""

    def __init__(self) -> None:
        self._providers: dict[str, BaseDataProvider] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register built-in default providers."""
        self.register(GeminiAdapter())
        self.register(KuCoinAdapter())
        self.register(SyntheticAdapter())
        self.register(AlpacaStockAdapter())
        self.register(PolygonStockAdapter())
        self.register(SyntheticStockAdapter())

    def register(self, provider: BaseDataProvider | type[BaseDataProvider]) -> None:
        """Register a provider instance or class."""
        if isinstance(provider, type):
            instance = provider()
        else:
            instance = provider
        key = instance.name.strip().lower()
        self._providers[key] = instance

    def get(self, exchange_or_name: str) -> BaseDataProvider:
        """
        Get provider by exchange name (case-insensitive).
        Falls back to SyntheticAdapter if not found.
        """
        key = (exchange_or_name or "synthetic").strip().lower()
        if key in self._providers:
            return self._providers[key]
        # Check if key is an alias or unsupported exchange
        print(
            f"[ProviderRegistry] Provider '{exchange_or_name}' not found. Falling back to synthetic provider."
        )
        return self._providers.get("synthetic", SyntheticAdapter())

    def list_providers(self) -> list[str]:
        """List all registered provider names."""
        return sorted(self._providers.keys())

    def get_supported_timeframes(self, exchange: str) -> set:
        """Get native timeframes supported by provider."""
        provider = self.get(exchange)
        return provider.get_supported_timeframes()


# Global default registry instance
registry = ProviderRegistry()


def get_provider(exchange: str) -> BaseDataProvider:
    """Convenience function to get a provider from the global registry."""
    return registry.get(exchange)


def register_provider(provider: BaseDataProvider | type[BaseDataProvider]) -> None:
    """Convenience function to register a provider into the global registry."""
    registry.register(provider)
