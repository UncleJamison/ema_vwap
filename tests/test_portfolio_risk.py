"""
Unit and integration tests for Cross-Asset Risk Budgeting, Correlation Matrix, and VaR Engine.
"""

from src.portfolio.aggregator import PortfolioAggregator
from src.portfolio.correlation import CrossAssetCorrelationEngine
from src.portfolio.models import AssetPosition, UnifiedPortfolioSnapshot
from src.portfolio.risk_budget import CrossAssetRiskBudgeter
from src.portfolio.risk_engine import PortfolioRiskEngine


def test_correlation_matrix_computation():
    """Verify correlation matrix and high correlation pair detection."""
    symbols = ["BTC/USD", "ETH/USD", "AAPL", "SPY"]
    returns_df = CrossAssetCorrelationEngine.generate_synthetic_returns(
        symbols=symbols, num_bars=120
    )
    metrics = CrossAssetCorrelationEngine.compute_correlation_matrix(returns_df)

    assert metrics["symbols"] == symbols
    assert len(metrics["correlation_matrix"]) == 4
    assert metrics["diversification_ratio"] > 1.0
    assert "average_correlation" in metrics
    assert isinstance(metrics["high_correlation_pairs"], list)


def test_portfolio_var_and_risk_decomposition():
    """Verify Parametric VaR, Historical VaR, CVaR, and component decomposition."""
    aggregator = PortfolioAggregator()
    snapshot = aggregator.get_unified_snapshot()

    risk_report = PortfolioRiskEngine.compute_portfolio_var(
        snapshot=snapshot, confidence_level=0.95, horizon_days=1
    )

    assert risk_report["total_nav_usd"] > 0
    assert risk_report["param_var_usd"] > 0
    assert risk_report["param_var_pct"] > 0
    assert risk_report["hist_var_usd"] > 0
    assert risk_report["cvar_usd"] >= risk_report["hist_var_usd"]
    assert risk_report["circuit_breaker_status"] == "NORMAL"
    assert risk_report["risk_scale_factor"] == 1.0
    assert "component_var" in risk_report


def test_circuit_breaker_triggers():
    """Verify that severe unrealized drawdowns scale down risk or halt trading."""
    # Create snapshot with 16% unrealized drawdown
    snapshot = UnifiedPortfolioSnapshot(
        timestamp="2026-08-31",
        total_nav_usd=100000.0,
        total_cash_usd=50000.0,
        total_stablecoin_usd=0.0,
        total_crypto_usd=50000.0,
        total_stock_usd=0.0,
        total_buying_power_usd=50000.0,
        total_unrealized_pnl_usd=-16000.0,  # 16% drawdown
        crypto_weight_pct=50.0,
        stock_weight_pct=0.0,
        cash_weight_pct=50.0,
        positions=[
            AssetPosition(
                symbol="BTC/USD",
                venue="synthetic",
                asset_type="crypto",
                side=1,
                quantity=1.0,
                entry_price=60000.0,
                current_price=50000.0,
                market_value_usd=50000.0,
                unrealized_pnl_usd=-16000.0,
            )
        ],
    )

    risk_report = PortfolioRiskEngine.compute_portfolio_var(snapshot)
    assert risk_report["circuit_breaker_status"] == "EMERGENCY_HALT"
    assert risk_report["risk_scale_factor"] == 0.0

    # Test position sizer responds to EMERGENCY_HALT
    sizing = CrossAssetRiskBudgeter.calculate_position_size(
        symbol="ETH/USD",
        entry_price=3000.0,
        stop_loss_price=2900.0,
        snapshot=snapshot,
    )
    assert sizing["units"] == 0.0
    assert "EMERGENCY_HALT" in sizing["reason"]


def test_correlation_penalized_position_sizing():
    """Verify high correlation with existing portfolio penalizes position size."""
    aggregator = PortfolioAggregator()
    snapshot = aggregator.get_unified_snapshot()

    # Size trade for an asset highly correlated with crypto positions (e.g. BTC/USD)
    sizing_btc = CrossAssetRiskBudgeter.calculate_position_size(
        symbol="BTC/USD",
        entry_price=65000.0,
        stop_loss_price=63000.0,
        snapshot=snapshot,
        risk_per_trade_pct=1.0,
    )

    assert sizing_btc["units"] > 0
    assert sizing_btc["effective_risk_usd"] > 0
    assert 0.0 < sizing_btc["correlation_penalty"] <= 1.0


def test_portfolio_risk_endpoints():
    """Verify FastAPI correlation, var, and size_position REST endpoints."""
    from fastapi.testclient import TestClient

    from src.app import app

    client = TestClient(app)

    # 1. Correlation endpoint
    resp_corr = client.get(
        "/api/portfolio/correlation?symbols=BTC/USD,ETH/USD,AAPL,SPY"
    )
    assert resp_corr.status_code == 200
    assert resp_corr.json()["status"] == "success"
    assert "metrics" in resp_corr.json()

    # 2. VaR endpoint
    resp_var = client.get(
        "/api/portfolio/var?confidence_level=0.95&horizon_days=1&include_synthetic=true"
    )
    assert resp_var.status_code == 200
    assert resp_var.json()["status"] == "success"
    assert "risk_report" in resp_var.json()

    # 3. Position Sizing endpoint
    payload = {
        "symbol": "AAPL",
        "entry_price": 220.0,
        "stop_loss_price": 215.0,
        "risk_per_trade_pct": 1.0,
        "allow_fractional": True,
        "include_synthetic": True,
    }
    resp_size = client.post("/api/portfolio/size_position", json=payload)
    assert resp_size.status_code == 200
    assert resp_size.json()["status"] == "success"
    sizing = resp_size.json()["sizing"]
    assert sizing["units"] > 0
    assert sizing["position_value_usd"] > 0
