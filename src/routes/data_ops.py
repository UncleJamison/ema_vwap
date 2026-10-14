"""
Data Sync, Cache & Database Maintenance Endpoints.
Extracted from src/app.py (ema_vwap-tq41). Shared state is read through
`src.app` at call time so monkeypatched tests keep working.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from src.app_limiter import limiter
from src.auth import get_api_key
from src.data_loader import DataLoader
from src.logger import logger

router = APIRouter(tags=["Data Ops"])


def _st():
    """Return the src.app module (call-time resolution for monkeypatched state)."""
    import src.app as m

    return m


class SyncHistoryRequest(BaseModel):
    exchange: str = "kucoin"
    symbol: str = "BTC/USDT"
    timeframe: str = "5m"
    days: int | None = 360


@router.post("/api/sync_history", dependencies=[Depends(get_api_key)])
@limiter.limit("10/minute")
def sync_history(request: Request, req: SyncHistoryRequest) -> dict[str, Any]:
    try:
        logger.info(
            f"[API] Syncing {req.days} days of historical candles for {req.symbol} ({req.exchange})..."
        )
        df = DataLoader.load_candles(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            days=req.days,
            limit=None,
            use_cache=False,
        )
        return {
            "status": "success",
            "message": f"Successfully synced {len(df)} candles for {req.symbol} ({req.exchange}, {req.days} days) into database.",
            "candle_count": len(df),
            "start_time": str(df["timestamp"].iloc[0]) if not df.empty else None,
            "end_time": str(df["timestamp"].iloc[-1]) if not df.empty else None,
        }
    except Exception as e:
        logger.error(f"[API] sync_history error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/clear_cache", dependencies=[Depends(get_api_key)])
def clear_cache() -> dict[str, Any]:
    try:
        deleted = _st()._batch_db.clear_cache()
        return {
            "status": "success",
            "message": f"Successfully cleared {deleted} cached candles from database.",
        }
    except Exception as e:
        logger.error(f"[API] clear_cache error: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear cache")


@router.get("/api/db/info", dependencies=[Depends(get_api_key)])
def db_info() -> dict[str, Any]:
    """Return database file information for maintenance UI (multi-DB)."""
    try:
        from src.db_maintenance_multi import get_db_info_all

        info = get_db_info_all()
        return {"status": "success", "data": info}
    except Exception as e:
        logger.error(f"[API] db/info error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/db/repair", dependencies=[Depends(get_api_key)])
def db_repair() -> dict[str, Any]:
    """Quarantine corrupt database files and initialize a clean database (multi-DB)."""
    try:
        from src.db_maintenance_multi import repair_database_all

        result = repair_database_all()
        return {"status": result["status"], "data": result}
    except Exception as e:
        logger.error(f"[API] db/repair error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/db/vacuum", dependencies=[Depends(get_api_key)])
def db_vacuum() -> dict[str, Any]:
    """Reclaim unused database disk space across all domain databases (multi-DB)."""
    try:
        from src.db_maintenance_multi import vacuum_database_all

        result = vacuum_database_all()
        return {"status": result["status"], "data": result}
    except Exception as e:
        logger.error(f"[API] db/vacuum error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.api_route(
    "/api/db/export/csv",
    methods=["GET", "POST"],
    dependencies=[Depends(get_api_key)],
)
def db_export_csv(
    exchange: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    limit: int | None = Query(default=500, ge=1, le=5000),
) -> Response:
    """Export candle data as CSV download."""
    try:
        csv_data = _st()._candles_db.export_candles_csv(
            exchange, symbol, timeframe, limit
        )
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=candles_{exchange or 'all'}_{symbol or 'all'}_{timeframe or 'all'}.csv"
            },
        )
    except Exception as e:
        logger.error(f"[API] db/export/csv error: {e}")
        raise HTTPException(status_code=500, detail="Internal error during CSV export")


@router.api_route(
    "/api/db/export/json",
    methods=["GET", "POST"],
    dependencies=[Depends(get_api_key)],
)
def db_export_json(
    exchange: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    limit: int | None = Query(default=500, ge=1, le=5000),
) -> Response:
    """Export candle data as JSON download."""
    try:
        json_data = _st()._candles_db.export_candles_json(
            exchange, symbol, timeframe, limit
        )
        return Response(
            content=json_data,
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=candles_{exchange or 'all'}_{symbol or 'all'}_{timeframe or 'all'}.json"
            },
        )
    except Exception as e:
        logger.error(f"[API] db/export/json error: {e}")
        raise HTTPException(status_code=500, detail="Internal error during JSON export")
