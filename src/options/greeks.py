"""
Black-Scholes Greeks Calculation (ema_vwap-m9v).

Implements analytical Black-Scholes formulas for European options:
- Price (call/put)
- Delta, Gamma, Vega, Theta, Rho
- Implied volatility solver
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from scipy.stats import norm


class OptionType(Enum):
    CALL = "call"
    PUT = "put"


@dataclass
class Greeks:
    """Container for option Greeks."""

    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    price: float

    def to_dict(self) -> dict:
        return {
            "delta": round(self.delta, 6),
            "gamma": round(self.gamma, 6),
            "vega": round(self.vega, 6),
            "theta": round(self.theta, 6),
            "rho": round(self.rho, 6),
            "price": round(self.price, 6),
        }


def _d1_d2(
    S: float,  # Spot price
    K: float,  # Strike price
    T: float,  # Time to maturity (years)
    r: float,  # Risk-free rate
    sigma: float,  # Volatility
    q: float = 0.0,  # Dividend yield
) -> tuple[float, float]:
    """Calculate d1 and d2 for Black-Scholes."""
    if T <= 0 or sigma <= 0:
        return 0.0, 0.0

    d1 = (math.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def black_scholes_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = OptionType.CALL,
    q: float = 0.0,
) -> float:
    """Calculate Black-Scholes option price."""
    if T <= 0:
        # Expired option - intrinsic value
        if option_type == OptionType.CALL:
            return max(S - K, 0.0)
        else:
            return max(K - S, 0.0)

    d1, d2 = _d1_d2(S, K, T, r, sigma, q)

    if option_type == OptionType.CALL:
        price = S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(
            d2
        )
    else:
        price = K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(
            -d1
        )

    return max(price, 0.0)


def black_scholes_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = OptionType.CALL,
    q: float = 0.0,
) -> Greeks:
    """Calculate all Black-Scholes Greeks analytically."""
    if T <= 0 or sigma <= 0:
        # Expired or zero vol
        intrinsic = (
            max(S - K, 0.0) if option_type == OptionType.CALL else max(K - S, 0.0)
        )
        delta = (
            1.0
            if option_type == OptionType.CALL and S > K
            else (0.0 if option_type == OptionType.CALL else -1.0 if S < K else 0.0)
        )
        return Greeks(
            delta=delta, gamma=0.0, vega=0.0, theta=0.0, rho=0.0, price=intrinsic
        )

    d1, d2 = _d1_d2(S, K, T, r, sigma, q)

    # Common terms
    pdf_d1 = norm.pdf(d1)
    sqrt_T = math.sqrt(T)
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)

    # Gamma (same for call and put)
    gamma = disc_q * pdf_d1 / (S * sigma * sqrt_T)

    # Vega (same for call and put, in price per 1% vol change)
    vega = S * disc_q * pdf_d1 * sqrt_T / 100.0

    if option_type == OptionType.CALL:
        delta = disc_q * norm.cdf(d1)
        theta = (
            -S * disc_q * pdf_d1 * sigma / (2 * sqrt_T)
            - r * K * disc_r * norm.cdf(d2)
            + q * S * disc_q * norm.cdf(d1)
        ) / 365.0  # Per day
        rho = K * T * disc_r * norm.cdf(d2) / 100.0
        price = S * disc_q * norm.cdf(d1) - K * disc_r * norm.cdf(d2)
    else:
        delta = -disc_q * norm.cdf(-d1)
        theta = (
            -S * disc_q * pdf_d1 * sigma / (2 * sqrt_T)
            + r * K * disc_r * norm.cdf(-d2)
            - q * S * disc_q * norm.cdf(-d1)
        ) / 365.0  # Per day
        rho = -K * T * disc_r * norm.cdf(-d2) / 100.0
        price = K * disc_r * norm.cdf(-d2) - S * disc_q * norm.cdf(-d1)

    return Greeks(
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        rho=rho,
        price=max(price, 0.0),
    )


def greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = OptionType.CALL,
    q: float = 0.0,
) -> Greeks:
    """Convenience function to get Greeks and price."""
    return black_scholes_greeks(S, K, T, r, sigma, option_type, q)


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: OptionType = OptionType.CALL,
    q: float = 0.0,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> float:
    """Solve for implied volatility using Newton-Raphson."""
    if T <= 0:
        return 0.0

    # Intrinsic value bounds
    intrinsic = max(S - K, 0.0) if option_type == OptionType.CALL else max(K - S, 0.0)
    if market_price < intrinsic - 1e-6:
        return 0.0  # Below intrinsic - arbitrage

    # Initial guess (Brenner-Subrahmanyam approximation)
    if option_type == OptionType.CALL:
        sigma = math.sqrt(2 * math.pi / T) * market_price / S
    else:
        sigma = math.sqrt(2 * math.pi / T) * market_price / S
    sigma = max(sigma, 0.01)

    for i in range(max_iter):
        g = black_scholes_greeks(S, K, T, r, sigma, option_type, q)
        price_diff = g.price - market_price

        if abs(price_diff) < tol:
            return sigma

        # Vega is derivative of price w.r.t. vol
        if g.vega < 1e-10:
            break

        # Newton-Raphson step
        sigma = sigma - price_diff / (g.vega * 100)  # vega is per 1% change

        if sigma < 0.001:
            sigma = 0.001
        elif sigma > 5.0:
            sigma = 5.0

    return max(sigma, 0.001)


__all__ = [
    "Greeks",
    "OptionType",
    "black_scholes_greeks",
    "black_scholes_price",
    "greeks",
    "implied_volatility",
]
