"""
Unit and integration tests for Portfolio-Level Risk Budgeting Engine.
"""

import numpy as np

from src.portfolio.correlation import CrossAssetCorrelationEngine
from src.portfolio.portfolio_risk_budget import (
    PortfolioRiskBudgetEngine,
    RiskBudgetConfig,
    RiskBudgetResult,
)


def test_risk_parity_weights():
    """Verify risk parity weights equalize risk contributions."""
    symbols = ["BTC/USD", "ETH/USD", "AAPL", "SPY"]
    returns_df = CrossAssetCorrelationEngine.generate_synthetic_returns(
        symbols=symbols, num_bars=200
    )

    # Build covariance matrix
    cov_matrix = returns_df[symbols].cov().values

    weights = PortfolioRiskBudgetEngine.compute_risk_parity_weights(
        cov_matrix=cov_matrix,
        symbols=symbols,
        max_single_weight=0.50,
    )

    # Weights should sum to ~1.0
    total_weight = sum(weights.values())
    assert abs(total_weight - 1.0) < 1e-6, f"Weights sum to {total_weight}"

    # All weights should be positive and within bounds
    for w in weights.values():
        assert 0.0 <= w <= 0.50

    # Risk contributions should be approximately equal
    weight_arr = np.array([weights[s] for s in symbols])
    marginal_contribs = cov_matrix @ weight_arr
    risk_contribs = weight_arr * marginal_contribs
    port_var = float(weight_arr @ cov_matrix @ weight_arr)

    if port_var > 0:
        rc_pct = risk_contribs / port_var * 100
        # Risk contributions should be roughly equal (within 5%)
        mean_rc = rc_pct.mean()
        assert np.allclose(
            rc_pct, mean_rc, rtol=0.05
        ), f"Risk contributions not equal: {rc_pct}"


def test_inverse_volatility_weights():
    """Verify inverse volatility weighting."""
    symbols = ["BTC/USD", "ETH/USD", "AAPL", "SPY"]
    returns_df = CrossAssetCorrelationEngine.generate_synthetic_returns(
        symbols=symbols, num_bars=200
    )
    vols = returns_df[symbols].std().values

    weights = PortfolioRiskBudgetEngine.compute_inverse_volatility_weights(
        volatilities=list(vols),
        symbols=symbols,
        max_single_weight=0.50,
    )

    total_weight = sum(weights.values())
    assert abs(total_weight - 1.0) < 1e-6

    # Lower volatility assets should get higher weights
    for i, s in enumerate(symbols):
        for j, t in enumerate(symbols):
            if vols[i] < vols[j]:
                assert weights[s] >= weights[t] * 0.9  # Allow small numerical diff


def test_drawdown_constraints():
    """Verify per-asset max drawdown constraints scale weights."""
    weights = {"BTC/USD": 0.4, "ETH/USD": 0.3, "AAPL": 0.2, "SPY": 0.1}
    asset_dd = {"BTC/USD": 50.0, "ETH/USD": 20.0, "AAPL": 15.0, "SPY": 10.0}

    # Budget of 30% DD - BTC should be constrained
    adjusted, details = PortfolioRiskBudgetEngine.apply_drawdown_constraints(
        weights=weights,
        asset_drawdowns=asset_dd,
        max_dd_per_asset_pct=30.0,
    )

    # BTC weight should be scaled down (50% > 30%)
    assert adjusted["BTC/USD"] < weights["BTC/USD"]
    assert details["BTC/USD"]["constrained"] is True
    assert details["BTC/USD"]["scale_factor"] < 1.0

    # ETH, AAPL, SPY should not be constrained (all <= 30%)
    assert details["ETH/USD"]["constrained"] is False
    assert details["AAPL"]["constrained"] is False
    assert details["SPY"]["constrained"] is False

    # Adjusted weights should sum to ~1.0
    total = sum(adjusted.values())
    assert abs(total - 1.0) < 1e-6


