"""
Risk Management and Position Sizing Engine.
Calculates dynamic position sizes based on equity risk %, stop losses (VWAP-based or ATR-based), and Take Profit targets.
"""

from src.config import StrategyParams

# Regime-conditioned multipliers for position sizing / stop management
# (trending = full risk; ranging = tighter; high_vol = reduced + wider stops)
_REGIME_RISK_MULTIPLIER = {"trending": 1.0, "ranging": 0.8, "high_vol": 0.7}
_REGIME_STOP_MULTIPLIER = {"trending": 1.0, "ranging": 1.15, "high_vol": 1.4}


class RiskManager:
    """Calculates risk parameters, stop-loss levels, take-profit targets, and position sizing."""

    def __init__(self, params: StrategyParams):
        self.params = params

    def calculate_position_size(
        self,
        equity: float,
        entry_price: float,
        stop_loss_price: float,
        regime: str = "trending",
    ) -> tuple[float, float, float]:
        """
        Calculate position size in units and USD value based on equity risk %.
        Applies regime risk multiplier (high_vol reduces size).
        Returns (position_units, position_value_usd, risk_amount_usd).
        """
        multiplier = _REGIME_RISK_MULTIPLIER.get(regime, 1.0)
        effective_equity = equity * multiplier
        if effective_equity <= 0 or entry_price <= 0:
            return 0.0, 0.0, 0.0

        risk_amount_usd = effective_equity * (self.params.risk_per_trade_pct / 100.0)
        stop_distance = abs(entry_price - stop_loss_price)

        if stop_distance <= 0:
            return 0.0, 0.0, 0.0

        units = risk_amount_usd / stop_distance

        # Whole-share rounding for equities when fractional shares are disabled
        if not getattr(self.params, "allow_fractional_shares", True):
            import math

            units = float(math.floor(units))

        position_value_usd = units * entry_price

        return units, position_value_usd, risk_amount_usd

    def calculate_stop_and_target(
        self, entry_price: float, side: int, vwap: float, atr: float
    ) -> tuple[float, float]:
        """
        Calculate Stop Loss and Take Profit levels.
        side: 1 for Long, -1 for Short.
        Returns (stop_loss_price, take_profit_price).
        """
        if atr <= 0:
            atr = entry_price * 0.01  # Fallback to 1% ATR if zero

        if self.params.stop_loss_type == "vwap":
            offset = vwap * (self.params.vwap_stop_offset_pct / 100.0)
            if side == 1:  # Long
                stop_loss = vwap - offset
                # Ensure stop loss is below entry price
                if stop_loss >= entry_price:
                    stop_loss = entry_price - (2.0 * atr)
                risk_dist = entry_price - stop_loss
                take_profit = entry_price + (risk_dist * self.params.risk_reward_ratio)
            else:  # Short
                stop_loss = vwap + offset
                # Ensure stop loss is above entry price
                if stop_loss <= entry_price:
                    stop_loss = entry_price + (2.0 * atr)
                risk_dist = stop_loss - entry_price
                take_profit = entry_price - (risk_dist * self.params.risk_reward_ratio)
        else:  # ATR-based trailing/fixed stop
            risk_dist = atr * self.params.atr_multiplier
            if side == 1:  # Long
                stop_loss = entry_price - risk_dist
                take_profit = entry_price + (risk_dist * self.params.risk_reward_ratio)
            else:  # Short
                stop_loss = entry_price + risk_dist
                take_profit = entry_price - (risk_dist * self.params.risk_reward_ratio)

        return max(stop_loss, 1e-9), max(take_profit, 1e-9)

    def update_trailing_stop(
        self,
        side: int,
        current_price: float,
        current_stop: float,
        atr: float,
        regime: str = "trending",
        entry_price: float | None = None,
    ) -> float:
        """Update trailing stop loss if current price moves favorably.

        Uses ATR as base distance; trailing_stop_pct acts as a MAXIMUM distance cap.
        If entry_price is provided, can set initial trail from entry (Fix #18).
        """
        if atr <= 0:
            return current_stop

        # Base distance: ATR * multiplier * regime multiplier
        stop_mult = _REGIME_STOP_MULTIPLIER.get(regime, 1.0)
        atr_distance = atr * self.params.atr_multiplier * stop_mult

        # If trailing_stop_pct configured, use it as MAXIMUM distance (cap)
        if (
            hasattr(self.params, "trailing_stop_pct")
            and self.params.trailing_stop_pct > 0
        ):
            pct_distance = current_price * (self.params.trailing_stop_pct / 100.0)
            distance = min(atr_distance, pct_distance)  # ATR base, capped by %
        else:
            distance = atr_distance

        # Fix #18: If entry_price provided and no current stop (or very loose),
        # set initial trail from entry (allows trailing from entry, not just in profit)
        if entry_price is not None and current_stop <= 1e-9:
            # Set initial stop at entry minus distance (Long) or entry plus distance (Short)
            if side == 1:  # Long
                return max(current_stop, entry_price - distance)
            else:  # Short
                return min(current_stop, entry_price + distance)

        # Standard trailing logic: only ratchet in favorable direction
        if side == 1:  # Long
            new_stop = current_price - distance
            return max(current_stop, new_stop)  # ratchet upward only
        else:  # Short
            new_stop = current_price + distance
            return min(current_stop, new_stop)
