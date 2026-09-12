"""
Unit tests verifying parameter preservation and flow across
Optuna -> Apply -> Backtest -> Deploy -> Paper Trading.
"""

from fastapi.testclient import TestClient

from src.app import BacktestRequest, app, build_strategy_params
from src.config import StrategyParams
from src.database import CandleDatabase
from src.paper.engine import PaperTradingEngine
from src.paper.ledger import PaperLedger
from src.paper.models import PaperPosition, PaperProfile


def test_request_to_strategy_params_fidelity():
    """Verify all parameters including max_holding_bars and multi-asset rules are preserved."""
    custom_params = {
        "strategy_mode": "pullback",
        "trade_direction": "long_short",
        "fast_ema": 12,
        "slow_ema": 26,
        "trend_ema": 65,
        "vwap_slope_min": 0.0003,
        "vwap_slope_lookback": 8,
        "volume_filter_enabled": True,
        "volume_multiplier": 1.7,
        "volume_sma_period": 25,
        "pullback_tolerance_pct": 0.45,
        "stop_loss_type": "atr",
        "vwap_stop_offset_pct": 0.18,
        "atr_period": 10,
        "atr_multiplier": 3.2,
        "risk_reward_ratio": 2.8,
        "risk_per_trade_pct": 1.5,
        "max_holding_bars": 18,
        "initial_capital": 25000.0,
        "maker_fee_pct": 0.02,
        "taker_fee_pct": 0.04,
        "slippage_pct": 0.03,
        "asset_type": "stock",
        "session_mode": "rth",
        "vwap_anchor": "US_EQUITY",
        "allow_fractional_shares": False,
        "enforce_margin_calls": True,
        "min_margin_equity": 2500.0,
        "long_buying_power_ratio": 4.0,
        "short_buying_power_ratio": 2.0,
        "position_size": 100.0,
    }

    req = BacktestRequest(**custom_params)
    strat_params = build_strategy_params(req)

    assert strat_params.max_holding_bars == 18
    assert strat_params.pullback_tolerance_pct == 0.45
    assert strat_params.atr_multiplier == 3.2
    assert strat_params.vwap_stop_offset_pct == 0.18
    assert strat_params.asset_type == "stock"
    assert strat_params.session_mode == "rth"
    assert strat_params.vwap_anchor == "US_EQUITY"
    assert strat_params.allow_fractional_shares is False
    assert strat_params.enforce_margin_calls is True
    assert strat_params.min_margin_equity == 2500.0
    assert strat_params.position_size == 100.0


def test_paper_profile_get_strategy_params():
    """Verify PaperProfile domain model converts stored JSON parameters to StrategyParams."""
    profile = PaperProfile(
        exchange="kucoin",
        symbol="ETH/USDT",
        timeframe="15m",
        strategy_mode="crossover",
        target_metric="sortino_ratio",
        params={
            "fast_ema": 14,
            "slow_ema": 35,
            "trend_ema": 70,
            "max_holding_bars": 24,
            "atr_multiplier": 2.5,
            "non_existent_extra_key": 999,  # Should be safely ignored
        },
    )

    strat_params = profile.get_strategy_params()
    assert isinstance(strat_params, StrategyParams)
    assert strat_params.fast_ema == 14
    assert strat_params.slow_ema == 35
    assert strat_params.trend_ema == 70
    assert strat_params.max_holding_bars == 24
    assert strat_params.atr_multiplier == 2.5


def test_paper_engine_reconciliation_safely_parses_params(tmp_path):
    """Verify PaperTradingEngine reconciles positions without AttributeError or TypeError."""
    db_file = tmp_path / "test_paper_recon.db"
    db = CandleDatabase(db_path=str(db_file))
    ledger = PaperLedger(db=db)
    engine = PaperTradingEngine(db=db)

    # Record open position with extra metadata
    pos = PaperPosition(
        exchange="gemini",
        symbol="BTC/USD",
        side="LONG",
        entry_price=50000.0,
        current_price=50100.0,
        quantity=0.1,
        cost_basis=5000.0,
        metadata={
            "params": {
                "max_holding_bars": 12,
                "atr_multiplier": 3.0,
                "random_noise_metric": 42,
            }
        },
    )
    ledger.save_position(pos)

    # Reconcile downtime: must not crash
    reconciled = engine.reconcile_positions_on_startup()
    assert isinstance(reconciled, list)


