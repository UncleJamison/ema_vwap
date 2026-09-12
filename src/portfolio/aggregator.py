"""
Unified Multi-Asset Portfolio Aggregator.
Consolidates real-time balances, positions, and purchasing power across crypto exchanges and stock brokers.
"""

import datetime as dt
from typing import Any

from src.portfolio.connectors import (
    AlpacaBalanceConnector,
    BaseVenueConnector,
    GeminiBalanceConnector,
    KuCoinBalanceConnector,
    PaperTradingBalanceConnector,
    RobinhoodCryptoBalanceConnector,
    SyntheticBalanceConnector,
)
from src.portfolio.models import (
    AssetPosition,
    UnifiedPortfolioSnapshot,
    VenueBalance,
)
from src.settings import SettingsManager


class PortfolioAggregator:
    """Aggregates and normalizes multi-venue assets into a unified portfolio snapshot."""

    def __init__(self, settings: SettingsManager | None = None):
        self.settings = settings or SettingsManager()
        self.connectors: dict[str, BaseVenueConnector] = {
            "gemini": GeminiBalanceConnector(self.settings),
            "kucoin": KuCoinBalanceConnector(self.settings),
            "alpaca": AlpacaBalanceConnector(self.settings),
            "robinhood": RobinhoodCryptoBalanceConnector(self.settings),
            "paper": PaperTradingBalanceConnector(self.settings),
            "synthetic": SyntheticBalanceConnector(self.settings),
        }

    def register_connector(self, name: str, connector: BaseVenueConnector) -> None:
        """Register a custom venue connector."""
        self.connectors[name.lower()] = connector

    def get_unified_snapshot(
        self, include_synthetic: bool = False
    ) -> UnifiedPortfolioSnapshot:
        """
        Polls all registered exchange/broker connectors and aggregates balances into a single NAV ledger.
        If no live API credentials are configured or all fail, includes synthetic portfolio to provide
        instant demonstrability.
        """
        venues: dict[str, VenueBalance] = {}
        all_positions: list[AssetPosition] = []

        total_nav = 0.0
        total_cash = 0.0
        total_stablecoin = 0.0
        total_crypto = 0.0
        total_stock = 0.0
        total_buying_power = 0.0
        total_unrealized_pnl = 0.0

        has_any_live_connection = False

        for name, connector in self.connectors.items():
            if name == "synthetic" and not include_synthetic:
                continue

            venue_bal = connector.fetch_balances()
            venues[name] = venue_bal

            if venue_bal.is_connected and name != "synthetic":
                has_any_live_connection = True

            total_nav += venue_bal.total_nav_usd
            total_cash += venue_bal.cash_usd
            total_stablecoin += venue_bal.stablecoin_usd
            total_crypto += venue_bal.crypto_usd
            total_stock += venue_bal.stock_usd
            total_buying_power += venue_bal.buying_power_usd

            for p in venue_bal.positions:
                total_unrealized_pnl += p.unrealized_pnl_usd
                all_positions.append(p)

        # If no live venues are configured or connected and synthetic was not requested,
        # fallback to synthetic paper wallet so UI and portfolio analysis remain fully operational
        if not has_any_live_connection and not include_synthetic:
            synth_bal = self.connectors["synthetic"].fetch_balances()
            venues["synthetic"] = synth_bal
            total_nav = synth_bal.total_nav_usd
            total_cash = synth_bal.cash_usd
            total_stablecoin = synth_bal.stablecoin_usd
            total_crypto = synth_bal.crypto_usd
            total_stock = synth_bal.stock_usd
            total_buying_power = synth_bal.buying_power_usd
            total_unrealized_pnl = 0.0
            all_positions = []
            for p in synth_bal.positions:
                total_unrealized_pnl += p.unrealized_pnl_usd
                all_positions.append(p)

        # Compute asset allocation percentages
        denom = total_nav if total_nav > 0 else 1.0
        crypto_pct = (total_crypto / denom) * 100.0
        stock_pct = (total_stock / denom) * 100.0
        cash_pct = ((total_cash + total_stablecoin) / denom) * 100.0

        # Compute position weights
        for p in all_positions:
            p.allocation_pct = round((p.market_value_usd / denom) * 100.0, 2)

        now_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")

        return UnifiedPortfolioSnapshot(
            timestamp=now_str,
            total_nav_usd=total_nav,
            total_cash_usd=total_cash,
            total_stablecoin_usd=total_stablecoin,
            total_crypto_usd=total_crypto,
            total_stock_usd=total_stock,
            total_buying_power_usd=total_buying_power,
            total_unrealized_pnl_usd=total_unrealized_pnl,
            crypto_weight_pct=crypto_pct,
            stock_weight_pct=stock_pct,
            cash_weight_pct=cash_pct,
            venues=venues,
            positions=all_positions,
        )

    def get_historical_nav(
        self, days: int = 30, include_synthetic: bool = True
    ) -> list[dict[str, Any]]:
        """
        Reconstructs daily portfolio NAV trajectory based on current asset holdings and historical daily candle closes.
        """
        import pandas as pd

        from src.data_loader import DataLoader

        snapshot = self.get_unified_snapshot(include_synthetic=include_synthetic)
        total_cash = snapshot.total_cash_usd + snapshot.total_stablecoin_usd

        positions = snapshot.positions
        now = dt.datetime.now(dt.timezone.utc)

        if not positions:
            return [
                {
                    "timestamp": (now - dt.timedelta(days=d)).strftime("%Y-%m-%d"),
                    "total_nav": round(total_cash, 2),
                    "crypto_value": 0.0,
                    "stock_value": 0.0,
                    "cash_value": round(total_cash, 2),
                }
                for d in range(days, -1, -1)
            ]

        candles_by_pos: dict[str, pd.DataFrame] = {}
        for p in positions:
            exchange = "synthetic_stock" if p.asset_type == "stock" else "synthetic"
            try:
                df = DataLoader.load_candles(
                    exchange=exchange,
                    symbol=p.symbol,
                    timeframe="1d",
                    limit=days + 15,
                )
                if not df.empty:
                    df["timestamp_dt"] = pd.to_datetime(df["timestamp"], utc=True)
                    df = df.sort_values("timestamp_dt")
                    candles_by_pos[p.symbol] = df
            except Exception:
                pass

        history: list[dict[str, Any]] = []

        for d in range(days, -1, -1):
            target_date = (now - dt.timedelta(days=d)).date()
            date_str = target_date.strftime("%Y-%m-%d")

            crypto_val = 0.0
            stock_val = 0.0

            for p in positions:
                df = candles_by_pos.get(p.symbol)
                price = p.current_price
                if df is not None and not df.empty:
                    sub = df[df["timestamp_dt"].dt.date <= target_date]
                    if not sub.empty:
                        price = float(sub.iloc[-1]["close"])
                    else:
                        price = float(df.iloc[0]["close"])

                asset_val = p.quantity * price
                if p.asset_type == "crypto":
                    crypto_val += asset_val
                else:
                    stock_val += asset_val

            tot_nav = total_cash + crypto_val + stock_val
            history.append(
                {
                    "timestamp": date_str,
                    "total_nav": round(tot_nav, 2),
                    "crypto_value": round(crypto_val, 2),
                    "stock_value": round(stock_val, 2),
                    "cash_value": round(total_cash, 2),
                }
            )

        return history


# Global singleton instance
portfolio_aggregator = PortfolioAggregator()
