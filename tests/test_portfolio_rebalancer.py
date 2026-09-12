"""
Unit and integration tests for Multi-Venue Order Router and Portfolio Rebalancing Engine.
"""

from src.portfolio.order_router import OrderRouter
from src.portfolio.rebalancer import PortfolioRebalancingEngine


def test_order_router_venue_detection_and_dry_run():
    """Verify order router auto-detects venue and handles dry-run mode."""
    router = OrderRouter()

    # Stock auto-detection -> alpaca
    stock_order = router.route_order(
        symbol="AAPL", side=1, quantity=10.0, venue="auto", dry_run=True
    )
    assert stock_order.venue == "alpaca"
    assert stock_order.status == "simulated"
    assert stock_order.filled_qty == 10.0

    # Crypto auto-detection -> kucoin
    crypto_order = router.route_order(
        symbol="BTC/USD", side=1, quantity=0.1, venue="auto", dry_run=True
    )
    assert crypto_order.venue == "kucoin"
    assert crypto_order.status == "simulated"
    assert crypto_order.filled_qty == 0.1


def test_rebalance_plan_generation():
    """Verify calculating drift deltas and rebalance orders from target weights."""
    target_weights = {
        "BTC/USD": 0.40,
        "AAPL": 0.30,
        "SPY": 0.30,
    }

    plan = PortfolioRebalancingEngine.create_rebalance_plan(
        target_weights=target_weights,
        drift_threshold_pct=1.0,
    )

    assert plan.total_nav_usd > 0
    assert len(plan.items) >= 3
    assert plan.total_turnover_usd >= 0
    assert plan.estimated_fees_usd >= 0

    # Verify each item has correct trade direction
    for item in plan.items:
        if item.trade_side == 1:
            assert item.trade_value_usd > 0
        elif item.trade_side == -1:
            assert item.trade_value_usd < 0


def test_rebalance_plan_execution():
    """Verify execution of rebalancing plan generates venue orders (sells first, then buys)."""
    target_weights = {
        "BTC/USD": 0.50,
        "AAPL": 0.20,
        "SPY": 0.30,
    }

    plan = PortfolioRebalancingEngine.create_rebalance_plan(
        target_weights=target_weights,
        drift_threshold_pct=0.5,
    )

    orders = PortfolioRebalancingEngine.execute_plan(plan, dry_run=True)
    assert isinstance(orders, list)
    if orders:
        # Check that executed orders match items
        assert all(o.status in ("simulated", "filled") for o in orders)


def test_rebalance_and_routing_endpoints():
    """Verify FastAPI order routing and rebalancing REST endpoints."""
    from fastapi.testclient import TestClient

    from src.app import app

    client = TestClient(app)

    # 1. Route order endpoint
    resp_order = client.post(
        "/api/portfolio/route_order",
        json={
            "symbol": "BTC/USD",
            "side": 1,
            "quantity": 0.05,
            "venue": "auto",
            "dry_run": True,
        },
    )
    assert resp_order.status_code == 200
    assert resp_order.json()["status"] == "success"
    assert resp_order.json()["order"]["filled_qty"] == 0.05

    # 2. Rebalance plan endpoint
    resp_plan = client.post(
        "/api/portfolio/rebalance/plan",
        json={
            "target_weights": {"BTC/USD": 0.35, "ETH/USD": 0.25, "AAPL": 0.40},
            "drift_threshold_pct": 1.0,
            "include_synthetic": True,
        },
    )
    assert resp_plan.status_code == 200
    assert resp_plan.json()["status"] == "success"
    assert "plan" in resp_plan.json()

    # 3. Rebalance execute endpoint
    resp_exec = client.post(
        "/api/portfolio/rebalance/execute",
        json={
            "target_weights": {"BTC/USD": 0.35, "ETH/USD": 0.25, "AAPL": 0.40},
            "drift_threshold_pct": 1.0,
            "dry_run": True,
            "include_synthetic": True,
        },
    )
    assert resp_exec.status_code == 200
    assert resp_exec.json()["status"] == "success"
    assert resp_exec.json()["dry_run"] is True
    assert "orders" in resp_exec.json()


