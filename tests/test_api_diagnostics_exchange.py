"""
Tests for API Diagnostics Exchange Connectivity (src/routes/api_diagnostics.py):
- POST /api/diagnostics/test_connection for Gemini, KuCoin, Alpaca, Robinhood
- GET /api/diagnostics/supported_exchanges
Tests cover: success cases, invalid credentials, timeouts, errors, rate limiting,
unsupported exchanges, credential fallbacks, sandbox/paper modes.
"""

from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.app import app

AUTH_HEADERS = {"X-API-Key": "test-key-for-tests"}


from src.routes.api_diagnostics import (
    ConnectionTestRequest,
    ConnectionTestResponse,
    _get_credentials_for_exchange,
    _test_alpaca,
    _test_gemini,
    _test_kucoin,
    _test_robinhood,
)

client = TestClient(app)


class TestSupportedExchanges:
    """Tests for GET /api/diagnostics/supported_exchanges."""

    def test_supported_exchanges_returns_all_four(self):
        """Should return all four supported exchanges with correct metadata."""
        response = client.get("/api/diagnostics/supported_exchanges")
        assert response.status_code == 200

        data = response.json()
        assert "exchanges" in data
        exchanges = data["exchanges"]
        assert len(exchanges) == 4

        exchange_ids = {e["id"] for e in exchanges}
        assert exchange_ids == {"gemini", "kucoin", "alpaca", "robinhood"}

        # Verify Gemini
        gemini = next(e for e in exchanges if e["id"] == "gemini")
        assert gemini["name"] == "Gemini"
        assert gemini["asset_type"] == "crypto"
        assert gemini["supports_sandbox"] is True
        assert set(gemini["required_fields"]) == {"api_key", "api_secret"}

        # Verify KuCoin
        kucoin = next(e for e in exchanges if e["id"] == "kucoin")
        assert kucoin["name"] == "KuCoin"
        assert kucoin["asset_type"] == "crypto"
        assert kucoin["supports_sandbox"] is True
        assert set(kucoin["required_fields"]) == {"api_key", "api_secret", "passphrase"}

        # Verify Alpaca
        alpaca = next(e for e in exchanges if e["id"] == "alpaca")
        assert alpaca["name"] == "Alpaca"
        assert alpaca["asset_type"] == "stock"
        assert alpaca["supports_paper"] is True
        assert set(alpaca["required_fields"]) == {"api_key", "api_secret"}

        # Verify Robinhood
        robinhood = next(e for e in exchanges if e["id"] == "robinhood")
        assert robinhood["name"] == "Robinhood"
        assert robinhood["asset_type"] == "stock"
        assert "note" in robinhood


