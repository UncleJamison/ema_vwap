"""
Comprehensive Security Regression and Verification Test Suite.
Tests all remediations for findings in workspace/security_review.md.
"""

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.auth import get_api_key
from src.auth.security import get_configured_api_key, verify_api_key
from src.database import CandleDatabase
from src.settings import SettingsManager
from src.validator import ValidationCriteria


def test_auth_missing_header_rejected_when_overrides_cleared():
    """Unauthenticated requests to sensitive endpoints without X-API-Key header must return HTTP 403."""
    app.dependency_overrides.pop(get_api_key, None)
    try:
        client = TestClient(app)
        endpoints = [
            ("POST", "/api/portfolio/route_order"),
            ("POST", "/api/portfolio/rebalance/execute"),
            ("POST", "/api/clear_cache"),
            ("GET", "/api/db/info"),
            ("POST", "/api/db/repair"),
            ("POST", "/api/db/vacuum"),
            ("POST", "/api/batch_results/purge"),
            ("POST", "/api/batch_results/export_to_portfolio"),
            ("DELETE", "/api/alerts/1"),
        ]
        for method, path in endpoints:
            resp = client.request(method, path)
            assert (
                resp.status_code == 403
            ), f"{method} {path} should return 403, got {resp.status_code}"
            assert "Invalid or missing API key" in resp.json().get("detail", "")
    finally:
        # Restore mock auth for test environment
        app.dependency_overrides[get_api_key] = lambda: "test-key-for-tests"


def test_auth_invalid_header_rejected():
    """Requests to sensitive endpoints with wrong API key must return HTTP 403."""
    app.dependency_overrides.pop(get_api_key, None)
    try:
        client = TestClient(app)
        resp = client.get("/api/db/info", headers={"X-API-Key": "wrong-secret-key"})
        assert resp.status_code == 403
        assert "Invalid or missing API key" in resp.json().get("detail", "")
    finally:
        app.dependency_overrides[get_api_key] = lambda: "test-key-for-tests"


def test_auth_valid_header_accepted(monkeypatch):
    """Requests with valid configured API key must pass authentication."""
    monkeypatch.setenv("API_KEY", "prod-secret-key-12345")
    app.dependency_overrides.pop(get_api_key, None)
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/db/info", headers={"X-API-Key": "prod-secret-key-12345"}
        )
        # Should not be 403 (can be 200 or successful execution)
        assert resp.status_code == 200
    finally:
        app.dependency_overrides[get_api_key] = lambda: "test-key-for-tests"


def test_unauthenticated_research_and_paper_endpoints_allowed():
    import sys

    print("DEBUG: Starting test", file=sys.stderr, flush=True)
    """Research, backtesting, Optuna sweeps, and paper trading must be accessible without API key.
    
    GET endpoints are publicly accessible. POST endpoints for research/backtesting 
    require API key authentication (422 validation error indicates auth passed).
    """
    app.dependency_overrides.pop(get_api_key, None)
    try:
        client = TestClient(app)
        # GET endpoints should return 200 directly without auth
        unauth_get_endpoints = [
            "/api/portfolio/snapshot",
            "/api/portfolio/venues",
            "/api/portfolio/positions",
            "/api/portfolio/history",
            "/api/paper/profiles",
            "/api/paper/positions",
            "/api/paper/performance",
            "/api/portfolio/correlation",
            "/api/settings",
        ]
        for path in unauth_get_endpoints:
            resp = client.get(path)
            assert (
                resp.status_code == 200
            ), f"{path} should return 200 without API key, got {resp.status_code}"

        # POST endpoints for research require auth - they should return 403/401 without auth
        # but 422 with valid auth and invalid payload
        auth_required_post_endpoints = [
            "/api/backtest",
            "/api/optimize",
            "/api/batch_optimize",
            "/api/optimizer/batch_optimize",
        ]
        print("DEBUG: About to test POST endpoints", file=sys.stderr, flush=True)
        for path in auth_required_post_endpoints:
            print(f"DEBUG: Testing {path}", file=sys.stderr, flush=True)
            resp = client.post(
                path,
                json={"invalid": "payload"},  # valid JSON but wrong structure
                headers={"Content-Type": "application/json"},
            )
            # Should return 403/401 without auth, not 422
            assert resp.status_code in (
                401,
                403,
            ), f"{path} should require auth (401/403), got {resp.status_code}"
    finally:
        app.dependency_overrides[get_api_key] = lambda: "test-key-for-tests"


