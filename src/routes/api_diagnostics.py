"""
API Key Diagnostics & Connectivity Test Endpoints (ema_vwap-7fb).

Provides POST /api/diagnostics/test_connection endpoint that accepts
exchange credentials and performs real connectivity/credential verification.
"""

import time
import urllib.error
import urllib.request
from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.auth import get_api_key

router = APIRouter(prefix="/api/diagnostics", tags=["Diagnostics"])


class ConnectionTestRequest(BaseModel):
    """Request to test an exchange connection with provided credentials."""

    exchange: str = Field(
        ..., description="Exchange name: gemini, kucoin, alpaca, robinhood"
    )
    # Credentials are optional; if omitted, uses stored settings
    api_key: str | None = None
    api_secret: str | None = None
    passphrase: str | None = None
    private_key: str | None = None
    account_number: str | None = None
    sandbox: bool | None = None
    paper: bool | None = None


class ConnectionTestResponse(BaseModel):
    """Response from a connection test."""

    exchange: str
    status: (
        str  # "success" | "error" | "timeout" | "invalid_credentials" | "rate_limited"
    )
    latency_ms: int | None = None
    message: str
    details: dict[str, Any] = {}


def _get_credentials_for_exchange(
    exchange: str, req: ConnectionTestRequest
) -> dict[str, Any]:
    """Build credential dict from request or fall back to settings."""
    if exchange == "gemini":
        return {
            "api_key": req.api_key,
            "secret_key": req.api_secret,
            "sandbox": req.sandbox if req.sandbox is not None else True,
        }
    elif exchange == "kucoin":
        return {
            "api_key": req.api_key,
            "secret_key": req.api_secret,
            "passphrase": req.passphrase,
            "sandbox": req.sandbox if req.sandbox is not None else False,
        }
    elif exchange == "alpaca":
        return {
            "api_key": req.api_key,
            "secret_key": req.api_secret,
            "paper": req.paper if req.paper is not None else True,
        }
    elif exchange == "robinhood":
        return {
            "api_key": req.api_key,
            "private_key": req.private_key,
            "account_number": req.account_number,
        }
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported exchange: {exchange}")


def _test_gemini(creds: dict) -> ConnectionTestResponse:
    """Test Gemini REST API connectivity and credentials."""
    start = time.perf_counter()
    try:

        # Test with a simple public endpoint first (no auth needed)

        test_url = "https://api.sandbox.gemini.com/v1/symbols"
        resp = requests.get(test_url, timeout=10)
        latency = int((time.perf_counter() - start) * 1000)

        if resp.status_code == 200:
            # Now test private endpoint if credentials provided
            if creds.get("api_key") and creds.get("secret_key"):
                private_url = "https://api.sandbox.gemini.com/v1/balances"
                import base64
                import hashlib
                import hmac
                import json

                payload = {
                    "request": "/v1/balances",
                    "nonce": str(int(time.time() * 1000)),
                }
                encoded = base64.b64encode(json.dumps(payload).encode()).decode()
                sig = hmac.new(
                    creds["secret_key"].encode(), encoded.encode(), hashlib.sha384
                ).hexdigest()
                headers = {
                    "X-GEMINI-APIKEY": creds["api_key"],
                    "X-GEMINI-PAYLOAD": encoded,
                    "X-GEMINI-SIGNATURE": sig,
                }
                priv_resp = requests.post(private_url, headers=headers, timeout=10)
                if priv_resp.status_code == 200:
                    balances = priv_resp.json()
                    return ConnectionTestResponse(
                        exchange="gemini",
                        status="success",
                        latency_ms=latency,
                        message="Connected successfully with valid credentials",
                        details={
                            "balance_count": len(balances),
                            "sandbox": creds.get("sandbox", True),
                        },
                    )
                else:
                    return ConnectionTestResponse(
                        exchange="gemini",
                        status="invalid_credentials",
                        latency_ms=latency,
                        message=f"Public endpoint OK but private auth failed: {priv_resp.status_code}",
                        details={"error": priv_resp.text},
                    )
            else:
                return ConnectionTestResponse(
                    exchange="gemini",
                    status="success",
                    latency_ms=latency,
                    message="Public endpoint reachable (no credentials tested)",
                    details={"sandbox": creds.get("sandbox", True)},
                )
        else:
            return ConnectionTestResponse(
                exchange="gemini",
                status="error",
                latency_ms=latency,
                message=f"Public endpoint failed: {resp.status_code}",
                details={"error": resp.text},
            )
    except requests.Timeout:
        return ConnectionTestResponse(
            exchange="gemini",
            status="timeout",
            latency_ms=None,
            message="Request timed out",
            details={},
        )
    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        return ConnectionTestResponse(
            exchange="gemini",
            status="error",
            latency_ms=latency,
            message=f"Connection test failed: {e!s}",
            details={"error": str(e)},
        )