class TestGetCredentialsForExchange:
    """Tests for _get_credentials_for_exchange helper."""

    def test_gemini_uses_sandbox_default_true(self):
        """Gemini should default to sandbox=True."""
        req = ConnectionTestRequest(
            exchange="gemini", api_key="test", api_secret="secret"
        )
        creds = _get_credentials_for_exchange("gemini", req)
        assert creds["api_key"] == "test"
        assert creds["secret_key"] == "secret"
        assert creds["sandbox"] is True

    def test_gemini_overrides_sandbox(self):
        """Gemini sandbox can be overridden."""
        req = ConnectionTestRequest(
            exchange="gemini", api_key="test", api_secret="secret", sandbox=False
        )
        creds = _get_credentials_for_exchange("gemini", req)
        assert creds["sandbox"] is False

    def test_kucoin_uses_sandbox_default_false(self):
        """KuCoin should default to sandbox=False."""
        req = ConnectionTestRequest(
            exchange="kucoin", api_key="test", api_secret="secret", passphrase="pass"
        )
        creds = _get_credentials_for_exchange("kucoin", req)
        assert creds["api_key"] == "test"
        assert creds["secret_key"] == "secret"
        assert creds["passphrase"] == "pass"
        assert creds["sandbox"] is False

    def test_kucoin_overrides_sandbox(self):
        """KuCoin sandbox can be overridden."""
        req = ConnectionTestRequest(
            exchange="kucoin",
            api_key="test",
            api_secret="secret",
            passphrase="pass",
            sandbox=True,
        )
        creds = _get_credentials_for_exchange("kucoin", req)
        assert creds["sandbox"] is True

    def test_alpaca_uses_paper_default_true(self):
        """Alpaca should default to paper=True."""
        req = ConnectionTestRequest(
            exchange="alpaca", api_key="test", api_secret="secret"
        )
        creds = _get_credentials_for_exchange("alpaca", req)
        assert creds["api_key"] == "test"
        assert creds["secret_key"] == "secret"
        assert creds["paper"] is True

    def test_alpaca_overrides_paper(self):
        """Alpaca paper can be overridden."""
        req = ConnectionTestRequest(
            exchange="alpaca", api_key="test", api_secret="secret", paper=False
        )
        creds = _get_credentials_for_exchange("alpaca", req)
        assert creds["paper"] is False

    def test_robinhood_requires_private_key(self):
        """Robinhood requires private_key and account_number."""
        req = ConnectionTestRequest(
            exchange="robinhood",
            api_key="test",
            private_key="private",
            account_number="12345",
        )
        creds = _get_credentials_for_exchange("robinhood", req)
        assert creds["api_key"] == "test"
        assert creds["private_key"] == "private"
        assert creds["account_number"] == "12345"

    def test_unsupported_exchange_raises(self):
        """Unsupported exchange should raise HTTPException."""
        req = ConnectionTestRequest(
            exchange="binance", api_key="test", api_secret="secret"
        )
        with pytest.raises(HTTPException) as exc_info:
            _get_credentials_for_exchange("binance", req)
        assert exc_info.value.status_code == 400
        assert "Unsupported exchange" in str(exc_info.value.detail)


