"""Gap tests for paper trading, alerts, and database repair.

Updated for PostgreSQL: tests requiring PG use the pg_container fixture.
SQLite-only tests (repair_database on missing file, DB_MAP manipulation)
have been removed or adapted.
"""

from datetime import UTC

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.database import CandleDatabase
from src.paper.ledger import PaperLedger
from src.paper.models import PaperPosition


class TestPaperEquityEdgeCases:
    """Test get_total_equity edge cases not covered by l7v epic."""

    @pytest.mark.asyncio
    async def test_get_total_equity_empty_account(self, pg_container):
        """Equity on account with no positions equals cash balance."""
        _ = CandleDatabase(domain="paper_trading")
        ledger = PaperLedger()
        await ledger.deposit(account_id="empty_account", amount=10000.0)
        equity = await ledger.get_total_equity(account_id="empty_account")
        assert equity == 10000.0  # deposit 10000 (no implicit default in PG)

    @pytest.mark.asyncio
    async def test_get_total_equity_mixed_long_short(self, pg_container):
        """Equity correctly sums long and short positions."""
        _ = CandleDatabase(domain="paper_trading")
        ledger = PaperLedger()
        # Deposit cash first
        await ledger.deposit(account_id="mixed", amount=10000.0)

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
        await ledger.save_position(pos_long)

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
        await ledger.save_position(pos_short)
        equity = await ledger.get_total_equity(account_id="mixed")
        # Cash: 10000 (deposit)
        # Long position_value: cost_basis + unrealized_pnl = 50000 + 0 = 50000
        # Short position_value: cost_basis + unrealized_pnl = 3000 + 0 = 3000
        # Total: 10000 + 50000 + 3000 = 63000
        expected = 10000.0 + 50000.0 + 3000.0
        assert abs(equity - expected) < 0.01

    @pytest.mark.asyncio
    async def test_get_total_equity_scoped_isolation(self, pg_container):
        """Equity for account A does not include account B positions."""
        _ = CandleDatabase(domain="paper_trading")
        ledger = PaperLedger()
        # Deposit cash for both accounts
        await ledger.deposit(account_id="A", amount=10000.0)
        await ledger.deposit(account_id="B", amount=10000.0)

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
        await ledger.save_position(pos)
        equity_a = await ledger.get_total_equity(account_id="A")
        equity_b = await ledger.get_total_equity(account_id="B")
        # A has position, B does not
        assert equity_a > 10000.0
        assert equity_b == 10000.0  # deposit only


class TestRepairDatabaseEdgeCases:
    """Test repair_database on various corruption scenarios."""

    def test_repair_database_all_healthy(self, pg_container):
        """repair_database_all() returns success on healthy PG domains."""
        from src.db_maintenance_multi import repair_database_all

        result = repair_database_all()
        assert result["status"] == "success"
        assert result["cross_db_consistent"] is True


class TestAlertAPILifecycle:
    """Test alert acknowledgment and deletion via API."""

    def test_acknowledge_alert_via_api(self, pg_container, auth_headers):
        """POST /api/alerts/{id}/acknowledge marks alert acknowledged."""
        from datetime import datetime

        db = CandleDatabase(domain="system")
        # Use a recent timestamp so it appears in the top 10 results (ordered by created_at DESC)
        recent_time = datetime.now(UTC).isoformat()
        db.save_alert(
            {
                "alert_type": "system_health",
                "severity": "INFO",
                "message": "test alert",
                "created_at": recent_time,
            }
        )
        alerts = db.get_alerts(limit=1)
        alert_id = alerts[0]["id"]

        client = TestClient(app)
        resp = client.post(f"/api/alerts/{alert_id}/acknowledge", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

        # Verify via get_alerts
        alerts = db.get_alerts(limit=10)
        matching = [a for a in alerts if a["id"] == alert_id]
        assert len(matching) == 1, f"Alert {alert_id} not found in {alerts}"
        assert matching[0]["acknowledged"] == 1
        assert matching[0]["acknowledged_at"] is not None

    def test_delete_alert_via_api(self, pg_container, auth_headers):
        """DELETE /api/alerts/{id} removes alert."""
        db = CandleDatabase(domain="system")
        # Directly insert an alert into PG
        db.save_alert(
            {
                "alert_type": "system_health",
                "severity": "INFO",
                "message": "to delete",
                "created_at": "2026-01-01T12:00:00Z",
            }
        )
        alerts = db.get_alerts(limit=1)
        alert_id = alerts[0]["id"]

        client = TestClient(app)
        resp = client.delete(f"/api/alerts/{alert_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

        alerts = db.get_alerts(limit=10)
        assert not any(a["id"] == alert_id for a in alerts)


class TestPaperProfileDeployedFlag:
    """Test paper profile deployed flag lifecycle."""

    @pytest.mark.asyncio
    async def test_profile_deployed_flag_persists(self, pg_container):
        """Save and retrieve profile with deployed=True."""
        from src.paper.profiles import PaperProfileRegistry

        registry = PaperProfileRegistry()

        await registry.save_profile(
            exchange="kucoin",
            symbol="BTC/USD",
            timeframe="5m",
            strategy_mode="crossover",
            target_metric="sharpe_ratio",
            params={"fast_ema": 10, "slow_ema": 30},
            optuna_score=1.5,
            is_active=True,
            is_deployed=True,
        )

        profiles = await registry.list_profiles(deployed_only=True)
        assert len(profiles) == 1
        assert profiles[0].deployed is True

    @pytest.mark.asyncio
    async def test_toggle_deployed_via_set_active(self, pg_container):
        """set_active(deployed=True) updates deployed flag."""
        from src.paper.profiles import PaperProfileRegistry

        registry = PaperProfileRegistry()

        profile_id = await registry.save_profile(
            exchange="kucoin",
            symbol="BTC/USD",
            timeframe="5m",
            strategy_mode="crossover",
            target_metric="sharpe_ratio",
            params={"fast_ema": 10, "slow_ema": 30},
            is_active=True,
        )

        # Deploy it
        await registry.set_active(profile_id, is_active=True, deployed=True)
        profiles = await registry.list_profiles(deployed_only=True)
        assert len(profiles) == 1
        assert profiles[0].deployed is True

        # Undeploy
        await registry.set_active(profile_id, is_active=True, deployed=False)
        profiles = await registry.list_profiles(deployed_only=True)
        assert len(profiles) == 0


class TestBatchOptimizeAllCombinations:
    """Test batch optimize all_combinations multi-objective path."""

    def test_all_combinations_returns_total_combos(
        self, tmp_path, monkeypatch, auth_headers
    ):
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

        resp = client.post("/api/batch_optimize", json=payload, headers=auth_headers)

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
