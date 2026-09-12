"""
Integration tests for FastAPI Web Backend Endpoints.
"""

import pytest
from fastapi.testclient import TestClient

import src.app as app_module
from src.app import app
from src.database import CandleDatabase

client = TestClient(app)


def test_batch_result_management_endpoints(tmp_path, monkeypatch):
    db = CandleDatabase(db_path=str(tmp_path / "batch.db"))
    db.save_batch_result(
        "kucoin",
        "BTC/USD",
        "5m",
        "auto",
        "sharpe_ratio",
        1.0,
        5,
        1.0,
        1.0,
        50.0,
        2.0,
        3.0,
        "{}",
        "2020-01-01T00:00:00+00:00",
    )
    monkeypatch.setattr(app_module, "_batch_db", db)

    invalid = client.request("DELETE", "/api/batch_results", json={"ids": []})
    assert invalid.status_code == 400

    result_id = db.load_batch_results(limit=1)[0]["id"]
    deleted = client.request("DELETE", "/api/batch_results", json={"ids": [result_id]})
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == 1

    db.save_batch_result(
        "kucoin",
        "ETH/USD",
        "5m",
        "auto",
        "sharpe_ratio",
        1.0,
        5,
        1.0,
        1.0,
        50.0,
        2.0,
        3.0,
        "{}",
        "2020-01-01T00:00:00+00:00",
    )
    purged = client.post(
        "/api/batch_results/purge",
        json={"older_than_days": 30, "target_metric": "sharpe_ratio"},
    )
    assert purged.status_code == 200
    assert purged.json()["deleted"] == 1


def test_batch_result_favorite_endpoint(tmp_path, monkeypatch):
    db = CandleDatabase(db_path=str(tmp_path / "batch_favorites.db"))
    db.save_batch_result(
        "kucoin",
        "BTC/USD",
        "5m",
        "auto",
        "sharpe_ratio",
        1.0,
        5,
        1.0,
        1.0,
        50.0,
        2.0,
        3.0,
        "{}",
        "2026-08-19T00:00:00+00:00",
    )
    monkeypatch.setattr(app_module, "_batch_db", db)
    result_id = db.load_batch_results(limit=1)[0]["id"]

    response = client.patch(
        f"/api/batch_results/{result_id}/favorite", json={"favorite": True}
    )
    assert response.status_code == 200
    assert response.json()["favorite"] is True
    assert db.load_batch_results(limit=1)[0]["favorite"] == 1

    # Test 404 for invalid result_id
    resp_404 = client.patch(
        "/api/batch_results/99999/favorite", json={"favorite": True}
    )
    assert resp_404.status_code == 404


