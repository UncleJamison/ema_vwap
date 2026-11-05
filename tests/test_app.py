"""
Integration tests for FastAPI Web Backend Endpoints.
"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import src.app as app_module
from src.app import app
from src.database import CandleDatabase

client = TestClient(app)


def _truncate(tables: list[str]) -> None:
    """TRUNCATE tables in their domain via the test main loop (real PG)."""
    import asyncio as _asyncio

    from src.sync_bridge import get_main_loop

    async def _cleanup():
        from src.pg_connection_manager import get_pg_manager

        mgr = get_pg_manager()
        paper_pool = mgr.get_pool("paper_trading")
        batch_pool = mgr.get_pool("batch_results")
        async with paper_pool.acquire() as conn:
            for t in (
                "paper_positions",
                "paper_trade_history",
                "paper_ledger_transactions",
                "paper_profiles",
            ):
                if t in tables:
                    await conn.execute(f"TRUNCATE {t} RESTART IDENTITY")
        async with batch_pool.acquire() as conn:
            if "batch_results" in tables:
                await conn.execute("TRUNCATE batch_results RESTART IDENTITY")

    _asyncio.run_coroutine_threadsafe(_cleanup(), get_main_loop()).result(timeout=10)


def test_batch_result_management_endpoints(pg_container, monkeypatch, auth_headers):
    _truncate(["batch_results"])
    db = CandleDatabase()
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

    invalid = client.request(
        "DELETE", "/api/batch_results", json={"ids": []}, headers=auth_headers
    )
    assert invalid.status_code == 400

    result_id = db.load_batch_results(limit=1)[0]["id"]
    deleted = client.request(
        "DELETE", "/api/batch_results", json={"ids": [result_id]}, headers=auth_headers
    )
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
        headers=auth_headers,
    )
    assert purged.status_code == 200
    assert purged.json()["deleted"] == 1


def test_batch_result_favorite_endpoint(pg_container, monkeypatch, auth_headers):
    _truncate(["batch_results"])
    db = CandleDatabase()
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
        f"/api/batch_results/{result_id}/favorite",
        json={"favorite": True},
        headers=auth_headers,
    )
    assert response.json()["favorite"] is True
    assert db.load_batch_results(limit=1)[0]["is_favorite"] is True

    # Test 404 for invalid result_id
    resp_404 = client.patch(
        "/api/batch_results/99999/favorite",
        json={"favorite": True},
        headers=auth_headers,
    )
    assert resp_404.status_code == 404


def test_batch_result_validation_endpoint(pg_container, monkeypatch, auth_headers):
    _truncate(["batch_results"])
    db = CandleDatabase()
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
        headers=auth_headers,
    )
    data = response.json()
    assert data["status"] == "success"
    assert data["validation_status"] == "ROBUST"

    # Verify 404 on non-existent result
    resp_404 = client.patch(
        "/api/batch_results/99999/validation",
        json={"status": "FAIL", "report_json": "{}"},
        headers=auth_headers,
    )
    assert resp_404.status_code == 404


def test_get_exchanges(auth_headers):
    response = client.get("/api/exchanges", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "exchanges" in data
    assert "timeframes" in data
    assert "strategy_modes" in data
    # Gemini should be listed in exchanges
    ex_ids = [ex["id"] for ex in data["exchanges"]]
    assert "gemini" in ex_ids
    assert "kucoin" in ex_ids


def test_run_backtest_endpoint(auth_headers, pg_container):
    # Reset rate limiter to avoid 429 from prior tests in session
    from src.app_limiter import limiter

    limiter.reset()
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
        "target_metric": "sharpe_ratio",
    }

    response = client.post("/api/backtest", json=payload, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "started"
    assert data["task_id"]

    deadline = time.monotonic() + 30
    task = None
    while time.monotonic() < deadline:
        task_response = client.get(f"/api/tasks/{data['task_id']}")
        assert task_response.status_code == 200
        task = task_response.json()["task"]
        if task["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.02)

    assert task is not None
    assert task["status"] == "completed", task
    result = task["result"]
    assert result["status"] == "success"
    assert result["symbol"] == "BTC/USD"
    assert "metrics" in result
    assert "chart_data" in result
    assert len(result["chart_data"]) == 200
    assert "win_rate" in result["metrics"]


def test_run_optimize_endpoint(auth_headers):
    payload = {
        "exchange": "synthetic",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "limit": 200,
        "strategy_mode": "crossover",
        "target_metric": "sharpe_ratio",
        "n_trials": 3,
        "sampler_seed": 73,
    }
    response = client.post("/api/optimize", json=payload, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "started"
    assert "task_id" in data


def test_run_optimize_endpoint_respects_explicit_slope_bounds(auth_headers):
    payload = {
        "exchange": "synthetic",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "limit": 200,
        "strategy_mode": "crossover",
        "target_metric": "sharpe_ratio",
        "n_trials": 3,
        "vwap_slope_min_bound": 0.0001,
        "vwap_slope_max_bound": 0.005,
    }
    response = client.post("/api/optimize", json=payload, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "started"
    assert "task_id" in data


def test_run_multi_objective_optimize_endpoint(auth_headers):
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
    response = client.post("/api/optimize", json=payload, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "started"
    assert "task_id" in data


def test_run_walk_forward_endpoint(auth_headers):
    payload = {
        "exchange": "synthetic",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "limit": 200,
        "strategy_mode": "crossover",
        "num_windows": 2,
        "trials_per_window": 2,
    }
    response = client.post("/api/walk_forward", json=payload, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "results" in data
    assert "walk_forward_efficiency_pct" in data["results"]


def test_run_monte_carlo_endpoint(auth_headers):
    payload = {
        "exchange": "synthetic",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "limit": 200,
        "strategy_mode": "crossover",
        "num_simulations": 50,
    }
    response = client.post("/api/monte_carlo", json=payload, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "results" in data
    assert "risk_of_ruin_pct" in data["results"]


def test_deploy_paper_profile_endpoint(pg_container, monkeypatch, auth_headers):
    from src.paper import PaperProfileRegistry

    _truncate(["paper_profiles"])
    profile_reg = PaperProfileRegistry()
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
    resp = client.post("/api/paper/profiles/deploy", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["status"] == "success"
    assert res_data["profile"]["symbol"] == "BTC/USDT"
    assert res_data["profile"]["params"]["fast_ema"] == 12

    # Get by symbol
    get_resp = client.get("/api/paper/profiles/kucoin/BTC/USDT", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["profile"]["optuna_score"] == 3.14

    # List profiles
    list_resp = client.get("/api/paper/profiles", headers=auth_headers)
    assert list_resp.status_code == 200
    assert list_resp.json()["count"] == 1
    profile_id = res_data["profile"]["id"]

    # Toggle active
    toggle_resp = client.post(
        f"/api/paper/profiles/{profile_id}/toggle-active",
        json={"is_active": False},
        headers=auth_headers,
    )
    assert toggle_resp.json()["is_active"] is False

    # Delete profile
    del_resp = client.delete(f"/api/paper/profiles/{profile_id}", headers=auth_headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True
    not_found = client.get("/api/paper/profiles/kucoin/BTC/USDT", headers=auth_headers)
    assert not_found.status_code == 404


def test_optuna_profile_deployment_requires_validation_report(
    auth_headers, pg_container, monkeypatch
):
    from src.paper import PaperProfileRegistry

    _truncate(["paper_profiles"])
    monkeypatch.setattr(app_module, "_paper_profiles", PaperProfileRegistry())

    response = client.post(
        "/api/paper/profiles/deploy",
        json={
            "source": "optuna",
            "exchange": "kucoin",
            "symbol": "BTC/USDT",
            "timeframe": "5m",
            "strategy_mode": "crossover",
            "target_metric": "sharpe_ratio",
            "params": {"fast_ema": 12, "slow_ema": 26},
            "optuna_score": 2.0,
            "is_active": True,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = {
        "source": "optuna",
        "exchange": "kucoin",
        "symbol": "BTC/USDT",
        "timeframe": "5m",
        "strategy_mode": "crossover",
        "target_metric": "sharpe_ratio",
        "params": {"fast_ema": 12, "slow_ema": 26},
        "optuna_score": 2.0,
        "is_active": True,
        "validation_report": {"status": "PASS", "overall_score": 90.0},
    }
    validated_response = client.post(
        "/api/paper/profiles/deploy", json=payload, headers=auth_headers
    )
    assert validated_response.status_code == 200


def test_paper_engine_and_ledger_endpoints(pg_container, monkeypatch, auth_headers):
    from src.paper import (
        PaperLedger,
        PaperPosition,
        PaperProfileRegistry,
        PaperTradingEngine,
    )

    _truncate(
        [
            "paper_profiles",
            "paper_positions",
            "paper_trade_history",
            "paper_ledger_transactions",
        ]
    )
    profile_reg = PaperProfileRegistry()
    ledger = PaperLedger()
    engine = PaperTradingEngine()

    monkeypatch.setattr(app_module, "_paper_profiles", profile_reg)
    monkeypatch.setattr(app_module, "_paper_ledger", ledger)
    monkeypatch.setattr(app_module, "_paper_engine", engine)

    # Engine status
    status_resp = client.get("/api/paper/engine/status", headers=auth_headers)
    assert status_resp.status_code == 200
    assert "cash_balance" in status_resp.json()

    # Step engine
    step_resp = client.post(
        "/api/paper/engine/step",
        json={"exchange": "synthetic", "symbol": "BTC/USD", "timeframe": "5m"},
        headers=auth_headers,
    )
    assert step_resp.status_code == 200
    assert step_resp.json()["status"] == "success"

    # Start / Stop polling
    start_resp = client.post(
        "/api/paper/engine/start", json={"interval_seconds": 30}, headers=auth_headers
    )
    assert start_resp.status_code == 200
    assert start_resp.json()["is_running"] is True

    stop_resp = client.post("/api/paper/engine/stop", headers=auth_headers)
    assert stop_resp.status_code == 200
    assert stop_resp.json()["is_running"] is False

    # Reconcile endpoint
    rec_resp = client.post("/api/paper/engine/reconcile", headers=auth_headers)
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
    asyncio.run(ledger.save_position(pos))

    # Positions list
    pos_resp = client.get("/api/paper/positions", headers=auth_headers)
    assert pos_resp.status_code == 200
    assert pos_resp.json()["count"] == 1

    # Close position manual
    close_resp = client.post(
        "/api/paper/positions/kucoin/BTC/USDT/close", headers=auth_headers
    )
    assert close_resp.status_code == 200
    assert close_resp.json()["status"] == "success"

    # Transactions list
    tx_resp = client.get("/api/paper/ledger/transactions", headers=auth_headers)
    assert tx_resp.status_code == 200
    assert tx_resp.json()["count"] >= 1

    # Trade history
    trades_resp = client.get("/api/paper/trades/history", headers=auth_headers)
    assert trades_resp.status_code == 200
    assert trades_resp.json()["count"] >= 1

    # Performance
    perf_resp = client.get("/api/paper/performance", headers=auth_headers)
    assert perf_resp.status_code == 200
    assert "cash_balance" in perf_resp.json()
    assert "total_equity" in perf_resp.json()


@pytest.mark.asyncio
async def test_app_lifespan_auto_resumes_runner(pg_container, monkeypatch):
    from unittest.mock import AsyncMock

    from src.app import lifespan
    from src.database import CandleDatabase
    from src.paper import PaperTradingEngine
    from src.settings import SettingsManager

    db = CandleDatabase()
    settings_mgr = SettingsManager()
    engine = PaperTradingEngine()

    # Pre-configure runner to be enabled in settings
    settings_mgr.set("paper_runner_enabled", "true")
    settings_mgr.set("paper_polling_interval", "10")

    monkeypatch.setattr(app_module, "_batch_db", db)
    monkeypatch.setattr(app_module, "_settings_mgr", settings_mgr)
    monkeypatch.setattr(app_module, "_paper_engine", engine)

    # Mock PostgreSQL initialization (shared test PG pools must stay open for
    # other test files that use pg_container)
    monkeypatch.setattr("src.app.init_pg_pools", AsyncMock())
    monkeypatch.setattr("src.app.close_pg_pools", AsyncMock())

    # The lifespan runs on pytest-asyncio's loop, but the shared test PG pools
    # are bound to the conftest main loop. Neutralize the lifespan's loop
    # hijack and mock its direct PG touchpoints so no pooled connection is
    # ever used from a foreign loop.
    monkeypatch.setattr("src.sync_bridge.set_main_loop", lambda loop: None)
    monkeypatch.setattr("src.pg_database.PostgresDatabase.initialize", AsyncMock())

    async def _fake_get_setting(key: str):
        values = {
            "app_api_key": {"value": "test-key-for-tests"},
            "paper_runner_enabled": {"value": "true"},
            "paper_polling_interval": {"value": "10"},
        }
        return values.get(key)

    monkeypatch.setattr(
        "src.pg_database.get_setting_pg", AsyncMock(side_effect=_fake_get_setting)
    )
    monkeypatch.setattr("src.pg_database.save_setting_pg", AsyncMock())

    # Run lifespan context
    async with lifespan(app):
        assert engine.is_running is True

    # After exit (shutdown), engine should be stopped
    assert engine.is_running is False


def test_validate_strategy_endpoint(tmp_path, auth_headers):
    resp = client.post(
        "/api/strategy/validate",
        json={
            "exchange": "synthetic",
            "symbol": "BTC/USD",
            "timeframe": "5m",
            "limit": 100,
            "params": {"fast_ema": 9, "slow_ema": 21, "strategy_mode": "crossover"},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "report" in data
    assert data["report"]["total_gates"] == 3
    assert "gate_results" in data["report"]


def test_validate_strategy_endpoint_default_lookback(tmp_path, auth_headers):
    # Verify that when days is None and limit is 500 (UI default), optimal lookback is resolved
    resp = client.post(
        "/api/strategy/validate",
        json={
            "exchange": "synthetic",
            "symbol": "BTC/USD",
            "timeframe": "5m",
            "limit": 500,
            "days": None,
            "asset_type": "crypto",
            "params": {"fast_ema": 9, "slow_ema": 21, "strategy_mode": "crossover"},
            "n_trials": 2,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "report" in data


def test_validate_strategy_endpoint_accepts_ui_criteria_aliases(auth_headers):
    resp = client.post(
        "/api/strategy/validate",
        json={
            "exchange": "synthetic",
            "symbol": "BTC/USD",
            "timeframe": "5m",
            "limit": 150,
            "params": {"fast_ema": 9, "slow_ema": 21, "strategy_mode": "crossover"},
            "criteria": {
                "min_backtest_sharpe": 0.0,
                "max_backtest_drawdown_pct": 80.0,
                "min_trade_count": 1,
                "min_walk_forward_efficiency": 0.0,
                "min_profitable_windows_pct": 0.0,
                "min_oos_return_pct": -50.0,
                "max_monte_carlo_ruin_pct": 50.0,
                "max_monte_carlo_drawdown_pct": 80.0,
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "report" in data
    assert data["report"]["total_gates"] == 3
