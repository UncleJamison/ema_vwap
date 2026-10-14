"""Gap tests for paper trading, alerts, and database repair."""

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.database import CandleDatabase
from src.paper.ledger import PaperLedger
from src.paper.models import PaperPosition


class TestPaperEquityEdgeCases:
    """Test get_total_equity edge cases not covered by l7v epic."""

    def test_get_total_equity_empty_account(self, tmp_path):
        """Equity on account with no positions equals cash balance."""
        db = CandleDatabase(db_path=str(tmp_path / "test_equity.db"))
        ledger = PaperLedger(db=db)
        ledger.deposit(account_id="empty_account", amount=10000.0)
        equity = ledger.get_total_equity(account_id="empty_account")
        assert equity == 20000.0  # default 10000 + deposit 10000

    def test_get_total_equity_mixed_long_short(self, tmp_path):
        """Equity correctly sums long and short positions."""
        db = CandleDatabase(db_path=str(tmp_path / "test_mixed.db"))
        ledger = PaperLedger(db=db)
        # Default balance is 10000

        # Long position: +1 BTC at 50000, current 51000
        pos_long = PaperPosition(
            exchange="binance",
            symbol="BTC/USDT",
            side="LONG",
            entry_price=50000.0,
            current_price=51000.0,
            quantity=1.0,
            cost_basis=50000.0,
            entry_time="2024-01-01T00:00:00Z",
            account_id="mixed",
        )
        ledger.save_position(pos_long)

        # Short position: -1 ETH at 3000, current 2900
        pos_short = PaperPosition(
            exchange="binance",
            symbol="ETH/USDT",
            side="SHORT",
            entry_price=3000.0,
            current_price=2900.0,
            quantity=1.0,
            cost_basis=3000.0,
            entry_time="2024-01-01T00:00:00Z",
            account_id="mixed",
        )
        ledger.save_position(pos_short)

        equity = ledger.get_total_equity(account_id="mixed")
        # Cash: 10000 (default)
        # Long position_value: cost_basis + unrealized_pnl = 50000 + 0 = 50000
        # Short position_value: cost_basis + unrealized_pnl = 3000 + 0 = 3000
        # Total: 10000 + 50000 + 3000 = 63000
        expected = 10000.0 + 50000.0 + 3000.0
        assert abs(equity - expected) < 0.01

    def test_get_total_equity_scoped_isolation(self, tmp_path):
        """Equity for account A does not include account B positions."""
        db = CandleDatabase(db_path=str(tmp_path / "test_scope.db"))
        ledger = PaperLedger(db=db)
        # Default balance is 10000 for each account

        pos = PaperPosition(
            exchange="binance",
            symbol="BTC/USDT",
            side="LONG",
            entry_price=50000.0,
            current_price=51000.0,
            quantity=1.0,
            cost_basis=50000.0,
            entry_time="2024-01-01T00:00:00Z",
            account_id="A",
        )
        ledger.save_position(pos)

        equity_a = ledger.get_total_equity(account_id="A")
        equity_b = ledger.get_total_equity(account_id="B")
        # A has position, B does not
        assert equity_a > 10000.0
        assert equity_b == 10000.0  # default balance


class TestRepairDatabaseEdgeCases:
    """Test repair_database on various corruption scenarios."""

    def test_repair_database_no_file(self, tmp_path):
        """Repair on non-existent file returns no_file status."""
        # Create DB path but don't initialize (avoid init_db creating the file)
        db_path = str(tmp_path / "nonexistent.db")
        db = CandleDatabase.__new__(CandleDatabase)
        db.db_path = db_path
        db.init_db = lambda: None  # skip init_db
        result = db.repair_database()
        assert result["status"] == "no_file"