def test_batch_result_validation_endpoint(tmp_path, monkeypatch):
    db = CandleDatabase(db_path=str(tmp_path / "batch_val_endpoint.db"))
    db.save_batch_result(
        "kucoin",
        "BTC/USD",
        "5m",
        "auto",
        "sharpe_ratio",
        1.5,
        8,
        1.5,
        1.2,
        60.0,
        4.0,
        12.0,
        "{}",
        "2026-08-20T00:00:00+00:00",
    )
    monkeypatch.setattr(app_module, "_batch_db", db)
    result_id = db.load_batch_results(limit=1)[0]["id"]

    response = client.patch(
        f"/api/batch_results/{result_id}/validation",
        json={
            "status": "ROBUST",
            "report_json": '{"status": "ROBUST", "overall_score": 90.0}',
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["validation_status"] == "ROBUST"

    # Verify 404 on non-existent result
    resp_404 = client.patch(
        "/api/batch_results/99999/validation",
        json={"status": "FAIL", "report_json": "{}"},
    )
    assert resp_404.status_code == 404


def test_get_exchanges():
    response = client.get("/api/exchanges")
    assert response.status_code == 200
    data = response.json()
    assert "exchanges" in data
    assert "timeframes" in data
    assert "strategy_modes" in data
    # Gemini should be listed in exchanges
    ex_ids = [ex["id"] for ex in data["exchanges"]]
    assert "gemini" in ex_ids
    assert "kucoin" in ex_ids


def test_run_backtest_endpoint():
    payload = {
        "exchange": "synthetic",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "limit": 200,
        "strategy_mode": "crossover",
        "fast_ema": 9,
        "slow_ema": 21,
        "trend_ema": 50,
        "vwap_slope_min": 0.0,
        "volume_filter_enabled": False,
        "stop_loss_type": "vwap",
        "risk_per_trade_pct": 1.0,
        "initial_capital": 10000.0,
    }

    response = client.post("/api/backtest", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "success"
    assert data["symbol"] == "BTC/USD"
    assert "metrics" in data
    assert "chart_data" in data
    assert len(data["chart_data"]) == 200
    assert "win_rate" in data["metrics"]


def test_run_optimize_endpoint():
    payload = {
        "exchange": "synthetic",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "limit": 200,
        "strategy_mode": "crossover",
        "target_metric": "sharpe_ratio",
        "n_trials": 3,
    }
    response = client.post("/api/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "results" in data
    assert "best_params" in data["results"]


def test_run_multi_objective_optimize_endpoint():
    payload = {
        "exchange": "synthetic",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "limit": 200,
        "strategy_mode": "crossover",
        "target_metric": "sharpe_ratio",
        "n_trials": 3,
        "enable_multi_objective": True,
        "multi_objective_metrics": ["sharpe_ratio", "max_drawdown_pct"],
    }
    response = client.post("/api/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["results"]["optimization_mode"] == "multi_objective"
    assert "pareto_objectives" in data["results"]


def test_run_walk_forward_endpoint():
    payload = {
        "exchange": "synthetic",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "limit": 200,
        "strategy_mode": "crossover",
        "num_windows": 2,
        "trials_per_window": 2,
    }
    response = client.post("/api/walk_forward", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "results" in data
    assert "walk_forward_efficiency_pct" in data["results"]


def test_run_monte_carlo_endpoint():
    payload = {
        "exchange": "synthetic",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "limit": 200,
        "strategy_mode": "crossover",
        "num_simulations": 50,
    }
    response = client.post("/api/monte_carlo", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "results" in data
    assert "risk_of_ruin_pct" in data["results"]


def test_deploy_paper_profile_endpoint(tmp_path, monkeypatch):
    from src.paper import PaperProfileRegistry

    db = CandleDatabase(db_path=str(tmp_path / "test_app_paper.db"))
    profile_reg = PaperProfileRegistry(db=db)
    monkeypatch.setattr(app_module, "_paper_profiles", profile_reg)

    payload = {
        "exchange": "kucoin",
        "symbol": "BTC/USDT",
        "timeframe": "5m",
        "strategy_mode": "crossover",
        "target_metric": "sharpe_ratio",
        "params": {"fast_ema": 12, "slow_ema": 26, "risk_per_trade_pct": 2.5},
        "optuna_score": 3.14,
        "is_active": True,
    }

    # Deploy endpoint
    resp = client.post("/api/paper/profiles/deploy", json=payload)
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["status"] == "success"
    assert res_data["profile"]["symbol"] == "BTC/USDT"
    assert res_data["profile"]["params"]["fast_ema"] == 12

    # Get by symbol
    get_resp = client.get("/api/paper/profiles/kucoin/BTC/USDT")
    assert get_resp.status_code == 200
    assert get_resp.json()["profile"]["optuna_score"] == 3.14

    # List profiles
    list_resp = client.get("/api/paper/profiles")
    assert list_resp.status_code == 200
    assert list_resp.json()["count"] == 1

    profile_id = res_data["profile"]["id"]

    # Toggle active
    toggle_resp = client.post(
        f"/api/paper/profiles/{profile_id}/toggle-active", json={"is_active": False}
    )
    assert toggle_resp.status_code == 200
    assert toggle_resp.json()["is_active"] is False

    # Delete profile
    del_resp = client.delete(f"/api/paper/profiles/{profile_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    # Check 404 after deletion
    not_found = client.get("/api/paper/profiles/kucoin/BTC/USDT")
    assert not_found.status_code == 404


def test_paper_engine_and_ledger_endpoints(tmp_path, monkeypatch):
    from src.paper import (
        PaperLedger,
        PaperPosition,
        PaperProfileRegistry,
        PaperTradingEngine,
    )

    db = CandleDatabase(db_path=str(tmp_path / "test_app_paper_engine.db"))
    profile_reg = PaperProfileRegistry(db=db)
    ledger = PaperLedger(db=db)
    engine = PaperTradingEngine(db=db)

    monkeypatch.setattr(app_module, "_paper_profiles", profile_reg)
    monkeypatch.setattr(app_module, "_paper_ledger", ledger)
    monkeypatch.setattr(app_module, "_paper_engine", engine)

    # Engine status
    status_resp = client.get("/api/paper/engine/status")
    assert status_resp.status_code == 200
    assert "cash_balance" in status_resp.json()

    # Step engine
    step_resp = client.post(
        "/api/paper/engine/step",
        json={"exchange": "synthetic", "symbol": "BTC/USD", "timeframe": "5m"},
    )
    assert step_resp.status_code == 200
    assert step_resp.json()["status"] == "success"

    # Start / Stop polling
    start_resp = client.post("/api/paper/engine/start", json={"interval_seconds": 30})
    assert start_resp.status_code == 200
    assert start_resp.json()["is_running"] is True

    stop_resp = client.post("/api/paper/engine/stop")
    assert stop_resp.status_code == 200
    assert stop_resp.json()["is_running"] is False

    # Reconcile endpoint
    rec_resp = client.post("/api/paper/engine/reconcile")
    assert rec_resp.status_code == 200
    assert rec_resp.json()["status"] == "success"

    # Pre-populate a position and test endpoints
    pos = PaperPosition(
        exchange="kucoin",
        symbol="BTC/USDT",
        side="LONG",
        entry_price=60000.0,
        current_price=62000.0,
        quantity=0.1,
        cost_basis=6000.0,
        unrealized_pnl=200.0,
        unrealized_pnl_pct=3.33,
    )
    ledger.save_position(pos)

    # Positions list
    pos_resp = client.get("/api/paper/positions")
    assert pos_resp.status_code == 200
    assert pos_resp.json()["count"] == 1

    # Close position manual
    close_resp = client.post("/api/paper/positions/kucoin/BTC/USDT/close")
    assert close_resp.status_code == 200
    assert close_resp.json()["status"] == "success"

    # Transactions list
    tx_resp = client.get("/api/paper/ledger/transactions")
    assert tx_resp.status_code == 200
    assert tx_resp.json()["count"] >= 1

    # Trade history
    trades_resp = client.get("/api/paper/trades/history")
    assert trades_resp.status_code == 200
    assert trades_resp.json()["count"] >= 1

    # Performance
    perf_resp = client.get("/api/paper/performance")
    assert perf_resp.status_code == 200
    assert "cash_balance" in perf_resp.json()
    assert "total_equity" in perf_resp.json()


@pytest.mark.asyncio
async def test_app_lifespan_auto_resumes_runner(tmp_path, monkeypatch):
    from src.app import lifespan
    from src.database import CandleDatabase
    from src.paper import PaperTradingEngine
    from src.settings import SettingsManager

    db = CandleDatabase(db_path=str(tmp_path / "test_app_lifespan.db"))
    settings_mgr = SettingsManager(db)
    engine = PaperTradingEngine(db=db)

    # Pre-configure runner to be enabled in settings
    settings_mgr.set("paper_runner_enabled", "true")
    settings_mgr.set("paper_polling_interval", "10")

    monkeypatch.setattr(app_module, "_batch_db", db)
    monkeypatch.setattr(app_module, "_settings_mgr", settings_mgr)
    monkeypatch.setattr(app_module, "_paper_engine", engine)

    # Run lifespan context
    async with lifespan(app):
        assert engine.is_running is True

    # After exit (shutdown), engine should be stopped
    assert engine.is_running is False


def test_validate_strategy_endpoint(tmp_path):
    resp = client.post(
        "/api/strategy/validate",
        json={
            "exchange": "synthetic",
            "symbol": "BTC/USD",
            "timeframe": "5m",
            "limit": 100,
            "params": {"fast_ema": 9, "slow_ema": 21, "strategy_mode": "crossover"},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "report" in data
    assert data["report"]["total_gates"] == 3
    assert "gate_results" in data["report"]