def test_zero_drawdown_budget_no_constraints():
    """Verify zero DD budget means no constraints."""
    weights = {"BTC/USD": 0.6, "ETH/USD": 0.4}
    asset_dd = {"BTC/USD": 50.0, "ETH/USD": 60.0}

    adjusted, details = PortfolioRiskBudgetEngine.apply_drawdown_constraints(
        weights=weights,
        asset_drawdowns=asset_dd,
        max_dd_per_asset_pct=0.0,  # No constraint
    )

    assert adjusted == weights
    assert details["BTC/USD"]["constrained"] is False
    assert details["ETH/USD"]["constrained"] is False


def test_full_risk_budget_computation():
    """Verify end-to-end risk budget computation with MC."""
    symbols = ["BTC/USD", "ETH/USD", "AAPL", "SPY"]
    returns_df = CrossAssetCorrelationEngine.generate_synthetic_returns(
        symbols=symbols, num_bars=200
    )

    asset_metrics = {
        "BTC/USD": {"max_drawdown_pct": 40.0, "sharpe_ratio": 1.5},
        "ETH/USD": {"max_drawdown_pct": 35.0, "sharpe_ratio": 1.2},
        "AAPL": {"max_drawdown_pct": 25.0, "sharpe_ratio": 1.0},
        "SPY": {"max_drawdown_pct": 20.0, "sharpe_ratio": 0.8},
    }

    config = RiskBudgetConfig(
        risk_parity=True,
        max_drawdown_per_asset_pct=30.0,
        max_single_asset_weight=0.30,
        mc_simulations=100,  # Reduced for test speed
    )

    result = PortfolioRiskBudgetEngine.compute_risk_budget(
        symbols=symbols,
        asset_metrics=asset_metrics,
        returns_df=returns_df,
        config=config,
    )

    assert isinstance(result, RiskBudgetResult)
    assert result.method == "risk_parity"
    assert abs(sum(result.weights.values()) - 1.0) < 1e-6
    assert result.portfolio_volatility > 0
    assert result.diversification_ratio >= 1.0
    assert "monte_carlo" in result.__dict__
    assert result.monte_carlo["num_simulations"] == 100
    assert result.monte_carlo["risk_of_ruin_pct"] >= 0


def test_risk_budget_without_asset_metrics():
    """Verify risk budget works when asset_metrics is None (estimated from returns)."""
    symbols = ["BTC/USD", "ETH/USD", "AAPL"]
    returns_df = CrossAssetCorrelationEngine.generate_synthetic_returns(
        symbols=symbols, num_bars=150
    )

    config = RiskBudgetConfig(
        risk_parity=True,
        max_drawdown_per_asset_pct=0.0,  # No DD constraint
        max_single_asset_weight=0.50,
        mc_simulations=50,
    )

    result = PortfolioRiskBudgetEngine.compute_risk_budget(
        symbols=symbols,
        asset_metrics=None,
        returns_df=returns_df,
        config=config,
    )

    assert result.method == "risk_parity"
    assert len(result.weights) == 3
    assert abs(sum(result.weights.values()) - 1.0) < 1e-6


def test_monte_carlo_outputs():
    """Verify MC simulation produces expected percentile outputs."""
    symbols = ["BTC/USD", "ETH/USD"]
    returns_df = CrossAssetCorrelationEngine.generate_synthetic_returns(
        symbols=symbols, num_bars=100
    )
    weights = {"BTC/USD": 0.6, "ETH/USD": 0.4}

    mc = PortfolioRiskBudgetEngine.compute_portfolio_monte_carlo(
        weights=weights,
        returns_df=returns_df,
        initial_capital=10000.0,
        num_simulations=100,
        horizon_days=60,
        confidence_levels=[5.0, 50.0, 95.0],
    )

    assert mc["num_simulations"] == 100
    assert "final_equity_percentiles" in mc
    assert "max_drawdown_percentiles" in mc
    assert "p5" in mc["final_equity_percentiles"]
    assert "p50" in mc["final_equity_percentiles"]
    assert "p95" in mc["final_equity_percentiles"]
    eq_p5 = mc["final_equity_percentiles"]["p5"]
    eq_p50 = mc["final_equity_percentiles"]["p50"]
    eq_p95 = mc["final_equity_percentiles"]["p95"]
    assert eq_p5 < eq_p50 < eq_p95
    assert mc["risk_of_ruin_pct"] >= 0.0
    assert "portfolio_sharpe" in mc
    assert "portfolio_volatility_annual_pct" in mc


