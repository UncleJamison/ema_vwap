"""
Tests for PostgreSQL database layer traceability.

These tests were originally written for the SQLite CandleDatabase with alerts
table DDL, legacy schema migration, and repair_database(). After the migration
to PostgreSQL, equivalent functionality lives in:
  - src.db_connection_manager.get_db_info()
  - src.db_maintenance_multi.repair_database_all()
  - src.pg_database.save_alert_pg() / get_alerts_pg()
"""

from datetime import UTC

from src.database import CandleDatabase
from src.db_connection_manager import get_db_info
from src.db_maintenance_multi import repair_database_all

# ---------------------------------------------------------------------------
# Alerts table — requires real PostgreSQL (pg_container)
# ---------------------------------------------------------------------------


def test_alerts_table_exists(pg_container):
    """Verify the alerts table exists in the system domain."""
    db = CandleDatabase(domain="system")
    # save_alert + get_alerts exercises the alerts table
    db.save_alert(
        {
            "alert_type": "system.startup",
            "severity": "INFO",
            "message": "Traceability test alert",
        }
    )
    alerts = db.get_alerts(limit=1)
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "system.startup"


def test_alert_lifecycle(pg_container):
    """Test save_alert and get_alerts through PG."""
    db = CandleDatabase(domain="system")

    # Save alert using modern keys - use recent timestamp so it appears in top 10
    from datetime import datetime

    recent_time = datetime.now(UTC).isoformat()
    entry = {
        "alert_type": "strategy_signal",
        "severity": "INFO",
        "message": "EMA crossover detected",
        "source": "strategy_runner",
        "category": "signal",
        "metadata": {"symbol": "BTC/USD", "timeframe": "1h"},
        "created_at": recent_time,
    }
    db.save_alert(entry)

    # Retrieve alerts
    alerts = db.get_alerts(limit=10)
    assert len(alerts) >= 1
    types = [a["alert_type"] for a in alerts]
    assert "strategy_signal" in types


# ---------------------------------------------------------------------------
# DB info / repair — require real PostgreSQL (pg_container)
# ---------------------------------------------------------------------------


def test_get_db_info_all_on_healthy_db(pg_container):
    """get_db_info_all() should report all domains healthy."""
    info = repair_database_all()
    # repair_database_all returns a dict with results_per_db
    assert "results_per_db" in info
    for domain in ("candles", "batch_results", "paper_trading", "system"):
        assert domain in info["results_per_db"]
        assert info["results_per_db"][domain]["status"] == "success"


def test_get_db_info(pg_container):
    """get_db_info() should return per-domain info."""
    info = get_db_info()
    assert isinstance(info, dict)
    for domain_info in info.values():
        assert domain_info["type"] == "postgresql"


def test_repair_database_all(pg_container):
    """repair_database_all() should report success on healthy domains."""
    result = repair_database_all()
    assert result["status"] == "success"
    assert result["cross_db_consistent"] is True