def test_api_backtest_with_extended_params():
    """Verify POST /api/backtest accepts and evaluates with full extended parameters."""
    client = TestClient(app)
    payload = {
        "exchange": "synthetic",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "limit": 100,
        "strategy_mode": "crossover",
        "fast_ema": 5,
        "slow_ema": 15,
        "trend_ema": 30,
        "max_holding_bars": 10,
        "pullback_tolerance_pct": 0.25,
        "atr_multiplier": 2.5,
    }
    response = client.post("/api/backtest", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "metrics" in data
    assert "chart_data" in data


def test_full_pipeline_optuna_to_paper_trading():
    """
    End-to-end integration test: Optuna Sweep -> Apply -> Backtest -> Deploy -> Paper Trade.
    Verifies parameter propagation across all 5 stages without stubbing or loss.
    """
    import os
    import tempfile

    from fastapi.testclient import TestClient

    from src.app import app
    from src.backtester import BacktestEngine
    from src.config import OptunaConfig, StrategyParams
    from src.data_loader import DataLoader
    from src.database import CandleDatabase
    from src.optimizer import OptunaOptimizer
    from src.paper.engine import PaperTradingEngine
    from src.paper.ledger import PaperLedger
    from src.paper.models import PaperProfile

    TestClient(app)

    # Step 1: Generate synthetic data for testing
    dl = DataLoader()
    df = dl.generate_synthetic_candles(
        symbol="BTC/USD",
        timeframe="5m",
        num_bars=500,
        seed=42,
    )
    assert len(df) > 100, "Need sufficient data for sweep"

    # Step 2: Run mini Optuna sweep (3 trials)
    base_params = StrategyParams(
        fast_ema=8,
        slow_ema=21,
        trend_ema=55,
        strategy_mode="crossover",
    )
    opt_config = OptunaConfig(
        n_trials=3,
        target_metric="sharpe_ratio",
        min_trades=1,
        fast_ema_min=5,
        fast_ema_max=15,
        slow_ema_min=15,
        slow_ema_max=30,
        trend_ema_min=40,
        trend_ema_max=80,
        seed_base_params=True,
    )
    optimizer = OptunaOptimizer(base_params=base_params, config=opt_config)
    opt_result = optimizer.optimize(df)

    assert opt_result is not None, "Optuna optimization should return result"
    assert "best_params" in opt_result, "Result should contain best_params"
    best_params = opt_result["best_params"]
    assert best_params is not None, "Best params should not be None"

    # Verify key params exist in best_params
    for key in [
        "fast_ema",
        "slow_ema",
        "trend_ema",
        "atr_multiplier",
        "risk_reward_ratio",
    ]:
        assert key in best_params, f"Missing key {key} in best_params"

    # Step 3: Apply best params to backtest
    bt_params = StrategyParams(
        fast_ema=best_params["fast_ema"],
        slow_ema=best_params["slow_ema"],
        trend_ema=best_params["trend_ema"],
        atr_multiplier=best_params.get("atr_multiplier", 2.0),
        risk_reward_ratio=best_params.get("risk_reward_ratio", 2.0),
        strategy_mode=base_params.strategy_mode,
    )
    bt_engine = BacktestEngine(params=bt_params)
    bt_result = bt_engine.run(df)

    assert bt_result is not None, "Backtest should return result"
    assert hasattr(bt_result, "total_trades"), "BacktestResult should have total_trades"

    # Step 4: Deploy as paper profile
    profile = PaperProfile(
        exchange="synthetic",
        symbol="BTC/USD",
        timeframe="5m",
        strategy_mode=base_params.strategy_mode,
        target_metric="sharpe_ratio",
        params={
            "fast_ema": bt_params.fast_ema,
            "slow_ema": bt_params.slow_ema,
            "trend_ema": bt_params.trend_ema,
            "atr_multiplier": bt_params.atr_multiplier,
            "risk_reward_ratio": bt_params.risk_reward_ratio,
            "max_holding_bars": bt_params.max_holding_bars,
        },
    )
    strat_params = profile.get_strategy_params()
    assert isinstance(strat_params, StrategyParams)
    assert strat_params.fast_ema == bt_params.fast_ema
    assert strat_params.slow_ema == bt_params.slow_ema
    assert strat_params.trend_ema == bt_params.trend_ema
    assert strat_params.atr_multiplier == bt_params.atr_multiplier

    # Step 5: Verify paper engine can use these params
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = None
    engine = None
    try:
        db = CandleDatabase(db_path=db_path)
        PaperLedger(db=db)
        engine = PaperTradingEngine(db=db)

        # Register a profile with the optimized params
        profile_id = engine.registry.save_profile(
            exchange="synthetic",
            symbol="BTC/USD",
            timeframe="5m",
            strategy_mode=base_params.strategy_mode,
            target_metric="sharpe_ratio",
            params={
                "fast_ema": strat_params.fast_ema,
                "slow_ema": strat_params.slow_ema,
                "trend_ema": strat_params.trend_ema,
                "atr_multiplier": strat_params.atr_multiplier,
                "risk_reward_ratio": strat_params.risk_reward_ratio,
            },
        )
        assert profile_id is not None

        # Verify profile retrieves correct params
        profiles = engine.registry.list_profiles(active_only=True)
        assert len(profiles) >= 1
        deployed = next(p for p in profiles if p.profile_id == profile_id)
        assert deployed.params["fast_ema"] == bt_params.fast_ema
        assert deployed.params["slow_ema"] == bt_params.slow_ema

        # Step 6: Evaluate the profile (simulate one step)
        # This verifies the engine can actually use the params without error
        eval_result = engine.evaluate_symbol(
            exchange="synthetic",
            symbol="BTC/USD",
            timeframe="5m",
            force_candles=df.tail(50),
        )
        # Evaluation may return signal or None - just verify no exception
        assert eval_result is None or isinstance(eval_result, dict)

        print("✅ Full pipeline Optuna->Apply->Backtest->Deploy->PaperTrade verified!")

    finally:
        # Close engine and db to release file handle on Windows
        if engine is not None:
            try:
                engine.stop_polling()
            except (
                RuntimeError,
                ValueError,
                TypeError,
            ):  # pragma: no cover - best effort cleanup
                pass
        # CandleDatabase doesn't have a persistent conn attribute; it manages connections internally
        # Just ensure the file is cleaned up
        try:
            os.unlink(db_path)
        except PermissionError:
            # On Windows, file might still be locked; ignore
            pass
        except (
            RuntimeError,
            ValueError,
            TypeError,
        ):  # pragma: no cover - best effort cleanup
            pass


def test_pipeline_parameter_fidelity_check():
    """
    Quick sanity check: verify no parameter keys are dropped at each transformation.
    """
    from src.app import BacktestRequest, build_strategy_params
    from src.paper.models import PaperProfile

    # Start with full params
    original = {
        "fast_ema": 11,
        "slow_ema": 28,
        "trend_ema": 67,
        "atr_multiplier": 3.5,
        "risk_reward_ratio": 2.7,
        "max_holding_bars": 22,
        "pullback_tolerance_pct": 0.33,
        "vwap_stop_offset_pct": 0.15,
        "volume_multiplier": 1.8,
        "vwap_slope_min": 0.0002,
        "strategy_mode": "pullback",
        "trade_direction": "long_short",
    }

    # 1. BacktestRequest -> StrategyParams
    req = BacktestRequest(**original)
    strat = build_strategy_params(req)
    assert strat.fast_ema == 11
    assert strat.slow_ema == 28
    assert strat.trend_ema == 67
    assert strat.atr_multiplier == 3.5
    assert strat.risk_reward_ratio == 2.7
    assert strat.max_holding_bars == 22
    assert strat.pullback_tolerance_pct == 0.33
    assert strat.vwap_stop_offset_pct == 0.15
    assert strat.volume_multiplier == 1.8
    assert strat.vwap_slope_min == 0.0002

    # 2. StrategyParams -> PaperProfile params dict
    profile = PaperProfile(
        exchange="kucoin",
        symbol="ETH/USDT",
        timeframe="15m",
        strategy_mode="pullback",
        target_metric="calmar_ratio",
        params={
            "fast_ema": strat.fast_ema,
            "slow_ema": strat.slow_ema,
            "trend_ema": strat.trend_ema,
            "atr_multiplier": strat.atr_multiplier,
            "risk_reward_ratio": strat.risk_reward_ratio,
            "max_holding_bars": strat.max_holding_bars,
            "pullback_tolerance_pct": strat.pullback_tolerance_pct,
            "vwap_stop_offset_pct": strat.vwap_stop_offset_pct,
            "volume_multiplier": strat.volume_multiplier,
            "vwap_slope_min": strat.vwap_slope_min,
        },
    )

    # 3. PaperProfile -> StrategyParams (round-trip)
    roundtrip = profile.get_strategy_params()
    assert roundtrip.fast_ema == 11
    assert roundtrip.slow_ema == 28
    assert roundtrip.trend_ema == 67
    assert roundtrip.atr_multiplier == 3.5
    assert roundtrip.risk_reward_ratio == 2.7
    assert roundtrip.max_holding_bars == 22
    assert roundtrip.pullback_tolerance_pct == 0.33
    assert roundtrip.vwap_stop_offset_pct == 0.15
    assert roundtrip.volume_multiplier == 1.8
    assert roundtrip.vwap_slope_min == 0.0002

    print("✅ Parameter fidelity check passed across all transformations!")
