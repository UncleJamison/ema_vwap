"""
Automated Portfolio Rebalancing Engine.
Translates target allocation weights into discrete multi-venue rebalancing orders.
"""

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any

from src.portfolio.aggregator import portfolio_aggregator
from src.portfolio.models import UnifiedPortfolioSnapshot
from src.portfolio.order_router import VenueOrder, order_router


@dataclass
class RebalanceItem:
    """Represents a planned position rebalancing delta."""

    symbol: str
    venue: str
    current_weight_pct: float
    target_weight_pct: float
    drift_pct: float
    current_value_usd: float
    target_value_usd: float
    trade_value_usd: float  # positive for BUY, negative for SELL
    trade_side: int  # 1 for BUY, -1 for SELL, 0 for HOLD
    estimated_units: float
    estimated_price: float
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RebalancePlan:
    """Full portfolio rebalancing plan containing target deltas and execution statistics."""

    timestamp: str
    total_nav_usd: float
    drift_threshold_pct: float
    requires_rebalancing: bool
    total_turnover_usd: float
    estimated_fees_usd: float
    items: list[RebalanceItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total_nav_usd": round(self.total_nav_usd, 2),
            "drift_threshold_pct": self.drift_threshold_pct,
            "requires_rebalancing": self.requires_rebalancing,
            "total_turnover_usd": round(self.total_turnover_usd, 2),
            "estimated_fees_usd": round(self.estimated_fees_usd, 2),
            "items": [item.to_dict() for item in self.items],
        }


class PortfolioRebalancingEngine:
    """Calculates and executes multi-asset portfolio rebalancing."""

    @staticmethod
    def create_rebalance_plan(
        target_weights: dict[str, float],
        snapshot: UnifiedPortfolioSnapshot | None = None,
        drift_threshold_pct: float = 2.0,
        min_trade_usd: float = 25.0,
    ) -> RebalancePlan:
        """
        Calculates rebalancing orders needed to bring current holdings to target weights.
        target_weights: e.g. {"BTC/USD": 0.30, "ETH/USD": 0.20, "AAPL": 0.25, "SPY": 0.25} (sum <= 1.0)
        """
        if snapshot is None:
            snapshot = portfolio_aggregator.get_unified_snapshot()

        total_nav = max(1000.0, snapshot.total_nav_usd)
        now_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")

        # Map current holdings
        current_holdings: dict[str, dict[str, Any]] = {}
        for p in snapshot.positions:
            sym = p.symbol.upper()
            current_holdings[sym] = {
                "venue": p.venue,
                "value_usd": p.market_value_usd,
                "current_price": p.current_price,
            }

        # Normalize target weights
        total_target_w = sum(target_weights.values())
        norm_targets: dict[str, float] = {}
        for s, w in target_weights.items():
            norm_targets[s.upper()] = (
                (w / total_target_w) if total_target_w > 1.0 else w
            )

        # Combine all symbols
        all_symbols = sorted(
            set(list(current_holdings.keys()) + list(norm_targets.keys()))
        )

        items: list[RebalanceItem] = []
        total_turnover = 0.0
        requires_rebalancing = False

        for sym in all_symbols:
            curr_val = current_holdings.get(sym, {}).get("value_usd", 0.0)
            curr_weight = (curr_val / total_nav) * 100.0
            target_weight = norm_targets.get(sym, 0.0) * 100.0
            drift = target_weight - curr_weight

            target_val = (target_weight / 100.0) * total_nav
            trade_val = target_val - curr_val

            # Price estimation
            curr_price = current_holdings.get(sym, {}).get("current_price", 100.0)
            if curr_price <= 0:
                if "BTC" in sym:
                    curr_price = 65000.0
                elif "ETH" in sym:
                    curr_price = 3500.0
                elif "AAPL" in sym:
                    curr_price = 225.0
                elif "SPY" in sym:
                    curr_price = 550.0
                else:
                    curr_price = 100.0

            venue = current_holdings.get(sym, {}).get("venue", "auto")

            if abs(drift) >= drift_threshold_pct and abs(trade_val) >= min_trade_usd:
                requires_rebalancing = True
                side = 1 if trade_val > 0 else -1
                est_units = abs(trade_val) / curr_price
                total_turnover += abs(trade_val)
            else:
                side = 0
                est_units = 0.0

            items.append(
                RebalanceItem(
                    symbol=sym,
                    venue=venue,
                    current_weight_pct=round(curr_weight, 2),
                    target_weight_pct=round(target_weight, 2),
                    drift_pct=round(drift, 2),
                    current_value_usd=round(curr_val, 2),
                    target_value_usd=round(target_val, 2),
                    trade_value_usd=round(trade_val, 2),
                    trade_side=side,
                    estimated_units=round(est_units, 6),
                    estimated_price=round(curr_price, 2),
                )
            )

        est_fees = total_turnover * 0.00075  # ~7.5 bps average exchange taker fee

        return RebalancePlan(
            timestamp=now_str,
            total_nav_usd=total_nav,
            drift_threshold_pct=drift_threshold_pct,
            requires_rebalancing=requires_rebalancing,
            total_turnover_usd=total_turnover,
            estimated_fees_usd=est_fees,
            items=items,
        )

    @staticmethod
    def execute_plan(plan: RebalancePlan, dry_run: bool = True) -> list[VenueOrder]:
        """
        Executes discrete rebalancing orders via OrderRouter.
        Sells are executed first to release buying power, followed by buys.
        """
        executed_orders: list[VenueOrder] = []

        # Sort items: SELLs (side == -1) first, then BUYs (side == 1)
        sell_items = [
            i for i in plan.items if i.trade_side == -1 and i.estimated_units > 0
        ]
        buy_items = [
            i for i in plan.items if i.trade_side == 1 and i.estimated_units > 0
        ]

        for item in sell_items + buy_items:
            order = order_router.route_order(
                symbol=item.symbol,
                side=item.trade_side,
                quantity=item.estimated_units,
                venue=item.venue,
                order_type="market",
                limit_price=item.estimated_price,
                dry_run=dry_run,
            )
            item.status = order.status
            executed_orders.append(order)

        return executed_orders