class TestAlertAPILifecycle:
    """Test alert acknowledgment and deletion via API."""

    def test_acknowledge_alert_via_api(self, tmp_path):
        """POST /api/alerts/{id}/acknowledge marks alert acknowledged."""
        import src.app as app_module
        import src.db_connection_manager as db_mgr
        from src.alerting import AlertManager

        # Use a test database for system by overriding DB_MAP
        db_path = str(tmp_path / "alerts.db")
        original_db_map = db_mgr.DB_MAP.copy()
        db_mgr.DB_MAP["system"] = db_path

        try:
            # Create alert via AlertManager
            mgr = AlertManager()
            mgr.send("system_health", "test alert", level="INFO")
            # Get id from database after persistence
            db = CandleDatabase(db_path=db_path)
            alerts = db.get_alerts(limit=1)
            alert_id = alerts[0]["id"]

            # Also override app's system db
            original_app_db = app_module._system_db
            app_module._system_db = db

            client = TestClient(app)
            resp = client.post(f"/api/alerts/{alert_id}/acknowledge")
            assert resp.status_code == 200
            assert resp.json()["status"] == "success"

            # Verify via get_alerts
            alerts = db.get_alerts(limit=10)
            ack_alert = next(a for a in alerts if a["id"] == alert_id)
            assert ack_alert["acknowledged"] == 1
            assert ack_alert["acknowledged_at"] is not None
        finally:
            db_mgr.DB_MAP = original_db_map
            app_module._system_db = original_app_db

    def test_delete_alert_via_api(self, tmp_path):
        """DELETE /api/alerts/{id} removes alert."""
        import src.app as app_module
        import src.db_connection_manager as db_mgr
        from src.alerting import AlertManager

        db_path = str(tmp_path / "alerts2.db")
        original_db_map = db_mgr.DB_MAP.copy()
        db_mgr.DB_MAP["system"] = db_path

        try:
            mgr = AlertManager()
            mgr.send("system_health", "to delete", level="INFO")
            # Get id from database after persistence
            db = CandleDatabase(db_path=db_path)
            alerts = db.get_alerts(limit=1)
            alert_id = alerts[0]["id"]

            original_app_db = app_module._system_db
            app_module._system_db = db

            client = TestClient(app)
            resp = client.delete(f"/api/alerts/{alert_id}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "success"

            alerts = db.get_alerts(limit=10)
            assert not any(a["id"] == alert_id for a in alerts)
        finally:
            db_mgr.DB_MAP = original_db_map
            app_module._system_db = original_app_db


class TestPaperProfileDeployedFlag:
    """Test paper profile deployed flag lifecycle."""

    def test_profile_deployed_flag_persists(self, tmp_path):
        """Save and retrieve profile with deployed=True."""
        db = CandleDatabase(db_path=str(tmp_path / "deployed.db"))
        from src.paper.profiles import PaperProfileRegistry

        registry = PaperProfileRegistry(db)

        registry.save_profile(
            exchange="kucoin",
            symbol="BTC/USDT",
            timeframe="5m",
            strategy_mode="crossover",
            target_metric="sharpe_ratio",
            params={"fast_ema": 10, "slow_ema": 30},
            optuna_score=1.5,
            is_active=True,
            deployed=True,
        )

        profiles = registry.list_profiles(deployed_only=True)
        assert len(profiles) == 1
        assert profiles[0].deployed is True
        assert profiles[0].deployed_at is not None

    def test_toggle_deployed_via_set_active(self, tmp_path):
        """set_active(deployed=True) updates deployed flag."""
        db = CandleDatabase(db_path=str(tmp_path / "deployed2.db"))
        from src.paper.profiles import PaperProfileRegistry

        registry = PaperProfileRegistry(db)

        profile_id = registry.save_profile(
            exchange="kucoin",
            symbol="BTC/USDT",
            timeframe="5m",
            strategy_mode="crossover",
            target_metric="sharpe_ratio",
            params={"fast_ema": 10, "slow_ema": 30},
            is_active=True,
        )

        # Deploy it
        registry.set_active(profile_id, is_active=True, deployed=True)
        profiles = registry.list_profiles(deployed_only=True)
        assert len(profiles) == 1
        assert profiles[0].deployed is True

        # Undeploy
        registry.set_active(profile_id, is_active=True, deployed=False)
        profiles = registry.list_profiles(deployed_only=True)
        assert len(profiles) == 0


class TestBatchOptimizeAllCombinations:
    """Test batch optimize all_combinations multi-objective path."""

    def test_all_combinations_returns_total_combos(self, tmp_path, monkeypatch):
        """all_combinations target returns correct total_combos."""
        client = TestClient(app)
        from unittest.mock import MagicMock

        mock_add_task = MagicMock()
        monkeypatch.setattr(
            "starlette.background.BackgroundTasks.add_task", mock_add_task
        )

        # Disable the shared limiter and clear counters so prior tests don't
        # exhaust the 5/min budget for /api/batch_optimize in this process.
        from src.app_limiter import limiter

        limiter.enabled = False
        limiter.reset()

        # Reset the global batch state so a still-"running" sweep from
        # another test doesn't 409 this request.
        from src.batch_optimizer import _update_state

        _update_state(
            running=False,
            cancelled=False,
            done=0,
            total=0,
            current_combo="",
            combo_progress=0.0,
            error=None,
        )

        payload = {
            "symbols": ["BTC/USD", "ETH/USD"],
            "timeframes": ["15m"],
            "exchange": "kucoin",
            "target_metric": "all_metrics",
            "n_trials": 5,
        }

        resp = client.post("/api/batch_optimize", json=payload)

        limiter.enabled = True
        limiter.reset()
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        from src.batch_optimizer import ALL_SINGLE_METRICS

        expected_combos = 2 * 1 * len(ALL_SINGLE_METRICS)
        assert data["total_combos"] == expected_combos
        assert data["total_metrics"] == len(ALL_SINGLE_METRICS)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
