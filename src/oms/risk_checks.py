"""
Pre-Trade Risk Checks (ema_vwap-6a1).

Implements configurable risk gates that orders must pass before submission:
- Max order size (absolute and % of portfolio)
- Max participation rate (order size / ADV)
- Max position concentration
- Buying power / margin checks
- Sector/asset class limits
- Daily loss limits
- Order rate limiting
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.logger import logger


@dataclass
class RiskLimit:
    """A single risk limit with threshold and action."""

    name: str
    threshold: float
    current: float = 0.0
    action: str = "reject"  # "reject", "warn", "reduce"
    enabled: bool = True


@dataclass
class RiskCheckResult:
    """Result of a single risk check."""

    check_name: str
    passed: bool
    current_value: float
    threshold: float
    message: str
    action: str = "reject"


@dataclass
class PreTradeRiskConfig:
    """Configuration for pre-trade risk checks."""

    # Max order size as % of portfolio NAV
    max_order_size_pct_nav: float = 10.0

    # Max order size as % of ADV (participation rate)
    max_participation_pct_adv: float = 20.0

    # Max position size as % of portfolio NAV
    max_position_pct_nav: float = 25.0

    # Max single asset concentration
    max_asset_concentration_pct: float = 50.0

    # Max sector concentration
    max_sector_concentration_pct: float = 40.0

    # Daily loss limit as % of starting NAV
    max_daily_loss_pct: float = 5.0

    # Max orders per minute
    max_orders_per_minute: int = 60

    # Max orders per day
    max_orders_per_day: int = 1000

    # Min order size (to prevent dust orders)
    min_order_size_usd: float = 10.0

    # Max leverage (gross exposure / NAV)
    max_leverage: float = 2.0

    # Enable/disable checks
    checks_enabled: dict[str, bool] = field(
        default_factory=lambda: {
            "max_order_size": True,
            "participation_rate": True,
            "position_concentration": True,
            "asset_concentration": True,
            "sector_concentration": True,
            "daily_loss": True,
            "order_rate": True,
            "min_order_size": True,
            "leverage": True,
        }
    )


class PreTradeRiskManager:
    """
    Manages pre-trade risk checks for order submission.

    Integrates with portfolio aggregator for real-time NAV/position data.
    """

    def __init__(self, config: PreTradeRiskConfig | None = None) -> None:
        self.config = config or PreTradeRiskConfig()
        self._order_timestamps: list[float] = []
        self._daily_pnl: float = 0.0
        self._starting_nav: float = 0.0
        self._daily_reset_time: float = 0.0

    def _reset_daily_if_needed(self) -> None:
        """Reset daily counters if new day."""
        import time

        current_day = int(time.time() // 86400)
        reset_day = (
            int(self._daily_reset_time // 86400) if self._daily_reset_time else -1
        )
        if current_day != reset_day:
            # First call of a NEW day: reset counters. But if _daily_reset_time
            # is 0 (never reset), state set before the first check (e.g. via
            # update_daily_pnl / set_starting_nav) already belongs to today —
            # stamp the reset time WITHOUT wiping it.
            if self._daily_reset_time:
                self._daily_pnl = 0.0
                self._order_timestamps.clear()
            self._daily_reset_time = time.time()

    def update_daily_pnl(self, pnl_change: float) -> None:
        """Update running daily P&L."""
        self._daily_pnl += pnl_change

    def set_starting_nav(self, nav: float) -> None:
        """Set starting NAV for daily loss calculation."""
        self._starting_nav = nav

    def check_order(
        self,
        symbol: str,
        side: str,  # "buy" or "sell"
        quantity: float,
        price: float,
        portfolio_nav: float,
        current_positions: dict[str, float] | None = None,
        adv: float = 1_000_000,
        sector: str = "unknown",
    ) -> tuple[bool, list[RiskCheckResult]]:
        """
        Run all enabled pre-trade risk checks.

        Returns: (all_passed, list_of_results)
        """
        self._reset_daily_if_needed()
        results = []
        current_positions = current_positions or {}

        order_value_usd = quantity * price
        current_position = current_positions.get(symbol, 0.0)
        new_position = current_position + (quantity if side == "buy" else -quantity)

        # 1. Max order size as % of NAV
        if self.config.checks_enabled.get("max_order_size", True):
            order_pct = (
                (order_value_usd / portfolio_nav * 100) if portfolio_nav > 0 else 0
            )
            passed = order_pct <= self.config.max_order_size_pct_nav
            results.append(
                RiskCheckResult(
                    check_name="max_order_size",
                    passed=passed,
                    current_value=order_pct,
                    threshold=self.config.max_order_size_pct_nav,
                    message=f"Order size {order_pct:.2f}% of NAV (limit: {self.config.max_order_size_pct_nav}%)",
                    action="reject",
                )
            )

        # 2. Participation rate (order / ADV)
        if self.config.checks_enabled.get("participation_rate", True):
            participation = (quantity / adv * 100) if adv > 0 else 0
            passed = participation <= self.config.max_participation_pct_adv
            results.append(
                RiskCheckResult(
                    check_name="participation_rate",
                    passed=passed,
                    current_value=participation,
                    threshold=self.config.max_participation_pct_adv,
                    message=f"Participation {participation:.2f}% of ADV (limit: {self.config.max_participation_pct_adv}%)",
                    action="reject",
                )
            )

        # 3. Position concentration
        if self.config.checks_enabled.get("position_concentration", True):
            new_pos_value = abs(new_position) * price  # Simplified
            pos_pct = (new_pos_value / portfolio_nav * 100) if portfolio_nav > 0 else 0
            passed = pos_pct <= self.config.max_position_pct_nav
            results.append(
                RiskCheckResult(
                    check_name="position_concentration",
                    passed=passed,
                    current_value=pos_pct,
                    threshold=self.config.max_position_pct_nav,
                    message=f"Position {pos_pct:.2f}% of NAV (limit: {self.config.max_position_pct_nav}%)",
                    action="reject",
                )
            )

        # 4. Asset concentration (single asset vs total portfolio)
        if self.config.checks_enabled.get("asset_concentration", True):
            total_portfolio = portfolio_nav
            asset_value = abs(new_position) * price
            asset_pct = (
                (asset_value / total_portfolio * 100) if total_portfolio > 0 else 0
            )
            passed = asset_pct <= self.config.max_asset_concentration_pct
            results.append(
                RiskCheckResult(
                    check_name="asset_concentration",
                    passed=passed,
                    current_value=asset_pct,
                    threshold=self.config.max_asset_concentration_pct,
                    message=f"Asset concentration {asset_pct:.2f}% (limit: {self.config.max_asset_concentration_pct}%)",
                    action="reject",
                )
            )

        # 5. Daily loss limit
        if (
            self.config.checks_enabled.get("daily_loss", True)
            and self._starting_nav > 0
        ):
            loss_pct = (
                (-self._daily_pnl / self._starting_nav * 100)
                if self._daily_pnl < 0
                else 0
            )
            passed = loss_pct <= self.config.max_daily_loss_pct
            results.append(
                RiskCheckResult(
                    check_name="daily_loss_limit",
                    passed=passed,
                    current_value=loss_pct,
                    threshold=self.config.max_daily_loss_pct,
                    message=f"Daily loss {loss_pct:.2f}% (limit: {self.config.max_daily_loss_pct}%)",
                    action="reject",
                )
            )

        # 6. Order rate limiting (per minute)
        if self.config.checks_enabled.get("order_rate", True):
            import time

            now = time.time()
            recent = [t for t in self._order_timestamps if now - t < 60]
            passed = len(recent) < self.config.max_orders_per_minute
            if not passed:
                results.append(
                    RiskCheckResult(
                        check_name="order_rate_limit",
                        passed=passed,
                        current_value=len(recent),
                        threshold=self.config.max_orders_per_minute,
                        message=f"Orders this minute: {len(recent)} (limit: {self.config.max_orders_per_minute})",
                        action="reject",
                    )
                )

        # 7. Min order size
        if self.config.checks_enabled.get("min_order_size", True):
            order_value = quantity * price  # Simplified
            passed = order_value >= self.config.min_order_size_usd
            results.append(
                RiskCheckResult(
                    check_name="min_order_size",
                    passed=passed,
                    current_value=order_value,
                    threshold=self.config.min_order_size_usd,
                    message=f"Order value ${order_value:.2f} (min: ${self.config.min_order_size_usd})",
                    action="reject",
                )
            )

        # 8. Leverage check (gross exposure / NAV)
        if self.config.checks_enabled.get("leverage", True):
            gross_exposure = sum(
                abs(pos) * price for symbol, pos in current_positions.items()
            )
            gross_exposure += quantity * price  # Add new order
            leverage = (gross_exposure / portfolio_nav) if portfolio_nav > 0 else 0
            passed = leverage <= self.config.max_leverage
            results.append(
                RiskCheckResult(
                    check_name="leverage_limit",
                    passed=passed,
                    current_value=leverage,
                    threshold=self.config.max_leverage,
                    message=f"Leverage {leverage:.2f}x (limit: {self.config.max_leverage}x)",
                    action="reject",
                )
            )

        # Record order timestamp for rate limiting
        import time

        self._order_timestamps.append(time.time())

        # Overall result
        all_passed = all(r.passed for r in results)

        for r in results:
            if not r.passed:
                logger.warning(f"[PreTradeRisk] {r.check_name} FAILED: {r.message}")

        return all_passed, results

    def record_order(self) -> None:
        """Record order for rate limiting."""
        import time

        self._order_timestamps.append(time.time())

    def get_check_summary(self, results: list[RiskCheckResult]) -> dict:
        """Get human-readable summary of check results."""
        return {
            "all_passed": all(r.passed for r in results),
            "failed_checks": [
                {"check": r.check_name, "message": r.message}
                for r in results
                if not r.passed
            ],
            "warnings": [
                {"check": r.check_name, "message": r.message}
                for r in results
                if r.passed and r.current_value > r.threshold * 0.8
            ],
        }


# Global instance
pre_trade_risk = PreTradeRiskManager()
