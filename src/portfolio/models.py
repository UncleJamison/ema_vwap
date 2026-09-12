"""
Data models and dataclasses for Unified Multi-Asset Portfolio Management.
Supports balance normalization, cross-asset positions, venue breakdown, and portfolio NAV metrics.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass
class Balance:
    """Represents a single currency/asset balance on an exchange or broker."""

    currency: str
    total: float
    free: float
    locked: float = 0.0
    usd_price: float = 1.0
    usd_value: float = 0.0
    venue: str = "unknown"
    asset_type: Literal["fiat", "stablecoin", "crypto", "stock"] = "crypto"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AssetPosition:
    """Represents an open asset holding (crypto or equity) across a specific venue."""

    symbol: str
    venue: str
    asset_type: Literal["crypto", "stock"]
    side: int  # 1 for Long, -1 for Short
    quantity: float
    entry_price: float
    current_price: float
    market_value_usd: float
    unrealized_pnl_usd: float = 0.0
    unrealized_pnl_pct: float = 0.0
    allocation_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VenueBalance:
    """Aggregated balances and positions for a single exchange or broker venue."""

    venue: str
    venue_type: Literal["crypto", "stock", "synthetic"]
    total_nav_usd: float = 0.0
    cash_usd: float = 0.0
    stablecoin_usd: float = 0.0
    crypto_usd: float = 0.0
    stock_usd: float = 0.0
    buying_power_usd: float = 0.0
    margin_used_usd: float = 0.0
    is_connected: bool = False
    is_sandbox: bool = False
    error_message: str | None = None
    balances: list[Balance] = field(default_factory=list)
    positions: list[AssetPosition] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UnifiedPortfolioSnapshot:
    """Consolidated real-time portfolio NAV, asset allocation, and venue breakdown."""

    timestamp: str
    total_nav_usd: float
    total_cash_usd: float
    total_stablecoin_usd: float
    total_crypto_usd: float
    total_stock_usd: float
    total_buying_power_usd: float
    total_unrealized_pnl_usd: float
    crypto_weight_pct: float
    stock_weight_pct: float
    cash_weight_pct: float
    venues: dict[str, VenueBalance] = field(default_factory=dict)
    positions: list[AssetPosition] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total_nav_usd": round(self.total_nav_usd, 2),
            "total_cash_usd": round(self.total_cash_usd, 2),
            "total_stablecoin_usd": round(self.total_stablecoin_usd, 2),
            "total_crypto_usd": round(self.total_crypto_usd, 2),
            "total_stock_usd": round(self.total_stock_usd, 2),
            "total_buying_power_usd": round(self.total_buying_power_usd, 2),
            "total_unrealized_pnl_usd": round(self.total_unrealized_pnl_usd, 2),
            "crypto_weight_pct": round(self.crypto_weight_pct, 2),
            "stock_weight_pct": round(self.stock_weight_pct, 2),
            "cash_weight_pct": round(self.cash_weight_pct, 2),
            "venues": {k: v.to_dict() for k, v in self.venues.items()},
            "positions": [p.to_dict() for p in self.positions],
        }