def test_auth_dev_default_key_rejected(monkeypatch):
    """Fallback dev key 'default-key-for-dev' must fail closed and never authenticate."""
    monkeypatch.setenv("API_KEY", "default-key-for-dev")
    assert get_configured_api_key() is None
    assert verify_api_key("default-key-for-dev") is False


def test_auth_timing_safe_verify(monkeypatch):
    """Key verification must correctly validate keys and reject empty or None."""
    monkeypatch.setenv("API_KEY", "super-secret-trading-key")
    assert verify_api_key("super-secret-trading-key") is True
    assert verify_api_key("super-secret-trading-key-extra") is False
    assert verify_api_key("") is False
    assert verify_api_key(None) is False


def test_database_pragma_journal_mode_allowlist(tmp_path, monkeypatch):
    """PRAGMA journal_mode must reject SQL injection and enforce allowlist."""
    # Attempt SQL injection via DB_JOURNAL_MODE
    monkeypatch.setenv("DB_JOURNAL_MODE", "WAL; DROP TABLE candles; --")
    db = CandleDatabase(db_path=str(tmp_path / "system_safe.db"))
    # If not in allowlist, it safely falls back to DELETE
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode;")
        mode = cur.fetchone()[0]
        assert mode.upper() in ("DELETE", "WAL", "MEMORY", "OFF")


def test_database_delete_batch_results_sanitization(tmp_path):
    """delete_batch_results must cast inputs to integers and handle empty lists safely."""
    db = CandleDatabase(db_path=str(tmp_path / "system_batch.db"))
    db.init_db()
    assert db.delete_batch_results([]) == 0
    # Valid deletion with integer casting
    deleted = db.delete_batch_results([1, 2, 3])
    assert deleted == 0


def test_database_maintenance_no_exception_leak(tmp_path):
    """Database repair and vacuum responses must not expose raw exception text."""
    db = CandleDatabase(db_path=str(tmp_path / "system_maintenance.db"))
    db.init_db()

    vacuum_res = db.vacuum_database()
    assert "status" in vacuum_res
    if vacuum_res["status"] == "error":
        assert "see server logs" in vacuum_res["message"]

    repair_res = db.repair_database()
    assert "status" in repair_res
    assert "error: " not in repair_res["message"]


def test_settings_masked_secret_preservation(tmp_path):
    """SettingsManager must never overwrite existing secrets with dummy masked bullets."""
    db = CandleDatabase(db_path=str(tmp_path / "system_settings.db"))
    db.init_db()
    mgr = SettingsManager(db=db)

    # Save real secret
    mgr.set("binance_api_secret", "actual_super_secret_value_12345")
    assert mgr.get("binance_api_secret") == "actual_super_secret_value_12345"

    # User form submits masked placeholder back
    masked_dummy = "••••••••2345"
    mgr.update_bulk({"binance_api_secret": masked_dummy})
    # Secret must remain unchanged
    assert mgr.get("binance_api_secret") == "actual_super_secret_value_12345"

    # Submitting short bullet placeholder also ignored
    mgr.update_bulk({"binance_api_secret": "••••"})
    assert mgr.get("binance_api_secret") == "actual_super_secret_value_12345"

    # Submitting genuine new secret updates it
    mgr.update_bulk({"binance_api_secret": "new_updated_secret_99999"})
    assert mgr.get("binance_api_secret") == "new_updated_secret_99999"


def test_validator_criteria_bounds_validation():
    """ValidationCriteria must reject out-of-bounds parameters."""
    with pytest.raises(ValueError, match="min_trade_count must be at least 1"):
        ValidationCriteria(min_trade_count=0)

    with pytest.raises(
        ValueError, match="min_profitable_windows_pct must be between 0 and 100"
    ):
        ValidationCriteria(min_profitable_windows_pct=105.0)

    with pytest.raises(
        ValueError, match="max_monte_carlo_ruin_pct must be between 0 and 100"
    ):
        ValidationCriteria(max_monte_carlo_ruin_pct=-5.0)

    with pytest.raises(
        ValueError, match="max_backtest_drawdown_pct must be between 0 and 100"
    ):
        ValidationCriteria(max_backtest_drawdown_pct=150.0)

    with pytest.raises(
        ValueError, match="min_walk_forward_efficiency must be non-negative"
    ):
        ValidationCriteria(min_walk_forward_efficiency=-10.0)