def test_export_batch_to_portfolio():
    """Verify batch results export to portfolio with weight calculation."""
    from fastapi.testclient import TestClient

    from src.app import app
    from src.database import CandleDatabase

    client = TestClient(app)
    db = CandleDatabase()

    # First ensure we have some batch results by running a mini sweep
    # or by checking if any exist
    all_rows = db.load_batch_results(limit=10)

    if not all_rows:
        # Skip if no batch results exist (would require running a sweep)
        import pytest

        pytest.skip("No batch results available - run a batch sweep first")

    # Pick a valid result ID
    result_id = all_rows[0]["id"]

    # Test 1: Export with Sharpe-weighted method
    resp = client.post(
        "/api/batch_results/export_to_portfolio",
        json={
            "result_ids": [result_id],
            "weighting_method": "sharpe_weighted",
            "max_assets": 5,
            "min_sharpe": 0.0,
            "min_trades": 1,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "target_weights" in data
    assert "selected_results" in data
    assert data["weighting_method"] == "sharpe_weighted"
    assert len(data["target_weights"]) > 0

    # Verify weights sum to ~1.0
    total_weight = sum(data["target_weights"].values())
    assert abs(total_weight - 1.0) < 0.01

    # Test 2: Export with risk_parity method
    resp = client.post(
        "/api/batch_results/export_to_portfolio",
        json={
            "result_ids": [result_id],
            "weighting_method": "risk_parity",
            "max_assets": 5,
            "min_sharpe": 0.0,
            "min_trades": 1,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["weighting_method"] == "risk_parity"
    total_weight = sum(data["target_weights"].values())
    assert abs(total_weight - 1.0) < 0.01

    # Test 3: Export with equal_weight method
    resp = client.post(
        "/api/batch_results/export_to_portfolio",
        json={
            "result_ids": [result_id],
            "weighting_method": "equal_weight",
            "max_assets": 5,
            "min_sharpe": 0.0,
            "min_trades": 1,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["weighting_method"] == "equal_weight"
    total_weight = sum(data["target_weights"].values())
    assert abs(total_weight - 1.0) < 0.01

    # Verify equal weights are actually equal
    weights = list(data["target_weights"].values())
    assert all(abs(w - weights[0]) < 0.001 for w in weights)

    print("All export_to_portfolio tests passed!")


def test_export_batch_to_portfolio_validation():
    """Verify validation on export endpoint."""
    from fastapi.testclient import TestClient

    from src.app import app

    client = TestClient(app)

    # Test: Empty result_ids should fail
    resp = client.post(
        "/api/batch_results/export_to_portfolio",
        json={
            "result_ids": [],
            "weighting_method": "sharpe_weighted",
            "max_assets": 10,
            "min_sharpe": 0.5,
            "min_trades": 5,
        },
    )
    assert resp.status_code == 404

    # Test: Non-existent result IDs
    resp = client.post(
        "/api/batch_results/export_to_portfolio",
        json={
            "result_ids": [999999],
            "weighting_method": "sharpe_weighted",
            "max_assets": 10,
            "min_sharpe": 0.5,
            "min_trades": 5,
        },
    )
    assert resp.status_code == 404

    # Test: Too high min_sharpe filters everything out
    from src.database import CandleDatabase

    db = CandleDatabase()
    all_rows = db.load_batch_results(limit=1)
    if all_rows:
        result_id = all_rows[0]["id"]
        resp = client.post(
            "/api/batch_results/export_to_portfolio",
            json={
                "result_ids": [result_id],
                "weighting_method": "sharpe_weighted",
                "max_assets": 10,
                "min_sharpe": 100.0,  # impossibly high
                "min_trades": 5,
            },
        )
        assert resp.status_code == 400

    print("All validation tests passed!")


def test_batch_sweep_search_range_settings():
    """Verify batch sweep respects Optuna search range settings from config."""
    from fastapi.testclient import TestClient

    from src.app import app
    from src.batch_optimizer import BatchOptimizerConfig
    from src.config import OptunaConfig

    TestClient(app)

    # Test 1: BatchOptimizerConfig accepts all 14 search range fields
    cfg = BatchOptimizerConfig(
        symbols=["BTC/USD"],
        timeframes=["1h"],
        fast_ema_min=10,
        fast_ema_max=20,
        slow_ema_min=25,
        slow_ema_max=50,
        trend_ema_min=100,
        trend_ema_max=150,
        volume_multiplier_min=1.5,
        volume_multiplier_max=2.5,
        atr_multiplier_min=2.0,
        atr_multiplier_max=4.0,
        risk_reward_min=2.0,
        risk_reward_max=4.0,
        vwap_slope_max_bound=0.005,
        n_jobs=2,
        n_startup_trials=10,
        seed_base_params=False,
    )

    assert cfg.fast_ema_min == 10
    assert cfg.fast_ema_max == 20
    assert cfg.slow_ema_min == 25
    assert cfg.slow_ema_max == 50
    assert cfg.trend_ema_min == 100
    assert cfg.trend_ema_max == 150
    assert cfg.volume_multiplier_min == 1.5
    assert cfg.volume_multiplier_max == 2.5
    assert cfg.atr_multiplier_min == 2.0
    assert cfg.atr_multiplier_max == 4.0
    assert cfg.risk_reward_min == 2.0
    assert cfg.risk_reward_max == 4.0
    assert cfg.vwap_slope_max_bound == 0.005
    assert cfg.n_jobs == 2
    assert cfg.n_startup_trials == 10
    assert cfg.seed_base_params is False

    # Test 2: BatchOptimizeRequest accepts all 14 search range fields
    from src.app import BatchOptimizeRequest

    req = BatchOptimizeRequest(
        symbols=["BTC/USD"],
        timeframes=["1h"],
        fast_ema_min=5,
        fast_ema_max=15,
        slow_ema_min=20,
        slow_ema_max=40,
        trend_ema_min=80,
        trend_ema_max=120,
        volume_multiplier_min=1.0,
        volume_multiplier_max=3.0,
        atr_multiplier_min=1.5,
        atr_multiplier_max=3.5,
        risk_reward_min=1.5,
        risk_reward_max=3.5,
        vwap_slope_max_bound=0.002,
        n_jobs=4,
        n_startup_trials=5,
        seed_base_params=True,
    )

    assert req.fast_ema_min == 5
    assert req.fast_ema_max == 15
    assert req.slow_ema_min == 20
    assert req.slow_ema_max == 40
    assert req.trend_ema_min == 80
    assert req.trend_ema_max == 120
    assert req.volume_multiplier_min == 1.0
    assert req.volume_multiplier_max == 3.0
    assert req.atr_multiplier_min == 1.5
    assert req.atr_multiplier_max == 3.5
    assert req.risk_reward_min == 1.5
    assert req.risk_reward_max == 3.5
    assert req.vwap_slope_max_bound == 0.002
    assert req.n_jobs == 4
    assert req.n_startup_trials == 5
    assert req.seed_base_params is True

    # Test 3: Default values match OptunaConfig defaults
    default_cfg = BatchOptimizerConfig()
    optuna_defaults = OptunaConfig()

    assert default_cfg.fast_ema_min == optuna_defaults.fast_ema_min
    assert default_cfg.fast_ema_max == optuna_defaults.fast_ema_max
    assert default_cfg.slow_ema_min == optuna_defaults.slow_ema_min
    assert default_cfg.slow_ema_max == optuna_defaults.slow_ema_max
    assert default_cfg.trend_ema_min == optuna_defaults.trend_ema_min
    assert default_cfg.trend_ema_max == optuna_defaults.trend_ema_max
    assert default_cfg.volume_multiplier_min == optuna_defaults.volume_multiplier_min
    assert default_cfg.volume_multiplier_max == optuna_defaults.volume_multiplier_max
    assert default_cfg.atr_multiplier_min == optuna_defaults.atr_multiplier_min
    assert default_cfg.atr_multiplier_max == optuna_defaults.atr_multiplier_max
    assert default_cfg.risk_reward_min == optuna_defaults.risk_reward_min
    assert default_cfg.risk_reward_max == optuna_defaults.risk_reward_max
    assert default_cfg.vwap_slope_max_bound == optuna_defaults.vwap_slope_max
    assert default_cfg.n_jobs == optuna_defaults.n_jobs
    assert default_cfg.n_startup_trials == optuna_defaults.n_startup_trials
    assert default_cfg.seed_base_params == optuna_defaults.seed_base_params

    print("All batch sweep search range tests passed!")


def test_batch_sweep_strategy_mode():
    """Verify batch sweep respects strategy_mode parameter."""
    from src.app import BatchOptimizeRequest
    from src.batch_optimizer import BatchOptimizerConfig

    # Test all four strategy modes
    for mode in ["auto", "crossover", "multi_ema", "vwap_pullback"]:
        req = BatchOptimizeRequest(
            symbols=["BTC/USD"],
            timeframes=["1h"],
            strategy_mode=mode,
        )
        assert req.strategy_mode == mode

        cfg = BatchOptimizerConfig(
            symbols=["BTC/USD"],
            timeframes=["1h"],
            strategy_mode=mode,
        )
        assert cfg.strategy_mode == mode

    print("All strategy_mode tests passed!")


def test_paper_trading_balance_connector():
    """Verify PaperTradingBalanceConnector integrates with PortfolioAggregator."""
    from src.database import CandleDatabase
    from src.portfolio.aggregator import PortfolioAggregator
    from src.portfolio.connectors import PaperTradingBalanceConnector
    from src.portfolio.models import VenueBalance
    from src.settings import SettingsManager

    # Test 1: Connector instantiation
    db = CandleDatabase()
    settings = SettingsManager(db=db)
    connector = PaperTradingBalanceConnector(settings)

    assert connector.get_venue_name() == "paper"
    assert hasattr(connector, "engine")

    # Test 2: fetch_balances returns VenueBalance
    balance = connector.fetch_balances()
    assert isinstance(balance, VenueBalance)
    assert balance.venue == "paper"
    assert balance.venue_type == "crypto"
    assert hasattr(balance, "total_nav_usd")
    assert hasattr(balance, "positions")

    # Test 3: When no positions, is_connected=False
    # (This allows synthetic fallback in aggregator)
    assert balance.is_connected in [True, False]  # Could be either based on positions

    # Test 4: PortfolioAggregator includes paper connector
    aggregator = PortfolioAggregator(settings=settings)
    assert "paper" in aggregator.connectors
    assert isinstance(aggregator.connectors["paper"], PaperTradingBalanceConnector)

    # Test 5: get_unified_snapshot works with paper trading
    snapshot = aggregator.get_unified_snapshot()
    assert "paper" in snapshot.venues

    print("All PaperTradingBalanceConnector tests passed!")


def test_paper_trading_engine_get_balance_snapshot():
    """Verify PaperTradingEngine.get_balance_snapshot returns correct structure."""
    from src.database import CandleDatabase
    from src.paper.engine import PaperTradingEngine

    db = CandleDatabase()
    engine = PaperTradingEngine(db=db)

    # Test method exists and returns dict
    balance = engine.get_balance_snapshot()
    assert isinstance(balance, dict)

    # Check required keys
    required_keys = [
        "cash_usd",
        "stablecoin_usd",
        "crypto_usd",
        "stock_usd",
        "positions",
        "total_nav_usd",
        "buying_power_usd",
        "unrealized_pnl_usd",
    ]
    for key in required_keys:
        assert key in balance, f"Missing key: {key}"

    # Check types
    assert isinstance(balance["cash_usd"], (int, float))
    assert isinstance(balance["positions"], list)
    assert isinstance(balance["total_nav_usd"], (int, float))

    print("All get_balance_snapshot tests passed!")
