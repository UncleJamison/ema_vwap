"""
Unit tests for Monte Carlo Simulation & Risk Analytics Engine.
"""

import math

from src.config import MonteCarloConfig
from src.monte_carlo import MonteCarloSimulator


def test_monte_carlo_simulator_basic():
    initial_capital = 10000.0
    trades = [
        {"pnl": 150.0},
        {"pnl": -50.0},
        {"pnl": 200.0},
        {"pnl": -100.0},
        {"pnl": 300.0},
        {"pnl": -80.0},
        {"pnl": 120.0},
        {"pnl": 40.0},
    ]

    mc_config = MonteCarloConfig(num_simulations=100, random_seed=42)
    simulator = MonteCarloSimulator(initial_capital, trades, mc_config)
    res = simulator.run()

    assert res["num_simulations"] == 100
    assert res["trade_count"] == 8
    assert "risk_of_ruin_pct" in res
    assert "final_equity_percentiles" in res
    assert "max_drawdown_percentiles" in res
    assert "equity_curves_percentiles" in res
    assert len(res["equity_curves_percentiles"]["p50"]) == 100
    assert (
        res["final_equity_percentiles"]["p5"] <= res["final_equity_percentiles"]["p95"]
    )


def test_monte_carlo_empty_trades():
    simulator = MonteCarloSimulator(10000.0, [])
    res = simulator.run()

    assert res["num_simulations"] == 0
    assert "message" in res


def test_monte_carlo_rejects_non_positive_simulations():
    simulator = MonteCarloSimulator(
        10000.0,
        [{"pnl": 10.0}, {"pnl": -5.0}],
        MonteCarloConfig(num_simulations=0),
    )

    import pytest

    with pytest.raises(ValueError, match="at least 1"):
        simulator.run()


def test_monte_carlo_heavy_losses_and_ruin():
    """Verify that severe losses causing capital depletion do not produce NaN or Inf in Sharpe/drawdown."""
    initial_capital = 1000.0
    trades = [
        {"pnl": -800.0},
        {"pnl": -500.0},
        {"pnl": -300.0},
        {"pnl": 50.0},
    ]

    mc_config = MonteCarloConfig(num_simulations=50, random_seed=42)
    simulator = MonteCarloSimulator(initial_capital, trades, mc_config)
    res = simulator.run()

    assert res["risk_of_ruin_pct"] > 0
    assert not any(
        math.isnan(v) for v in res["final_equity_percentiles"].values()
    )  # No NaN
    assert not any(
        math.isnan(v) for v in res["sharpe_ratio_percentiles"].values()
    )  # No NaN
    assert res["max_drawdown_percentiles"]["p95"] <= 100.0


def test_monte_carlo_permutation_mode():
    """Verify permutation sampling (without replacement)."""
    initial_capital = 5000.0
    trades = [{"pnl": 100.0}, {"pnl": -50.0}, {"pnl": 75.0}]
    mc_config = MonteCarloConfig(
        num_simulations=20, sample_with_replacement=False, random_seed=42
    )
    simulator = MonteCarloSimulator(initial_capital, trades, mc_config)
    res = simulator.run()

    assert res["num_simulations"] == 20
    # In permutation mode, final equity is identical across all permutations since sum(pnls) is constant
    expected_final = initial_capital + 100.0 - 50.0 + 75.0
    assert res["final_equity_percentiles"]["p5"] == expected_final
    assert res["final_equity_percentiles"]["p95"] == expected_final
