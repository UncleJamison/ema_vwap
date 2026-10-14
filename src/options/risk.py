"""
Options Risk Analysis (ema_vwap-m9v).

Provides:
- Options-specific Monte Carlo simulation
- Portfolio Greeks aggregation
- Greeks-based risk metrics
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.options.greeks import Greeks, black_scholes_greeks
from src.options.strategies import OptionStrategy


@dataclass
class OptionsMonteCarloResult:
    """Result of options Monte Carlo simulation."""

    paths: np.ndarray  # Shape (n_sims, n_steps)
    terminal_values: np.ndarray  # Shape (n_sims,)
    strategy_values: np.ndarray  # Shape (n_sims,)
    mean_terminal: float
    std_terminal: float
    var_95: float
    var_99: float
    expected_shortfall_95: float
    prob_profit: float
    max_drawdown: float
    greeks_paths: dict[str, np.ndarray]  # Greeks evolution


def options_monte_carlo(
    strategy: OptionStrategy,
    n_sims: int = 10000,
    n_steps: int = 252,
    T: float = 1.0,
    r: float = 0.05,
    sigma: float = 0.3,
    q: float = 0.0,
    S0: float | None = None,
    seed: int = 42,
) -> OptionsMonteCarloResult:
    """
    Monte Carlo simulation for options strategies.

    Simulates underlying price paths using Geometric Brownian Motion
    and evaluates strategy value at each step.
    """
    np.random.seed(seed)

    if S0 is None:
        S0 = strategy.spot

    dt = T / n_steps
    sqrt_dt = np.sqrt(dt)

    # Pre-generate random shocks
    shocks = np.random.normal(0, 1, size=(n_sims, n_steps))

    # Initialize paths
    paths = np.zeros((n_sims, n_steps + 1))
    paths[:, 0] = S0

    # GBM evolution
    drift = (strategy.r - strategy.q - 0.5 * sigma**2) * dt
    vol_term = sigma * sqrt_dt

    for t in range(1, n_steps + 1):
        paths[:, t] = paths[:, t - 1] * np.exp(drift + vol_term * shocks[:, t - 1])

    # Evaluate strategy at each step for terminal values
    terminal_times = np.array([leg.expiry for leg in strategy.legs])
    max_T = max(terminal_times)

    # Strategy value at each step (only valid when t*dt >= expiry for all legs)
    # For simplicity, evaluate at final time T
    strategy_values = np.zeros(n_sims)
    for i in range(n_sims):
        S_T = paths[i, -1]
        eval_result = strategy.evaluate(S_T, sigma, max_T, strategy.r, strategy.q)
        strategy_values[i] = eval_result["net_price"]

    # Terminal underlying values
    terminal_values = paths[:, -1]

    # Risk metrics
    mean_terminal = float(np.mean(terminal_values))
    std_terminal = float(np.std(terminal_values))

    # VaR on strategy P&L
    pnl = strategy_values - np.mean(strategy_values)  # Centered
    var_95 = float(np.percentile(pnl, 5))
    var_99 = float(np.percentile(pnl, 1))
    es_95 = float(np.mean(pnl[pnl <= var_95]))

    # Probability of profit
    prob_profit = float(np.mean(strategy_values > 0))

    # Max drawdown on paths
    running_max = np.maximum.accumulate(paths, axis=1)
    drawdown = (paths - running_max) / running_max
    max_drawdown = float(np.min(drawdown))

    # Greeks evolution (simplified: compute at each step for first path)
    greeks_paths = {}
    sample_path = paths[0]
    for greek_name in ["delta", "gamma", "vega", "theta", "rho"]:
        greeks_paths[greek_name] = np.zeros(n_steps + 1)
        for t in range(n_steps + 1):
            # Only compute if we have legs with remaining time
            total = 0.0
            for leg in strategy.legs:
                tau = leg.expiry - t * (max_T / len(sample_path))
                if tau > 0:
                    g = black_scholes_greeks(
                        S=sample_path[t],
                        K=leg.strike,
                        T=tau,
                        r=strategy.r,
                        sigma=0.3,  # Would use actual sigma surface
                        option_type=leg.option_type,
                    )
                    total += getattr(g, greek_name) * leg.quantity
            greeks_paths[greek_name][t] = total

    return OptionsMonteCarloResult(
        paths=paths,
        terminal_values=terminal_values,
        strategy_values=strategy_values,
        mean_terminal=mean_terminal,
        std_terminal=std_terminal,
        var_95=var_95,
        var_99=var_99,
        expected_shortfall_95=es_95,
        prob_profit=prob_profit,
        max_drawdown=max_drawdown,
        greeks_paths=greeks_paths,
    )


def portfolio_greeks(
    strategies: list[OptionStrategy],
    S: float,
    sigma: float,
    T: float,
    r: float = 0.05,
    q: float = 0.0,
) -> Greeks:
    """
    Aggregate Greeks across multiple option strategies.

    Returns portfolio-level Greeks.
    """
    total_delta = 0.0
    total_gamma = 0.0
    total_vega = 0.0
    total_theta = 0.0
    total_rho = 0.0
    total_price = 0.0

    for strat in strategies:
        eval_result = strat.evaluate(S, sigma, T, r, q)
        total_delta += eval_result["delta"]
        total_gamma += eval_result["gamma"]
        total_vega += eval_result["vega"]
        total_theta += eval_result["theta"]
        total_rho += eval_result["rho"]
        total_price += eval_result["net_price"]

    return Greeks(
        delta=total_delta,
        gamma=total_gamma,
        vega=total_vega,
        theta=total_theta,
        rho=total_rho,
        price=total_price,
    )


def strategy_risk_metrics(
    strategy: OptionStrategy,
    S: float,
    sigma: float,
    T: float,
    r: float = 0.05,
    q: float = 0.0,
) -> dict:
    """Calculate comprehensive risk metrics for a strategy."""
    eval_result = strategy.evaluate(S, sigma, T, r, q)

    # Delta-hedge effectiveness (how much delta changes with spot)
    dS = S * 0.01
    eval_up = strategy.evaluate(S + dS, sigma, T, r, q)
    eval_down = strategy.evaluate(S - dS, sigma, T, r, q)

    delta_change = eval_up["delta"] - eval_down["delta"]
    gamma_est = delta_change / (2 * dS)

    # Vega sensitivity
    dSigma = 0.01  # 1% vol change
    eval_vol_up = strategy.evaluate(S, sigma + dSigma, T, r, q)
    vega_est = (eval_vol_up["net_price"] - eval_result["net_price"]) / dSigma

    return {
        "strategy": strategy.__class__.__name__,
        "symbol": strategy.symbol,
        "spot": S,
        "sigma": sigma,
        "T": T,
        "net_price": eval_result["net_price"],
        "delta": eval_result["delta"],
        "gamma": eval_result["gamma"],
        "gamma_est": gamma_est,
        "vega": eval_result["vega"],
        "vega_est": vega_est,
        "theta": eval_result["theta"],
        "rho": eval_result["rho"],
        "max_profit": None,  # Would need payoff analysis
        "max_loss": None,
        "breakeven_points": [],  # Would need root finding
    }