def test_risk_budget_result_serialization():
    """Verify RiskBudgetResult.to_dict() produces JSON-serializable output."""
    import json

    symbols = ["BTC/USD", "ETH/USD"]
    returns_df = CrossAssetCorrelationEngine.generate_synthetic_returns(
        symbols=symbols, num_bars=100
    )

    config = RiskBudgetConfig(
        risk_parity=True,
        max_drawdown_per_asset_pct=30.0,
        max_single_asset_weight=0.50,
        mc_simulations=50,
    )

    result = PortfolioRiskBudgetEngine.compute_risk_budget(
        symbols=symbols,
        asset_metrics={"BTC/USD": {"max_drawdown_pct": 35.0}},
        returns_df=returns_df,
        config=config,
    )

    d = result.to_dict()

    # Should be JSON serializable
    json_str = json.dumps(d)
    assert isinstance(json_str, str)

    # Check structure
    assert "weights" in d
    assert "method" in d
    assert "risk_contributions" in d
    assert "portfolio_volatility" in d
    assert "diversification_ratio" in d
    assert "max_drawdown_constraints" in d
    assert "monte_carlo" in d


def test_portfolio_risk_budget_api_endpoint():
    """Verify FastAPI portfolio risk budget endpoint."""
    from fastapi.testclient import TestClient

    from src.app import app

    client = TestClient(app)

    payload = {
        "symbols": ["BTC/USD", "ETH/USD", "AAPL", "SPY"],
        "risk_parity": True,
        "max_drawdown_per_asset_pct": 35.0,
        "max_single_asset_weight": 0.30,
        "mc_simulations": 100,
    }

    resp = client.post("/api/portfolio/risk_budget", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "result" in data
    result = data["result"]
    assert "weights" in result
    assert "risk_contributions" in result
    assert "monte_carlo" in result
    assert len(result["weights"]) == 4
    assert abs(sum(result["weights"].values()) - 1.0) < 0.01


def test_export_batch_to_portfolio_risk_budget():
    """Verify export_batch_to_portfolio with risk_budget method."""
    from fastapi.testclient import TestClient

    from src.app import app
    from src.database import CandleDatabase

    client = TestClient(app)
    db = CandleDatabase()

    all_rows = db.load_batch_results(limit=5)
    if not all_rows:
        import pytest

        pytest.skip("No batch results available - run a batch sweep first")

    result_id = all_rows[0]["id"]

    # Test risk_budget weighting method
    resp = client.post(
        "/api/batch_results/export_to_portfolio",
        json={
            "result_ids": [result_id],
            "weighting_method": "risk_budget",
            "max_assets": 5,
            "min_sharpe": 0.0,
            "min_trades": 1,
            "max_drawdown_per_asset_pct": 35.0,
            "max_single_asset_weight": 0.30,
            "mc_simulations": 100,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["weighting_method"] == "risk_budget"
    assert "target_weights" in data
    total_weight = sum(data["target_weights"].values())
    assert abs(total_weight - 1.0) < 0.01


if __name__ == "__main__":
    test_risk_parity_weights()
    print("test_risk_parity_weights passed")
    test_inverse_volatility_weights()
    print("test_inverse_volatility_weights passed")
    test_drawdown_constraints()
    print("test_drawdown_constraints passed")
    test_zero_drawdown_budget_no_constraints()
    print("test_zero_drawdown_budget_no_constraints passed")
    test_full_risk_budget_computation()
    print("test_full_risk_budget_computation passed")
    test_risk_budget_without_asset_metrics()
    print("test_risk_budget_without_asset_metrics passed")
    test_monte_carlo_outputs()
    print("test_monte_carlo_outputs passed")
    test_risk_budget_result_serialization()
    print("test_risk_budget_result_serialization passed")
    print("\nAll tests passed!")