def _test_kucoin(creds: dict) -> ConnectionTestResponse:
    """Test KuCoin REST API connectivity and credentials."""
    start = time.perf_counter()
    try:

        # Test public endpoint
        test_url = "https://api.kucoin.com/api/v1/timestamp"
        resp = requests.get(test_url, timeout=10)
        latency = int((time.perf_counter() - start) * 1000)

        if resp.status_code == 200 and resp.json().get("code") == "200000":
            if (
                creds.get("api_key")
                and creds.get("secret_key")
                and creds.get("passphrase")
            ):
                # Test private endpoint
                import base64
                import hashlib
                import hmac

                now = str(int(time.time() * 1000))

                str_to_sign = now + "GET" + "/api/v1/accounts"
                sig = base64.b64encode(
                    hmac.new(
                        creds["secret_key"].encode(),
                        str_to_sign.encode(),
                        hashlib.sha256,
                    ).digest()
                ).decode()
                headers = {
                    "KC-API-KEY": creds["api_key"],
                    "KC-API-SIGN": sig,
                    "KC-API-TIMESTAMP": now,
                    "KC-API-PASSPHRASE": creds["passphrase"],
                    "KC-API-KEY-VERSION": "2",
                }
                priv_resp = requests.get(
                    "https://api.kucoin.com/api/v1/accounts",
                    headers=headers,
                    timeout=10,
                )
                if (
                    priv_resp.status_code == 200
                    and priv_resp.json().get("code") == "200000"
                ):
                    accounts = priv_resp.json()["data"]
                    return ConnectionTestResponse(
                        exchange="kucoin",
                        status="success",
                        latency_ms=latency,
                        message="Connected successfully with valid credentials",
                        details={
                            "account_count": len(accounts),
                            "sandbox": creds.get("sandbox", False),
                        },
                    )
                else:
                    return ConnectionTestResponse(
                        exchange="kucoin",
                        status="invalid_credentials",
                        latency_ms=latency,
                        message=f"Public endpoint OK but private auth failed: {priv_resp.status_code}",
                        details={"error": priv_resp.text},
                    )
            else:
                return ConnectionTestResponse(
                    exchange="kucoin",
                    status="success",
                    latency_ms=latency,
                    message="Public endpoint reachable (no credentials tested)",
                    details={"sandbox": creds.get("sandbox", False)},
                )
        else:
            return ConnectionTestResponse(
                exchange="kucoin",
                status="error",
                latency_ms=latency,
                message=f"Public endpoint failed: {resp.status_code}",
                details={"error": resp.text},
            )
    except requests.Timeout:
        return ConnectionTestResponse(
            exchange="kucoin",
            status="timeout",
            latency_ms=None,
            message="Request timed out",
            details={},
        )
    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        return ConnectionTestResponse(
            exchange="kucoin",
            status="error",
            latency_ms=latency,
            message=f"Connection test failed: {e!s}",
            details={"error": str(e)},
        )


