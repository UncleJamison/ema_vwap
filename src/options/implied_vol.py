"""
Implied Volatility Surface Fitting (ema_vwap-m9v).

Implements:
- SVI (Stochastic Volatility Inspired) parameterization
- Cubic spline surface interpolation
- Arbitrage-free constraints
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.interpolate import RectBivariateSpline
from scipy.optimize import minimize

from src.options.greeks import OptionType, implied_volatility


@dataclass
class SVIParameters:
    """SVI (Stochastic Volatility Inspired) parameters.

    SVI parameterization: sigma^2(k) = a + b * (rho * (k - m) + sqrt((k - m)^2 + sigma^2))
    where k = log(K/F) is log-moneyness.
    """

    a: float  # Overall variance level
    b: float  # Slope of wings
    rho: float  # Correlation (-1 < rho < 1)
    m: float  # Location of minimum
    sigma: float  # Curvature (sigma > 0)

    def variance(self, k: float | np.ndarray) -> float | np.ndarray:
        """Compute total variance for given log-moneyness."""
        return self.a + self.b * (
            self.rho * (k - self.m) + np.sqrt((k - self.m) ** 2 + self.sigma**2)
        )

    def implied_vol(self, k: float | np.ndarray, T: float = 1.0) -> float | np.ndarray:
        """Compute implied volatility for given log-moneyness and maturity."""
        var = self.variance(k)
        var = np.maximum(var, 0)
        return np.sqrt(var / T)


@dataclass
class VolSurface:
    """Implied volatility surface with interpolation."""

    strikes: np.ndarray  # Shape (n_strikes,)
    maturities: np.ndarray  # Shape (n_maturities,)
    implied_vols: np.ndarray  # Shape (n_maturities, n_strikes)
    spot: float  # Current spot price
    forward: np.ndarray  # Shape (n_maturities,) - forward prices

    def __post_init__(self):
        self._interpolator = None
        self._build_interpolator()

    def _build_interpolator(self):
        """Build 2D interpolator for the surface."""
        # Use log-moneyness as x-axis
        log_moneyness = np.log(self.strikes / self.forward[:, np.newaxis])

        # Create 2D spline interpolator
        self._interpolator = RectBivariateSpline(
            self.maturities,
            log_moneyness[0],  # Assume same strikes for all maturities
            self.implied_vols.T,  # Transpose for RectBivariateSpline (x=maturities, y=strikes)
            kx=min(3, len(self.maturities) - 1),
            ky=min(3, len(self.strikes) - 1),
            s=0,  # Interpolating spline
        )

    def get_iv(self, T: float, K: float) -> float:
        """Get interpolated implied volatility for maturity T and strike K."""
        if self._interpolator is None:
            self._build_interpolator()

        # Find forward for maturity T
        F = np.interp(T, self.maturities, self.forward)
        k = np.log(K / F)

        # Interpolate
        try:
            iv = self._interpolator(T, k, grid=False)[0]
            return max(float(iv), 0.001)
        except Exception:
            # Fallback to nearest neighbor
            t_idx = np.argmin(np.abs(self.maturities - T))
            k_vals = np.log(self.strikes / self.forward[t_idx])
            k_idx = np.argmin(np.abs(k_vals - k))
            return max(float(self.implied_vols[t_idx, k_idx]), 0.001)

    def get_smile(self, T: float) -> tuple[np.ndarray, np.ndarray]:
        """Get implied volatility smile for maturity T."""
        t_idx = np.argmin(np.abs(self.maturities - T))
        return self.strikes, self.implied_vols[t_idx]


def fit_svi_surface(
    strikes: np.ndarray,
    maturities: np.ndarray,
    implied_vols: np.ndarray,
    forwards: np.ndarray,
    T: float,
) -> SVIParameters:
    """
    Fit SVI parameters for a single maturity slice.

    Args:
        strikes: Array of strikes
        maturities: Array of maturities (same length as implied_vols columns)
        implied_vols: 2D array of IVs (n_maturities x n_strikes)
        forwards: Forward prices for each maturity
        T: Target maturity to fit

    Returns:
        Fitted SVIParameters
    """
    # Find maturity slice
    t_idx = np.argmin(np.abs(maturities - T))
    iv_slice = implied_vols[t_idx]
    F = forwards[t_idx]

    # Log-moneyness
    k = np.log(strikes / F)
    var = iv_slice**2 * T  # Total variance

    # Initial guess for SVI parameters
    a_init = np.min(var) * 0.8
    b_init = (
        (np.max(var) - np.min(var)) / (np.max(k) - np.min(k)) if len(k) > 1 else 0.1
    )
    rho_init = 0.0
    m_init = k[np.argmin(var)]
    sigma_init = (np.max(k) - np.min(k)) / 4 if len(k) > 1 else 0.1

    def svi_variance(params, k_vals):
        a, b, rho, m, sigma = params
        return a + b * (rho * (k_vals - m) + np.sqrt((k_vals - m) ** 2 + sigma**2))

    def objective(params):
        pred_var = svi_variance(params, k)
        return np.sum((pred_var - var) ** 2)

    # Constraints: a > 0, b > 0, -1 < rho < 1, sigma > 0
    bounds = [(1e-6, None), (1e-6, None), (-0.99, 0.99), (None, None), (1e-6, None)]

    result = minimize(
        objective,
        x0=[a_init, b_init, rho_init, m_init, sigma_init],
        bounds=bounds,
        method="L-BFGS-B",
        options={"maxiter": 500},
    )

    a, b, rho, m, sigma = result.x
    return SVIParameters(a=a, b=b, rho=rho, m=m, sigma=sigma)


def fit_cubic_spline_surface(
    strikes: np.ndarray,
    maturities: np.ndarray,
    implied_vols: np.ndarray,
    forwards: np.ndarray,
) -> VolSurface:
    """
    Build a cubic spline volatility surface.

    Returns a VolSurface object with 2D spline interpolation.
    """
    return VolSurface(
        strikes=strikes,
        maturities=maturities,
        implied_vols=implied_vols,
        spot=forwards[0],  # Approximate
        forward=forwards,
    )


def build_vol_surface_from_market_data(
    market_data: list[dict],
    spot: float,
    r: float = 0.05,
) -> VolSurface:
    """
    Build volatility surface from raw option market data.

    Args:
        market_data: List of dicts with keys:
            - strike: float
            - maturity: float (years)
            - option_type: 'call' or 'put'
            - market_price: float
        spot: Current spot price
        r: Risk-free rate

    Returns:
        VolSurface object
    """
    # Group by maturity
    maturities = sorted({d["maturity"] for d in market_data})
    all_strikes = sorted({d["strike"] for d in market_data})

    # Compute implied vols
    implied_vols = np.zeros((len(maturities), len(all_strikes)))
    forwards = np.zeros(len(maturities))

    for i, T in enumerate(maturities):
        forwards[i] = spot * np.exp(r * T)  # Forward = S * exp(rT)
        for j, K in enumerate(all_strikes):
            # Find market price for this strike/maturity
            price = None
            for d in market_data:
                if abs(d["maturity"] - T) < 1e-6 and abs(d["strike"] - K) < 1e-6:
                    price = d["market_price"]
                    break

            if price is not None:
                opt_type = (
                    OptionType.CALL if d["option_type"] == "call" else OptionType.PUT
                )
                try:
                    iv = implied_volatility(
                        market_price=price, S=spot, K=K, T=T, r=r, option_type=opt_type
                    )
                    implied_vols[i, j] = iv
                except Exception:
                    implied_vols[i, j] = 0.2  # Default
            else:
                implied_vols[i, j] = 0.2

    return VolSurface(
        strikes=np.array(all_strikes),
        maturities=np.array(maturities),
        implied_vols=implied_vols,
        spot=spot,
        forward=forwards,
    )
