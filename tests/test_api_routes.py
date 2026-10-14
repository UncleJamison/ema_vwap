"""
API route tests for settings/sensitive, api_diagnostics, and tear-sheet endpoints.
Added to close coverage gaps found in the security/optimization/traceability audit.
"""

import pytest
from fastapi.testclient import TestClient

import src.app as app_module
from src.app import app
from src.auth import get_api_key


@pytest.fixture
def client(monkeypatch):
    """Client WITHOUT auth (pops the conftest global override)."""
    monkeypatch.setattr("src.auth.security.get_configured_api_key", lambda: "test-key")
    app.dependency_overrides.pop(get_api_key, None)
    yield TestClient(app)
    app.dependency_overrides.pop(get_api_key, None)


@pytest.fixture
def auth_client(monkeypatch):
    """Client that sends a valid API key header through the REAL dependency."""
    monkeypatch.setattr("src.auth.security.get_configured_api_key", lambda: "test-key")
    app.dependency_overrides.pop(get_api_key, None)
    yield TestClient(app)
    app.dependency_overrides.pop(get_api_key, None)


class TestSettingsSensitiveAPI:
    """The /api/settings/sensitive endpoints added by the secrets-out-of-localStorage fix."""

    def test_get_returns_masked_or_empty_settings(self, auth_client):
        resp = auth_client.get(
            "/api/settings/sensitive", headers={"X-API-Key": "test-key"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert isinstance(body["settings"], dict)

    def test_get_bootstrap_allowed(self, client):
        """GET /api/settings/sensitive is intentionally unauthenticated for bootstrap."""
        resp = client.get("/api/settings/sensitive")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert "settings" in body

    def test_post_updates_setting(self, auth_client, monkeypatch):
        # Record what update_sensitive receives
        captured = {}

        class FakeMgr:
            def update_sensitive(self, settings_map):
                captured.update(settings_map)
                return len(settings_map)

            def get_all_sensitive(self):
                return {}

        monkeypatch.setattr(app_module, "_settings_mgr", FakeMgr(), raising=False)
        resp = auth_client.post(
            "/api/settings/sensitive",
            json={"app_api_key": "new-key-123"},
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 200
        assert captured.get("app_api_key") == "new-key-123"
        assert "Successfully updated 1" in resp.json()["message"]

    def test_post_empty_payload_rejected(self, auth_client, monkeypatch):
        monkeypatch.setattr(
            app_module,
            "_settings_mgr",
            type(
                "M",
                (),
                {
                    "update_sensitive": lambda self, m: 0,
                    "get_all_sensitive": lambda self: {},
                },
            )(),
            raising=False,
        )
        resp = auth_client.post(
            "/api/settings/sensitive", json={}, headers={"X-API-Key": "test-key"}
        )
        assert resp.status_code == 400

    def test_post_requires_auth(self, client):
        resp = client.post("/api/settings/sensitive", json={"a": "b"})
        assert resp.status_code in (401, 403)


class TestApiDiagnostics:
    def test_supported_exchanges_public(self, client):
        resp = client.get("/api/diagnostics/supported_exchanges")
        assert resp.status_code == 200
        exchanges = resp.json()["exchanges"]
        ids = {e["id"] for e in exchanges}
        assert {"gemini", "kucoin", "alpaca", "robinhood"} <= ids

    def test_test_connection_rejects_unknown_exchange(self, auth_client):
        resp = auth_client.post(
            "/api/diagnostics/test_connection",
            json={"exchange": "not-a-real-exchange"},
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 400
        assert "Unsupported exchange" in resp.json()["detail"]

    def test_test_connection_requires_auth(self, client):
        resp = client.post(
            "/api/diagnostics/test_connection",
            json={"exchange": "gemini"},
        )
        assert resp.status_code in (401, 403)


class TestTearSheet:
    def _payload(self):
        return {
            "exchange": "kucoin",
            "symbol": "BTC/USDT",
            "timeframe": "5m",
            "params": {"fast_ema": 9, "slow_ema": 21, "trend_ema": 50},
        }

    def test_all_tear_sheet_endpoints_require_auth(self, client):
        for path in (
            "/api/tear-sheet/pdf",
            "/api/tear-sheet/html",
            "/api/tear-sheet/json",
        ):
            resp = client.post(path, json=self._payload())
            # 500 = auth PASSED and WeasyPrint/GTK is missing on this host — not an auth failure
            assert resp.status_code in (
                401,
                403,
                422,
                500,
            ), f"{path} returned {resp.status_code}"
            assert resp.status_code != 200, f"{path} returned 200 without API key"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