def _test_alpaca(creds: dict) -> ConnectionTestResponse:
    """Test Alpaca REST API connectivity and credentials."""
    start = time.perf_counter()
    try:
        import json

        # Test account endpoint (requires auth)
        if creds.get("api_key") and creds.get("secret_key"):
            base = (
                "https://paper-api.alpaca.markets"
                if creds.get("paper", True)
                else "https://api.alpaca.markets"
            )
            url = f"{base}/v2/account"
            headers = {
                "APCA-API-KEY-ID": creds["api_key"],
                "APCA-API-SECRET-KEY": creds["secret_key"],
            }
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                latency = int((time.perf_counter() - start) * 1000)
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    return ConnectionTestResponse(
                        exchange="alpaca",
                        status="success",
                        latency_ms=latency,
                        message="Connected successfully with valid credentials",
                        details={
                            "account_status": data.get("status"),
                            "equity": data.get("equity"),
                            "buying_power": data.get("buying_power"),
                            "paper": creds.get("paper", True),
                        },
                    )
                else:
                    return ConnectionTestResponse(
                        exchange="alpaca",
                        status="invalid_credentials",
                        latency_ms=latency,
                        message=f"Auth failed: {resp.status}",
                        details={"error": resp.read().decode()},
                    )
        else:
            # No creds - just test connectivity to public endpoint
            url = "https://api.alpaca.markets/v2/clock"
            with urllib.request.urlopen(url, timeout=10) as resp:
                latency = int((time.perf_counter() - start) * 1000)
                return ConnectionTestResponse(
                    exchange="alpaca",
                    status="success",
                    latency_ms=latency,
                    message="Public endpoint reachable (no credentials tested)",
                    details={},
                )
    except urllib.error.HTTPError as e:
        latency = int((time.perf_counter() - start) * 1000)
        return ConnectionTestResponse(
            exchange="alpaca",
            status="invalid_credentials" if e.code == 401 else "error",
            latency_ms=latency,
            message=f"HTTP error: {e.code}",
            details={"error": e.read().decode()},
        )
    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        return ConnectionTestResponse(
            exchange="alpaca",
            status="error",
            latency_ms=latency,
            message=f"Connection test failed: {e!s}",
            details={"error": str(e)},
        )


def _test_robinhood(creds: dict) -> ConnectionTestResponse:
    """Test Robinhood connectivity - placeholder since no official public API."""
    # Robinhood doesn't have an official public REST API for this use case
    # This would require unofficial libraries or OAuth flow
    return ConnectionTestResponse(
        exchange="robinhood",
        status="error",
        latency_ms=None,
        message="Robinhood API testing not implemented (no official public API)",
        details={"note": "Requires unofficial library or OAuth integration"},
    )


@router.post("/test_connection", dependencies=[Depends(get_api_key)])
def test_connection(req: ConnectionTestRequest) -> ConnectionTestResponse:
    """
    Test connection to an exchange with provided or stored credentials.

    Performs:
    1. Public endpoint reachability check
    2. Private endpoint authentication (if credentials provided)
    3. Balance/permissions retrieval
    4. Latency measurement
    """
    exchange = req.exchange.lower()
    creds = _get_credentials_for_exchange(exchange, req)

    if exchange == "gemini":
        return _test_gemini(creds)
    elif exchange == "kucoin":
        return _test_kucoin(creds)
    elif exchange == "alpaca":
        return _test_alpaca(creds)
    elif exchange == "robinhood":
        return _test_robinhood(creds)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported exchange: {exchange}")


@router.get("/supported_exchanges")
async def list_supported_exchanges() -> dict[str, Any]:
    """List exchanges that support connection testing."""
    return {
        "exchanges": [
            {
                "id": "gemini",
                "name": "Gemini",
                "asset_type": "crypto",
                "supports_sandbox": True,
                "required_fields": ["api_key", "api_secret"],
            },
            {
                "id": "kucoin",
                "name": "KuCoin",
                "asset_type": "crypto",
                "supports_sandbox": True,
                "required_fields": ["api_key", "api_secret", "passphrase"],
            },
            {
                "id": "alpaca",
                "name": "Alpaca",
                "asset_type": "stock",
                "supports_paper": True,
                "required_fields": ["api_key", "api_secret"],
            },
            {
                "id": "robinhood",
                "name": "Robinhood",
                "asset_type": "stock",
                "required_fields": ["api_key", "private_key", "account_number"],
                "note": "Unofficial API - testing not yet implemented",
            },
        ]
    }
