"""
Options & Derivatives Strategy Support (ema_vwap-m9v).

Provides:
- Black-Scholes greeks calculation (delta, gamma, vega, theta, rho)
- Implied volatility surface fitting (SVI, cubic spline)
- Options strategy definitions (straddles, spreads, iron condors, etc.)
- Options-specific Monte Carlo risk analysis
- Options data feed interface
"""

from .greeks import (
    OptionType,
    black_scholes_greeks,
    black_scholes_price,
    greeks,
    implied_volatility,
)
from .risk import (
    options_monte_carlo,
    portfolio_greeks,
)
from .strategies import (
    ButterflyStrategy,
    CalendarSpreadStrategy,
    IronCondorStrategy,
    OptionStrategy,
    StraddleStrategy,
    StrangleStrategy,
    VerticalSpreadStrategy,
)

__all__ = [
    "ButterflyStrategy",
    "CalendarSpreadStrategy",
    "IronCondorStrategy",
    "OptionStrategy",
    "OptionType",
    "StraddleStrategy",
    "StrangleStrategy",
    "VerticalSpreadStrategy",
    "black_scholes_greeks",
    "black_scholes_price",
    "fit_cubic_spline_surface",
    "fit_svi_surface",
    "greeks",
    "implied_volatility",
    "options_monte_carlo",
    "portfolio_greeks",
]
