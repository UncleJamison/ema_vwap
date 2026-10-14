"""
Tests for UI layer traceability fixes:
- /api/db/vacuum endpoint authentication and multi-DB vacuum execution
- /api/db/export/csv and /api/db/export/json GET and POST method compatibility
- /api/optimizer/* route aliases (optimize, batch_optimize, status)
"""

from fastapi.testclient import TestClient

from src.app import app
from src.auth import get_api_key
from src.db_maintenance_multi import vacuum_database_all


def test_db_vacuum_requires_authentication():
    """POST /api/db/vacuum must reject unauthenticated calls with 403."""
    app.dependency_overrides.pop(get_api_key, None)
    try:
        client = TestClient(app)
        resp = client.post("/api/db/vacuum")
        assert resp.status_code == 403
    finally:
        app.dependency_overrides[get_api_key] = lambda: "test-key-for-tests"


def test_db_vacuum_authenticated_success():
    """POST /api/db/vacuum must execute successfully when authenticated."""
    client = TestClient(app)
    resp = client.post("/api/db/vacuum", headers={"X-API-Key": "test-key-for-tests"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("success", "partial_failure")
    assert "data" in data
    assert "results_per_db" in data["data"]


def test_vacuum_database_all_function():
    """vacuum_database_all helper must return per-DB execution results."""
    res = vacuum_database_all()
    assert "status" in res
    assert "results_per_db" in res
    assert isinstance(res["results_per_db"], dict)


def test_db_export_csv_supports_get_and_post():
    """Export CSV endpoint must accept both GET and POST requests."""
    client = TestClient(app)
    # Test GET
    get_res = client.get(
        "/api/db/export/csv?limit=5",
        headers={"X-API-Key": "test-key-for-tests"},
    )
    assert get_res.status_code == 200
    assert "text/csv" in get_res.headers.get("content-type", "")

    # Test POST (as called by UI maintenance export button)
    post_res = client.post(
        "/api/db/export/csv?limit=5",
        headers={"X-API-Key": "test-key-for-tests"},
    )
    assert post_res.status_code == 200
    assert "text/csv" in post_res.headers.get("content-type", "")


def test_db_export_json_supports_get_and_post():
    """Export JSON endpoint must accept both GET and POST requests."""
    client = TestClient(app)
    # Test GET
    get_res = client.get(
        "/api/db/export/json?limit=5",
        headers={"X-API-Key": "test-key-for-tests"},
    )
    assert get_res.status_code == 200
    assert "application/json" in get_res.headers.get("content-type", "")

    # Test POST
    post_res = client.post(
        "/api/db/export/json?limit=5",
        headers={"X-API-Key": "test-key-for-tests"},
    )
    assert post_res.status_code == 200
    assert "application/json" in post_res.headers.get("content-type", "")


def test_optimizer_route_aliases_exist():
    """Optimizer alias routes must exist and be registered in app."""
    client = TestClient(app)

    # Status alias
    status_resp = client.get("/api/optimizer/status")
    assert status_resp.status_code == 200
    assert "running" in status_resp.json()

    # Optimize alias now requires auth (security improvement: ema_vwap-c45)
    app.dependency_overrides.pop(get_api_key, None)
    try:
        opt_resp = client.post("/api/optimizer/optimize", json={})
        # Should return 403 without auth (security fix applied)
        assert opt_resp.status_code == 403
    finally:
        app.dependency_overrides[get_api_key] = lambda: "test-key-for-tests"
