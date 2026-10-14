"""
Tests for Database Layer Traceability Fixes (workspace/traceability_database_review.md):
- alerts table DDL and index creation in init_db()
- save_alert(), get_alerts(), acknowledge_alert(), delete_alert() functionality
- Legacy alerts schema backwards-compatible migration
- repair_database() status semantics ('healthy', 'repaired' with quarantine, 'error')
- App alert REST endpoints wiring to system domain
"""

import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.database import CandleDatabase


@pytest.fixture
def clean_db(tmp_path):
    db_file = os.path.join(tmp_path, "system_fresh_traceability.db")
    db = CandleDatabase(db_path=db_file)
    return db


def test_init_db_creates_alerts_table_and_indexes(clean_db):
    """Verify that init_db creates the alerts table, required columns, and composite indexes."""
    with clean_db.get_connection() as conn:
        cursor = conn.cursor()
        # Verify table existence
        tables = [
            r[0]
            for r in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "alerts" in tables
        assert "candles" in tables

        # Verify columns
        columns = {
            r[1]: r[2] for r in cursor.execute("PRAGMA table_info(alerts)").fetchall()
        }
        for col in [
            "id",
            "type",
            "message",
            "level",
            "source",
            "category",
            "metadata",
            "created_at",
            "acknowledged",
            "acknowledged_at",
        ]:
            assert col in columns, f"Missing column {col} in alerts table"

        # Verify indexes
        indexes = [
            r[1]
            for r in cursor.execute(
                "SELECT type, name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        ]
        assert "idx_alerts_created_at" in indexes
        assert "idx_alerts_type" in indexes
        assert "idx_alerts_level" in indexes
        assert "idx_candles_lookup" in indexes


def test_alert_lifecycle(clean_db):
    """Test save_alert, get_alerts, acknowledge_alert, and delete_alert on a fresh DB."""
    # 1. Save alert using modern keys
    entry = {
        "type": "strategy_signal",
        "message": "EMA crossover detected",
        "level": "INFO",
        "source": "strategy_runner",
        "category": "signal",
        "metadata": {"symbol": "BTC/USD", "timeframe": "1h"},
        "created_at": "2026-09-15T12:00:00Z",
    }
    clean_db.save_alert(entry)

    # 2. Save alert using legacy keys (alert_type and timestamp fallback)
    legacy_entry = {
        "alert_type": "system_health",
        "message": "High memory usage",
        "level": "WARNING",
        "timestamp": "2026-09-15T12:05:00Z",
    }
    clean_db.save_alert(legacy_entry)

    # 3. Retrieve alerts
    alerts = clean_db.get_alerts(limit=10)
    assert len(alerts) == 2
    types = [a["type"] for a in alerts]
    assert "strategy_signal" in types
    assert "system_health" in types

    # Filter by level
    warning_alerts = clean_db.get_alerts(level="WARNING")
    assert len(warning_alerts) == 1
    assert warning_alerts[0]["message"] == "High memory usage"

    # Filter by unacknowledged
    unack_alerts = clean_db.get_alerts(acknowledged=False)
    assert len(unack_alerts) == 2

    # 4. Acknowledge alert
    alert_id = alerts[0]["id"]
    ack_res = clean_db.acknowledge_alert(alert_id)
    assert ack_res is True

    updated_alerts = clean_db.get_alerts(acknowledged=True)
    assert len(updated_alerts) == 1
    assert updated_alerts[0]["id"] == alert_id
    assert updated_alerts[0]["acknowledged"] == 1
    assert updated_alerts[0]["acknowledged_at"] is not None

    # 5. Delete alert
    del_res = clean_db.delete_alert(alert_id)
    assert del_res is True
    assert len(clean_db.get_alerts(limit=10)) == 1


def test_alerts_legacy_schema_migration(tmp_path):
    """Verify that an older DB with legacy alerts schema is safely migrated."""
    legacy_db_path = os.path.join(tmp_path, "system_legacy_alerts.db")
    conn = sqlite3.connect(legacy_db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}',
            channels_sent TEXT NOT NULL DEFAULT '[]',
            acknowledged INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    cur.execute(
        """
        INSERT INTO alerts (timestamp, alert_type, level, message, acknowledged)
        VALUES ('2026-09-10T10:00:00Z', 'risk_breach', 'ERROR', 'Max drawdown breached', 0)
        """
    )
    conn.commit()
    conn.close()

    # Now open with CandleDatabase and run init_db
    db = CandleDatabase(db_path=legacy_db_path)
    # Data should be accessible and migrated
    alerts = db.get_alerts(limit=5)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "risk_breach"
    assert alerts[0]["created_at"] == "2026-09-10T10:00:00Z"
    assert alerts[0]["message"] == "Max drawdown breached"


def test_repair_database_healthy(clean_db):
    """Verify repair_database returns healthy when database integrity check passes."""
    res = clean_db.repair_database()
    assert res["status"] == "healthy"
    assert res["message"] == "no repair needed"
    assert res["quarantined_files"] == []


def test_repair_database_corrupt_quarantined_and_rebuilt(tmp_path):
    """Verify that a corrupted DB file is quarantined and replaced with a healthy database."""
    corrupt_db_path = os.path.join(tmp_path, "system_corrupt_test.db")
    # Initialize a valid DB first
    db = CandleDatabase(db_path=corrupt_db_path)
    # Corrupt it with junk bytes
    with open(corrupt_db_path, "wb") as f:
        f.write(b"NOT A VALID SQLITE DATABASE FILE HEADER JUNK DATA")

    repair_res = db.repair_database()

    assert repair_res["status"] == "repaired"
    assert "quarantined" in repair_res["message"]
    assert len(repair_res["quarantined_files"]) >= 1
    quarantined_file = repair_res["quarantined_files"][0]
    assert os.path.exists(quarantined_file)
    with open(quarantined_file, "rb") as f:
        assert f.read() == b"NOT A VALID SQLITE DATABASE FILE HEADER JUNK DATA"

    # Verify the original path now contains a fresh, valid database
    assert os.path.exists(corrupt_db_path)
    info = db.get_db_info()
    assert info["integrity_check"] == "ok"


def test_app_alerts_api_integration(tmp_path, monkeypatch):
    """Verify app.py alert endpoints write and read from the system database."""
    test_db_path = os.path.join(tmp_path, "system_sys_alerts.db")
    test_db = CandleDatabase(db_path=test_db_path)

    # Patch get_database so both AlertManager and app use test_db
    monkeypatch.setattr("src.db_connection_manager.get_database", lambda k: test_db)
    monkeypatch.setattr("src.alerting.get_database", lambda k: test_db)
    monkeypatch.setattr("src.app._system_db", test_db)
    # Reset AlertManager singleton so it uses test_db
    import src.alerting as _alerting

    _alerting._alert_manager_instance = None

    # Monkeypatch get_alert_manager to return a fresh AlertManager using test_db
    def _patched_get_alert_manager(settings_mgr=None):
        from src.alerting import AlertManager

        return AlertManager(settings_mgr=None)  # Will use monkeypatched get_database

    monkeypatch.setattr("src.alerting.get_alert_manager", _patched_get_alert_manager)

    client = TestClient(app)

    # 1. Create alert
    create_resp = client.post(
        "/api/alerts",
        json={
            "alert_type": "system_health",
            "message": "Integration test alert",
            "level": "INFO",
            "metadata": {"test": True},
        },
    )
    assert create_resp.status_code == 200
    alert_entry = create_resp.json().get("alert", {})
    assert alert_entry.get("type") == "system_health"

    # 2. List alerts
    list_resp = client.get("/api/alerts?limit=10")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["status"] == "success"
    assert data["count"] >= 1
    created_id = data["alerts"][0]["id"]

    # 3. Acknowledge alert
    ack_resp = client.post(f"/api/alerts/{created_id}/acknowledge")
    assert ack_resp.status_code == 200
    assert ack_resp.json()["status"] == "success"

    # 4. Delete alert
    del_resp = client.delete(f"/api/alerts/{created_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "success"

    # Verify deletion reflected in list
    list_after = client.get("/api/alerts?limit=10").json()
    remaining_ids = [a["id"] for a in list_after["alerts"]]
    assert created_id not in remaining_ids
    # Verify list API still returns with 0 items after deletion
    list_empty = client.get("/api/alerts?limit=10").json()
    assert list_empty["count"] == 0