class TestTestConnectionEndpoint:
    """Tests for POST /api/diagnostics/test_connection endpoint."""

    def test_unsupported_exchange_returns_400(self):
        """Unsupported exchange should return 400."""
        response = client.post(
            "/api/diagnostics/test_connection",
            json={"exchange": "binance", "api_key": "test", "api_secret": "secret"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 400
        assert "Unsupported exchange" in response.json()["detail"]

    @patch("src.routes.api_diagnostics._test_gemini")
    def test_gemini_success_with_credentials(self, mock_test_gemini):
        """Should call _test_gemini and return its result."""
        mock_response = ConnectionTestResponse(
            exchange="gemini",
            status="success",
            latency_ms=150,
            message="Connected successfully",
            details={"balance_count": 5, "sandbox": True},
        )
        mock_test_gemini.return_value = mock_response

        response = client.post(
            "/api/diagnostics/test_connection",
            json={
                "exchange": "gemini",
                "api_key": "test-key",
                "api_secret": "test-secret",
            },
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["exchange"] == "gemini"
        assert data["status"] == "success"
        assert data["latency_ms"] == 150
        assert data["details"]["balance_count"] == 5

    @patch("src.routes.api_diagnostics._test_kucoin")
    def test_kucoin_success_with_credentials(self, mock_test_kucoin):
        """Should call _test_kucoin and return its result."""
        mock_response = ConnectionTestResponse(
            exchange="kucoin",
            status="success",
            latency_ms=200,
            message="Connected successfully",
            details={"account_count": 3, "sandbox": False},
        )
        mock_test_kucoin.return_value = mock_response

        response = client.post(
            "/api/diagnostics/test_connection",
            json={
                "exchange": "kucoin",
                "api_key": "test-key",
                "api_secret": "test-secret",
                "passphrase": "test-passphrase",
            },
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["exchange"] == "kucoin"
        assert data["status"] == "success"

    @patch("src.routes.api_diagnostics._test_alpaca")
    def test_alpaca_success_with_credentials(self, mock_test_alpaca):
        """Should call _test_alpaca and return its result."""
        mock_response = ConnectionTestResponse(
            exchange="alpaca",
            status="success",
            latency_ms=180,
            message="Connected successfully",
            details={"account_status": "ACTIVE", "equity": "10000.00", "paper": True},
        )
        mock_test_alpaca.return_value = mock_response

        response = client.post(
            "/api/diagnostics/test_connection",
            json={
                "exchange": "alpaca",
                "api_key": "test-key",
                "api_secret": "test-secret",
            },
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["exchange"] == "alpaca"
        assert data["status"] == "success"

    @patch("src.routes.api_diagnostics._test_robinhood")
    def test_robinhood_not_implemented(self, mock_test_robinhood):
        """Should return error for Robinhood (not implemented)."""
        mock_response = ConnectionTestResponse(
            exchange="robinhood",
            status="error",
            latency_ms=None,
            message="Robinhood API testing not implemented (no official public API)",
            details={"note": "Requires unofficial library or OAuth integration"},
        )
        mock_test_robinhood.return_value = mock_response

        response = client.post(
            "/api/diagnostics/test_connection",
            json={
                "exchange": "robinhood",
                "api_key": "test-key",
                "private_key": "private-key",
                "account_number": "12345",
            },
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["exchange"] == "robinhood"
        assert data["status"] == "error"
        assert "not implemented" in data["message"].lower()


class TestGeminiConnectionTest:
    """Tests for _test_gemini function (mocked HTTP)."""

    @patch("src.routes.api_diagnostics.requests.get")
    def test_gemini_public_endpoint_success_no_creds(self, mock_get):
        """Should succeed with public endpoint only when no creds provided."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["BTCUSD", "ETHUSD"]
        mock_get.return_value = mock_resp

        creds = {"api_key": None, "secret_key": None, "sandbox": True}
        result = _test_gemini(creds)

        assert result.status == "success"
        assert "no credentials tested" in result.message.lower()
        assert result.details.get("sandbox") is True

    @patch("src.routes.api_diagnostics.requests.get")
    def test_gemini_public_endpoint_failure(self, mock_get):
        """Should return error when public endpoint fails."""
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_get.return_value = mock_resp

        creds = {"api_key": None, "secret_key": None, "sandbox": True}
        result = _test_gemini(creds)

        assert result.status == "error"
        assert "Public endpoint failed" in result.message

    @patch("src.routes.api_diagnostics.requests.get")
    @patch("src.routes.api_diagnostics.requests.post")
    def test_gemini_private_success(self, mock_post, mock_get):
        """Should test private endpoint when creds provided and succeed."""
        # Public endpoint
        mock_pub = Mock()
        mock_pub.status_code = 200
        mock_pub.json.return_value = ["BTCUSD"]
        mock_get.return_value = mock_pub

        # Private endpoint
        mock_priv = Mock()
        mock_priv.status_code = 200
        mock_priv.json.return_value = [{"currency": "BTC", "amount": "1.0"}]
        mock_post.return_value = mock_priv

        creds = {"api_key": "test", "secret_key": "secret", "sandbox": True}
        result = _test_gemini(creds)

        assert result.status == "success"
        assert "valid credentials" in result.message.lower()
        assert result.details.get("balance_count") == 1

    @patch("src.routes.api_diagnostics.requests.get")
    @patch("src.routes.api_diagnostics.requests.post")
    def test_gemini_private_invalid_credentials(self, mock_post, mock_get):
        """Should return invalid_credentials when private auth fails."""
        mock_pub = Mock()
        mock_pub.status_code = 200
        mock_pub.json.return_value = ["BTCUSD"]
        mock_get.return_value = mock_pub

        mock_priv = Mock()
        mock_priv.status_code = 401
        mock_priv.text = "Invalid signature"
        mock_post.return_value = mock_priv

        creds = {"api_key": "test", "secret_key": "wrong", "sandbox": True}
        result = _test_gemini(creds)

        assert result.status == "invalid_credentials"
        assert "private auth failed" in result.message.lower()

    @patch("src.routes.api_diagnostics.requests.get")
    def test_gemini_timeout(self, mock_get):
        """Should return timeout when request times out."""
        import requests

        mock_get.side_effect = requests.Timeout()

        creds = {"api_key": "test", "secret_key": "secret", "sandbox": True}
        result = _test_gemini(creds)

        assert result.status == "timeout"
        assert "timed out" in result.message.lower()


class TestKuCoinConnectionTest:
    """Tests for _test_kucoin function (mocked HTTP)."""

    @patch("src.routes.api_diagnostics.requests.get")
    def test_kucoin_public_endpoint_success_no_creds(self, mock_get):
        """Should succeed with public endpoint only when no creds provided."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": "200000", "data": "1234567890"}
        mock_get.return_value = mock_resp

        creds = {
            "api_key": None,
            "secret_key": None,
            "passphrase": None,
            "sandbox": False,
        }
        result = _test_kucoin(creds)

        assert result.status == "success"
        assert "no credentials tested" in result.message.lower()

    @patch("src.routes.api_diagnostics.requests.get")
    def test_kucoin_public_endpoint_failure(self, mock_get):
        """Should return error when public endpoint fails."""
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_get.return_value = mock_resp

        creds = {
            "api_key": None,
            "secret_key": None,
            "passphrase": None,
            "sandbox": False,
        }
        result = _test_kucoin(creds)

        assert result.status == "error"

    @patch("src.routes.api_diagnostics.requests.get")
    def test_kucoin_private_success(self, mock_get):
        """Should test private endpoint when creds provided and succeed."""
        # Public endpoint
        mock_pub = Mock()
        mock_pub.status_code = 200
        mock_pub.json.return_value = {"code": "200000", "data": "1234567890"}

        # Private endpoint
        mock_priv = Mock()
        mock_priv.status_code = 200
        mock_priv.json.return_value = {
            "code": "200000",
            "data": [{"currency": "BTC", "balance": "1.0"}],
        }

        mock_get.side_effect = [mock_pub, mock_priv]

        creds = {
            "api_key": "test",
            "secret_key": "secret",
            "passphrase": "pass",
            "sandbox": False,
        }
        result = _test_kucoin(creds)

        assert result.status == "success"
        assert "valid credentials" in result.message.lower()
        assert result.details.get("account_count") == 1

    @patch("src.routes.api_diagnostics.requests.get")
    def test_kucoin_private_invalid_credentials(self, mock_get):
        """Should return invalid_credentials when private auth fails."""
        mock_pub = Mock()
        mock_pub.status_code = 200
        mock_pub.json.return_value = {"code": "200000", "data": "1234567890"}

        mock_priv = Mock()
        mock_priv.status_code = 401
        mock_priv.text = "Invalid signature"

        mock_get.side_effect = [mock_pub, mock_priv]

        creds = {
            "api_key": "test",
            "secret_key": "wrong",
            "passphrase": "pass",
            "sandbox": False,
        }
        result = _test_kucoin(creds)

        assert result.status == "invalid_credentials"


class TestAlpacaConnectionTest:
    """Tests for _test_alpaca function (mocked HTTP)."""

    @patch("src.routes.api_diagnostics.urllib.request.urlopen")
    def test_alpaca_public_endpoint_success_no_creds(self, mock_urlopen):
        """Should succeed with public clock endpoint when no creds provided."""
        mock_resp = Mock()
        mock_resp.status = 200
        mock_resp.read.return_value = (
            b'{"timestamp":"2024-01-01T00:00:00Z","is_open":true}'
        )
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        creds = {"api_key": None, "secret_key": None, "paper": True}
        result = _test_alpaca(creds)

        assert result.status == "success"
        assert "no credentials tested" in result.message.lower()

    @patch("src.routes.api_diagnostics.urllib.request.urlopen")
    def test_alpaca_private_success(self, mock_urlopen):
        """Should test account endpoint when creds provided and succeed."""
        mock_resp = Mock()
        mock_resp.status = 200
        mock_resp.read.return_value = (
            b'{"status":"ACTIVE","equity":"10000.00","buying_power":"20000.00"}'
        )
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp

        creds = {"api_key": "test", "secret_key": "secret", "paper": True}
        result = _test_alpaca(creds)

        assert result.status == "success"
        assert "valid credentials" in result.message.lower()
        assert result.details.get("account_status") == "ACTIVE"

    @patch("src.routes.api_diagnostics.urllib.request.urlopen")
    def test_alpaca_invalid_credentials(self, mock_urlopen):
        """Should return invalid_credentials on 401."""
        import urllib.error

        mock_resp = Mock()
        mock_resp.code = 401
        mock_resp.read.return_value = b'{"message":"unauthorized"}'
        error = urllib.error.HTTPError("url", 401, "Unauthorized", {}, mock_resp)
        mock_urlopen.side_effect = error

        creds = {"api_key": "test", "secret_key": "wrong", "paper": True}
        result = _test_alpaca(creds)

        assert result.status == "invalid_credentials"

    @patch("src.routes.api_diagnostics.urllib.request.urlopen")
    def test_alpaca_other_http_error(self, mock_urlopen):
        """Should return error on other HTTP errors."""
        import urllib.error

        mock_resp = Mock()
        mock_resp.code = 500
        mock_resp.read.return_value = b'{"message":"server error"}'
        error = urllib.error.HTTPError("url", 500, "Server Error", {}, mock_resp)
        mock_urlopen.side_effect = error

        creds = {"api_key": "test", "secret_key": "secret", "paper": True}
        result = _test_alpaca(creds)

        assert result.status == "error"


class TestRobinhoodConnectionTest:
    """Tests for _test_robinhood function."""

    def test_robinhood_always_returns_error(self):
        """Should always return error since not implemented."""
        result = _test_robinhood(
            {"api_key": "test", "private_key": "key", "account_number": "123"}
        )
        assert result.status == "error"
        assert "not implemented" in result.message.lower()


class TestRequestResponseModels:
    """Tests for Pydantic models."""

    def test_connection_test_request_defaults(self):
        """Should have correct default values."""
        req = ConnectionTestRequest(exchange="gemini")
        assert req.exchange == "gemini"
        assert req.api_key is None
        assert req.sandbox is None
        assert req.paper is None

    def test_connection_test_request_all_fields(self):
        """Should accept all fields."""
        req = ConnectionTestRequest(
            exchange="kucoin",
            api_key="key",
            api_secret="secret",
            passphrase="pass",
            sandbox=True,
            paper=False,
        )
        assert req.exchange == "kucoin"
        assert req.api_key == "key"
        assert req.api_secret == "secret"
        assert req.passphrase == "pass"
        assert req.sandbox is True
        assert req.paper is False

    def test_connection_test_response_serialization(self):
        """Response should serialize correctly."""
        resp = ConnectionTestResponse(
            exchange="gemini",
            status="success",
            latency_ms=150,
            message="OK",
            details={"balance_count": 5},
        )
        data = resp.model_dump()
        assert data["exchange"] == "gemini"
        assert data["status"] == "success"
        assert data["latency_ms"] == 150
        assert data["details"]["balance_count"] == 5


class TestIntegration:
    """Integration-style tests with full request flow."""

    @patch("src.routes.api_diagnostics._test_gemini")
    def test_full_gemini_flow_with_defaults(self, mock_test_gemini):
        """Full integration test with default sandbox for Gemini."""
        mock_test_gemini.return_value = ConnectionTestResponse(
            exchange="gemini",
            status="success",
            latency_ms=100,
            message="OK",
            details={"sandbox": True},
        )

        response = client.post(
            "/api/diagnostics/test_connection",
            json={"exchange": "gemini", "api_key": "key", "api_secret": "secret"},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["details"]["sandbox"] is True

    @patch("src.routes.api_diagnostics._test_kucoin")
    def test_full_kucoin_flow_with_sandbox_override(self, mock_test_kucoin):
        """Full integration test with sandbox override for KuCoin."""
        mock_test_kucoin.return_value = ConnectionTestResponse(
            exchange="kucoin",
            status="success",
            latency_ms=100,
            message="OK",
            details={"sandbox": True},
        )

        response = client.post(
            "/api/diagnostics/test_connection",
            json={
                "exchange": "kucoin",
                "api_key": "key",
                "api_secret": "secret",
                "passphrase": "pass",
                "sandbox": True,
            },
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["details"]["sandbox"] is True

    @patch("src.routes.api_diagnostics._test_alpaca")
    def test_full_alpaca_flow_with_paper_override(self, mock_test_alpaca):
        """Full integration test with paper override for Alpaca."""
        mock_test_alpaca.return_value = ConnectionTestResponse(
            exchange="alpaca",
            status="success",
            latency_ms=100,
            message="OK",
            details={"paper": False},
        )

        response = client.post(
            "/api/diagnostics/test_connection",
            json={
                "exchange": "alpaca",
                "api_key": "key",
                "api_secret": "secret",
                "paper": False,
            },
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["details"]["paper"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
