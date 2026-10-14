"""
Options Strategy Definitions (ema_vwap-m9v).

Provides standard options strategy implementations:
- Straddle, Strangle
- Iron Condor
- Vertical Spreads (Bull/Bear Call/Put)
- Butterfly
- Calendar Spread
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from src.options.greeks import Greeks, OptionType, black_scholes_greeks


class StrategyType(Enum):
    STRADDLE = "straddle"
    STRANGLE = "strangle"
    IRON_CONDOR = "iron_condor"
    BULL_CALL_SPREAD = "bull_call_spread"
    BEAR_CALL_SPREAD = "bear_call_spread"
    BULL_PUT_SPREAD = "bull_put_spread"
    BEAR_PUT_SPREAD = "bear_put_spread"
    BUTTERFLY = "butterfly"
    CALENDAR_SPREAD = "calendar_spread"


@dataclass
class OptionLeg:
    """Single option leg in a multi-leg strategy."""

    option_type: OptionType
    strike: float
    quantity: int  # Positive = long, negative = short
    expiry: float  # Years to maturity
    greeks: Greeks | None = None

    def price(self, S: float, r: float, sigma: float, q: float = 0.0) -> float:
        """Calculate leg price."""
        if self.greeks:
            return self.greeks.price * abs(self.quantity)
        g = black_scholes_greeks(
            S=S,
            K=self.strike,
            T=self.expiry,
            r=r,
            sigma=sigma,
            option_type=self.option_type,
        )
        return g.price * abs(self.quantity)

    def leg_greeks(self, S: float, r: float, sigma: float, q: float = 0.0) -> Greeks:
        """Calculate leg Greeks scaled by quantity."""
        if self.greeks:
            return Greeks(
                delta=self.greeks.delta * self.quantity,
                gamma=self.greeks.gamma * abs(self.quantity),
                vega=self.greeks.vega * abs(self.quantity),
                theta=self.greeks.theta * abs(self.quantity),
                rho=self.greeks.rho * abs(self.quantity),
                price=self.greeks.price * abs(self.quantity),
            )
        g = black_scholes_greeks(
            S=S,
            K=self.strike,
            T=self.expiry,
            r=r,
            sigma=sigma,
            option_type=self.option_type,
        )
        return Greeks(
            delta=g.delta * self.quantity,
            gamma=g.gamma * abs(self.quantity),
            vega=g.vega * abs(self.quantity),
            theta=g.theta * abs(self.quantity),
            rho=g.rho * abs(self.quantity),
            price=g.price * abs(self.quantity),
        )


class OptionStrategy(ABC):
    """Abstract base class for options strategies."""

    def __init__(
        self,
        symbol: str,
        spot: float,
        r: float = 0.05,
        q: float = 0.0,
        legs: list[OptionLeg] | None = None,
    ):
        self.symbol = symbol
        self.spot = spot
        self.r = r
        self.q = q
        self.legs = legs or []

    @abstractmethod
    def build_legs(self, **params) -> list[OptionLeg]:
        """Build strategy legs from parameters."""

    def evaluate(
        self,
        S: float,
        sigma: float,
        T: float,
        r: float | None = None,
        q: float | None = None,
    ) -> dict:
        """Evaluate strategy at given spot, vol, and time."""
        r = r if r is not None else self.r
        q = q if q is not None else self.q

        total_price = 0.0
        total_delta = 0.0
        total_gamma = 0.0
        total_vega = 0.0
        total_theta = 0.0
        total_rho = 0.0

        for leg in self.legs:
            g = leg.leg_greeks(S, r, sigma, q)
            total_price += g.price
            total_delta += g.delta
            total_gamma += g.gamma
            total_vega += g.vega
            total_theta += g.theta
            total_rho += g.rho

        return {
            "strategy": self.__class__.__name__,
            "symbol": self.symbol,
            "spot": S,
            "sigma": sigma,
            "T": T,
            "net_price": total_price,
            "delta": total_delta,
            "gamma": total_gamma,
            "vega": total_vega,
            "theta": total_theta,
            "rho": total_rho,
            "legs": [
                {
                    "type": leg.option_type.value,
                    "strike": leg.strike,
                    "qty": leg.quantity,
                    "expiry": leg.expiry,
                    "price": leg.price(S, r, sigma, q),
                    "delta": g.delta,
                    "gamma": g.gamma,
                    "vega": g.vega,
                    "theta": g.theta,
                    "rho": g.rho,
                }
                for leg, g in zip(
                    self.legs, [l.leg_greeks(S, r, sigma, q) for l in self.legs]
                )
            ],
        }

    def payoff_at_expiry(self, S_T: float) -> float:
        """Calculate payoff at expiration."""
        total = 0.0
        for leg in self.legs:
            if leg.option_type == OptionType.CALL:
                intrinsic = max(S_T - leg.strike, 0.0)
            else:
                intrinsic = max(leg.strike - S_T, 0.0)
            total += intrinsic * leg.quantity
        return total


@dataclass
class StraddleStrategy(OptionStrategy):
    """Long Straddle: Long Call + Long Put at same strike, same expiry."""

    symbol: str = ""
    spot: float = 0.0
    r: float = 0.05
    q: float = 0.0
    legs: list[OptionLeg] | None = None

    strike: float = 0.0
    expiry: float = 0.25  # 3 months default

    def build_legs(self, **params) -> list[OptionLeg]:
        k = params.get("strike", self.strike or self.spot)
        T = params.get("expiry", self.expiry)

        self.legs = [
            OptionLeg(OptionType.CALL, k, 1, T),
            OptionLeg(OptionType.PUT, k, 1, T),
        ]
        return self.legs

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.STRADDLE


@dataclass
class StrangleStrategy(OptionStrategy):
    """Long Strangle: Long OTM Call + Long OTM Put, different strikes."""

    symbol: str = ""
    spot: float = 0.0
    r: float = 0.05
    q: float = 0.0
    legs: list[OptionLeg] | None = None

    call_strike: float = 0.0
    put_strike: float = 0.0
    expiry: float = 0.25

    def build_legs(self, **params) -> list[OptionLeg]:
        c_k = params.get("call_strike", self.call_strike or self.spot * 1.05)
        _p_k = params.get("put_strike", self.put_strike or self.spot * 0.95)
        T = params.get("expiry", self.expiry)

        self.legs = [
            OptionLeg(OptionType.CALL, c_k, 1, T),
            OptionLeg(OptionType.PUT, _p_k, 1, T),
        ]
        return self.legs

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.STRANGLE


@dataclass
class IronCondorStrategy(OptionStrategy):
    """Iron Condor: Short OTM Call Spread + Short OTM Put Spread."""

    symbol: str = ""
    spot: float = 0.0
    r: float = 0.05
    q: float = 0.0
    legs: list[OptionLeg] | None = None

    # Call spread
    call_short_strike: float = 0.0
    call_long_strike: float = 0.0
    # Put spread
    put_short_strike: float = 0.0
    put_long_strike: float = 0.0
    expiry: float = 0.25

    def build_legs(self, **params) -> list[OptionLeg]:
        cs = params.get("call_short_strike", self.call_short_strike or self.spot * 1.05)
        cl = params.get("call_long_strike", self.call_long_strike or self.spot * 1.10)
        ps = params.get("put_short_strike", self.put_short_strike or self.spot * 0.95)
        pl = params.get("put_long_strike", self.put_long_strike or self.spot * 0.90)
        T = params.get("expiry", self.expiry)

        self.legs = [
            # Short call spread
            OptionLeg(OptionType.CALL, cs, -1, T),  # Short call
            OptionLeg(OptionType.CALL, cl, 1, T),  # Long call
            # Short put spread
            OptionLeg(OptionType.PUT, ps, -1, T),  # Short put
            OptionLeg(OptionType.PUT, pl, 1, T),  # Long put
        ]
        return self.legs

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.IRON_CONDOR


@dataclass
class VerticalSpreadStrategy(OptionStrategy):
    """Vertical Spread: Long + Short options same type, different strikes."""

    symbol: str = ""
    spot: float = 0.0
    r: float = 0.05
    q: float = 0.0
    legs: list[OptionLeg] | None = None

    long_strike: float = 0.0
    short_strike: float = 0.0
    option_type: OptionType = OptionType.CALL
    expiry: float = 0.25

    def build_legs(self, **params) -> list[OptionLeg]:
        ls = params.get("long_strike", self.long_strike)
        ss = params.get("short_strike", self.short_strike)
        ot = params.get("option_type", self.option_type)
        T = params.get("expiry", self.expiry)

        # Determine which is lower strike
        if ot == OptionType.CALL:
            # Bull call spread: long lower, short higher
            low, high = sorted([ls, ss])
            self.legs = [
                OptionLeg(OptionType.CALL, low, 1, T),
                OptionLeg(OptionType.CALL, high, -1, T),
            ]
        else:
            # Bear put spread: long higher, short lower
            low, high = sorted([ls, ss])
            self.legs = [
                OptionLeg(OptionType.PUT, high, 1, T),
                OptionLeg(OptionType.PUT, low, -1, T),
            ]
        return self.legs

    @property
    def strategy_type(self) -> StrategyType:
        if self.option_type == OptionType.CALL:
            return StrategyType.BULL_CALL_SPREAD
        return StrategyType.BEAR_PUT_SPREAD


@dataclass
class ButterflyStrategy(OptionStrategy):
    """Long Butterfly: Long 1 ITM + Short 2 ATM + Long 1 OTM (all same type)."""

    symbol: str = ""
    spot: float = 0.0
    r: float = 0.05
    q: float = 0.0
    legs: list[OptionLeg] | None = None

    lower_strike: float = 0.0
    middle_strike: float = 0.0
    upper_strike: float = 0.0
    option_type: OptionType = OptionType.CALL
    expiry: float = 0.25

    def build_legs(self, **params) -> list[OptionLeg]:
        l = params.get("lower_strike", self.lower_strike)
        m = params.get("middle_strike", self.middle_strike)
        u = params.get("upper_strike", self.upper_strike)
        ot = params.get("option_type", self.option_type)
        T = params.get("expiry", self.expiry)

        # Ensure ordered
        l, m, u = sorted([l, m, u])

        self.legs = [
            OptionLeg(ot, l, 1, T),
            OptionLeg(ot, m, -2, T),
            OptionLeg(ot, u, 1, T),
        ]
        return self.legs

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.BUTTERFLY


@dataclass
class CalendarSpreadStrategy(OptionStrategy):

    symbol: str = ""
    spot: float = 0.0
    r: float = 0.05
    q: float = 0.0
    legs: list[OptionLeg] | None = None
    """Calendar Spread: Long longer-dated + Short shorter-dated (same strike)."""

    strike: float = 0.0
    option_type: OptionType = OptionType.CALL
    near_expiry: float = 0.0833  # 1 month
    far_expiry: float = 0.25  # 3 months

    def build_legs(self, **params) -> list[OptionLeg]:
        k = params.get("strike", self.strike or self.spot)
        ot = params.get("option_type", self.option_type)
        T1 = params.get("near_expiry", self.near_expiry)
        T2 = params.get("far_expiry", self.far_expiry)

        self.legs = [
            OptionLeg(ot, k, 1, T2),  # Long far-dated
            OptionLeg(ot, k, -1, T1),  # Short near-dated
        ]
        return self.legs

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.CALENDAR_SPREAD


# Convenience factory
def create_strategy(
    strategy_type: StrategyType, symbol: str, spot: float, **params
) -> OptionStrategy:
    """Factory function to create strategy instances."""
    strategies = {
        StrategyType.STRADDLE: StraddleStrategy,
        StrategyType.STRANGLE: StrangleStrategy,
        StrategyType.IRON_CONDOR: IronCondorStrategy,
        StrategyType.BULL_CALL_SPREAD: VerticalSpreadStrategy,
        StrategyType.BEAR_CALL_SPREAD: VerticalSpreadStrategy,
        StrategyType.BULL_PUT_SPREAD: VerticalSpreadStrategy,
        StrategyType.BEAR_PUT_SPREAD: VerticalSpreadStrategy,
        StrategyType.BUTTERFLY: ButterflyStrategy,
        StrategyType.CALENDAR_SPREAD: CalendarSpreadStrategy,
    }

    cls = strategies.get(strategy_type)
    if not cls:
        raise ValueError(f"Unknown strategy type: {strategy_type}")

    if strategy_type in (StrategyType.BULL_CALL_SPREAD, StrategyType.BEAR_CALL_SPREAD):
        return cls(symbol, spot, option_type=OptionType.CALL, **params)
    elif strategy_type in (StrategyType.BULL_PUT_SPREAD, StrategyType.BEAR_PUT_SPREAD):
        return cls(symbol, spot, option_type=OptionType.PUT, **params)

    return cls(symbol, spot, **params)
