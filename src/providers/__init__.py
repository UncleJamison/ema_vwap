"""
Data Providers Package for Cryptocurrency, Stock, and Synthetic Market Data Ingestion.
"""

from src.providers.alpaca import AlpacaStockAdapter
from src.providers.base import (
    STANDARD_TF_ORDER,
    BaseDataProvider,
    normalize_symbol,
)
from src.providers.crypto import (
    GeminiAdapter,
    KuCoinAdapter,
)
from src.providers.polygon import PolygonStockAdapter
from src.providers.registry import (
    ProviderRegistry,
    get_provider,
    register_provider,
    registry,
)
from src.providers.stock_synthetic import SyntheticStockAdapter
from src.providers.synthetic import (
    SyntheticAdapter,
)
from src.providers.timezone import (
    filter_market_hours,
    get_nyse_holidays,
    get_session_anchor_utc,
    get_session_id,
    is_market_hours,
    is_nyse_holiday,
    to_utc_series,
    utc_to_market_tz,
)

__all__ = [
    "STANDARD_TF_ORDER",
    "AlpacaStockAdapter",
    "BaseDataProvider",
    "GeminiAdapter",
    "KuCoinAdapter",
    "PolygonStockAdapter",
    "ProviderRegistry",
    "SyntheticAdapter",
    "SyntheticStockAdapter",
    "filter_market_hours",
    "get_nyse_holidays",
    "get_provider",
    "get_session_anchor_utc",
    "get_session_id",
    "is_market_hours",
    "is_nyse_holiday",
    "normalize_symbol",
    "register_provider",
    "registry",
    "to_utc_series",
    "utc_to_market_tz",
]
