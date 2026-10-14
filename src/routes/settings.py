"""
Settings, Exchanges Catalog & Regime Endpoints.
Extracted from src/app.py (ema_vwap-tq41). Shared state is read through
`src.app` at call time so monkeypatched tests keep working.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.app_limiter import limiter
from src.auth import get_api_key
from src.data_loader import DataLoader
from src.regime import INSUFFICIENT_DATA, REGIME_MAP, get_current_regime

router = APIRouter(tags=["Settings"])


def _st():
    """Return the src.app module (call-time resolution for monkeypatched state)."""
    import src.app as m

    return m


@router.get("/api/settings")
@limiter.limit("30/minute")
def get_settings(request: Request) -> dict[str, Any]:
    """Retrieve all system settings and credentials with secrets safely masked."""
    settings = _st()._settings_mgr.get_all_masked()
    return {"status": "success", "settings": settings}


@router.get("/api/exchanges")
def get_exchanges() -> dict[str, Any]:
    return {
        "exchanges": [
            {
                "id": "gemini",
                "name": "Gemini (Live / Public API)",
                "default_symbol": "BTC/USD",
            },
            {
                "id": "kucoin",
                "name": "KuCoin (Live / Public API)",
                "default_symbol": "BTC-USDT",
            },
            {
                "id": "synthetic",
                "name": "Synthetic Generator (Offline Testing)",
                "default_symbol": "BTC/USD",
            },
        ],
        "timeframes": ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"],
        "strategy_modes": [
            {"id": "crossover", "name": "Setup 1: EMA 9 x VWAP Crossover"},
            {
                "id": "multi_ema",
                "name": "Setup 2: Multi-EMA Confirmation (8/21 x 50 VWAP)",
            },
            {"id": "pullback", "name": "Setup 3: VWAP Pullback Entry"},
        ],
    }


@router.post("/api/settings", dependencies=[Depends(get_api_key)])
@limiter.limit("10/minute")
def update_settings(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Save or update system settings and API credentials."""
    if not payload:
        raise HTTPException(status_code=400, detail="Empty settings payload provided.")
    updated = _st()._settings_mgr.update_bulk(payload)
    return {
        "status": "success",
        "message": f"Successfully updated {updated} setting(s).",
    }


@router.get("/api/regime/current")
def get_regime_current() -> dict[str, Any]:
    """Return the current detected regime (trending / ranging / high_vol).

    Computes the regime live from the latest available candles for the default
    symbol/timeframe via classify_regime(), falling back to INSUFFICIENT_DATA
    if no data is available.
    """

    try:
        df = DataLoader.load_candles(
            exchange="kucoin",
            symbol="BTC/USD",
            timeframe="1h",
            days=2,
        )
        if df.empty or len(df) < 60:
            regime = INSUFFICIENT_DATA
        else:
            regime = get_current_regime(df)
    except Exception:
        regime = INSUFFICIENT_DATA

    return {
        "status": "success",
        "regime": regime,
        "last_computed": datetime.now(timezone.utc).isoformat(),
        "regime_map": dict(REGIME_MAP),
    }


@router.get("/api/settings/sensitive")
async def get_sensitive_settings(request: Request) -> dict[str, Any]:
    """Get sensitive settings (API keys, secrets) for the current session.
    Allows unauthenticated access for bootstrap (first page load).
    The key is already protected by network access to the dashboard.
    """
    from src.logger import logger

    logger.info(
        f"[DEBUG] get_sensitive_settings called, headers: {dict(request.headers)}"
    )
    settings = _st()._settings_mgr.get_all_sensitive()
    return {"status": "success", "settings": settings}


@router.post("/api/settings/sensitive", dependencies=[Depends(get_api_key)])
async def update_sensitive_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Update sensitive settings (API keys, secrets)."""
    if not payload:
        raise HTTPException(status_code=400, detail="Empty settings payload provided.")
    updated = _st()._settings_mgr.update_sensitive(payload)
    return {
        "status": "success",
        "message": f"Successfully updated {updated} sensitive setting(s).",
    }
