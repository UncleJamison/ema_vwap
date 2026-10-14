"""
FastAPI Server and API Endpoints for EMA + VWAP Trading System.
Provides REST endpoints for candle fetching, indicator calculation, strategy backtesting,
background task management, system settings, and static file hosting for the Web Dashboard.
"""

import logging
import os
from contextlib import asynccontextmanager

# ------------------------------------------------------------
# Standardized error handling (ema_vwap-4l1w)
# ------------------------------------------------------------
from enum import Enum
from typing import Any, ClassVar

from fastapi import (
    FastAPI,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.app_limiter import limiter
from src.auth import get_configured_api_key
from src.db_connection_manager import get_database
from src.logger import logger, setup_logging
from src.paper import PaperLedger, PaperProfileRegistry, PaperTradingEngine

# Route modules (ema_vwap-tq41): endpoints extracted verbatim into src/routes/.
# Shared state stays module-level here; route modules read it through this
# module at call time so monkeypatching src.app._* in tests keeps working.
# -------------------------------------------------------------------------
from src.routes import (
    alerts_router,
    api_diagnostics_router,
    audit_router,
    backtest_opt_router,
    batch_router,
    data_ops_router,
    oms_router,
    paper_router,
    portfolio_router,
    settings_router,
    tasks_router,
    tear_sheet_router,
    ws_router,
)

# Backward-compatibility re-exports for tests that import from src.app
# These are moved into route modules but re-exported here so existing test
# imports (e.g., from src.app import BacktestRequest) continue to work.
# =============================================================================
from src.routes.backtest_opt import (
    BacktestRequest,
    BaseStrategyRequest,
    MonteCarloRequest,
    OptimizeRequest,
    ValidateStrategyRequest,
    WFORequest,
    build_strategy_params,
)
from src.routes.batch import BatchOptimizeRequest, _run_all_metrics_batch_task
from src.settings import SettingsManager
from src.tasks import global_task_manager


class ErrorCode(str, Enum):
    """Standardized error codes for API responses."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"


class APIError(Exception):
    """Base exception for API errors with structured details."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        status_code: int = 500,
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code
        super().__init__(message)


def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
    """Global exception handler for APIError."""
    logger.error(
        f"[API] {exc.code.value}: {exc.message}",
        extra={"details": exc.details, "path": str(request.url)},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "error": {
                "code": exc.code.value,
                "message": exc.message,
                "details": exc.details,
            },
        },
    )


def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Handler for Pydantic validation errors."""
    logger.warning(
        f"[API] Validation error: {exc}",
        extra={"path": str(request.url)},
    )
    return JSONResponse(
        status_code=422,
        content={
            "status": "error",
            "error": {
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": "Request validation failed",
                "details": {"errors": str(exc)},
            },
        },
    )


def handle_generic_error(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unexpected errors (no stack trace leaked)."""
    logger.exception(
        f"[API] Internal error: {exc}",
        extra={"path": str(request.url)},
    )
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "error": {
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": "An internal error occurred",
                "details": {},
            },
        },
    )


# Initialize Rotating File & Console Logging
setup_logging()
_batch_db = get_database("batch_results")
_candles_db = get_database("candles")
_system_db = get_database("system")
_paper_db = get_database("paper_trading")
_settings_mgr = SettingsManager(_system_db)
_paper_profiles = PaperProfileRegistry(_paper_db)
_paper_ledger = PaperLedger(_paper_db)
_paper_engine = PaperTradingEngine(db=_paper_db)


class _PollFilter(logging.Filter):
    """Suppress noisy GET /api/batch_status and /api/tasks 200 lines from the uvicorn access log."""

    _SUPPRESS: ClassVar[set[str]] = {"/api/batch_status", "/api/tasks"}

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(path in msg and "200" in msg for path in self._SUPPRESS)


# Install the filter once at import time (works with --reload too)
logging.getLogger("uvicorn.access").addFilter(_PollFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI application lifespan manager for startup and graceful shutdown."""
    logger.info(
        "[Lifespan] Initializing database and evaluating paper runner auto-start..."
    )
    # Initialize all domain databases
    _candles_db.init_db()
    _batch_db.init_db()
    _system_db.init_db()
    _paper_db.init_db()

    # First-run bootstrap: if no API key is configured anywhere (env var or
    # settings DB), generate a secure random key, persist it, and log it once.
    # This prevents lockout: without it every authenticated endpoint fails
    # closed and the Settings UI could never set a key.
    from src.auth.security import get_configured_api_key

    if not get_configured_api_key():
        import secrets as _secrets

        generated = _secrets.token_urlsafe(32)
        _settings_mgr.set("app_api_key", generated, is_secret=True)
        logger.warning(
            "[Lifespan] No API key configured - generated a new one. "
            "Retrieve it with: docker exec ema_vwap_trading_daemon python -c "
            '"from src.settings import SettingsManager; '
            "from src.db_connection_manager import get_database; "
            "print(SettingsManager(get_database('system')).get('app_api_key'))\" "
            "then set it in the Settings UI."
        )

    # Check if live paper polling was active prior to restart
    runner_enabled = _settings_mgr.get("paper_runner_enabled", "false")
    is_enabled = str(runner_enabled).lower() == "true"
    interval_str = _settings_mgr.get("paper_polling_interval", "15")
    try:
        interval = int(interval_str or 15)
    except ValueError:
        interval = 15

    if is_enabled:
        logger.info(
            f"[Lifespan] Auto-resuming live paper trading runner (interval={interval}s)..."
        )
        _paper_engine.start_polling(interval_seconds=interval, persist=False)
    else:
        logger.info("[Lifespan] Paper trading runner is in standby mode.")

    yield

    logger.info("[Lifespan] Shutting down: stopping paper polling runner...")
    _paper_engine.stop_polling(persist=False)


app = FastAPI(
    title="EMA + VWAP Crypto Trading Suite",
    description="Quantitative Crypto Trading System utilizing EMA timing and VWAP institutional bias.",
    version="1.0.0",
    lifespan=lifespan,
)


# Security headers (ema_vwap-d7o): CSP, HSTS, X-Frame-Options, etc.
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # HSTS only meaningful over HTTPS; safe to always send
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    # Restrictive CSP: allow self for scripts/styles, no object frames
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.plot.ly; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data:; "
        "connect-src 'self' ws://localhost:8080 ws://127.0.0.1:8080 http://localhost:8080 http://127.0.0.1:8080; "
        "font-src 'self' https://fonts.gstatic.com; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'"
    )
    return response


# Rate limiting (ema_vwap-q81): shared Limiter instance (src.app_limiter) so
# route modules decorate with the same limiter bound to app.state.

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Standardized error handling (ema_vwap-4l1w)
app.add_exception_handler(APIError, handle_api_error)
app.add_exception_handler(ValueError, handle_validation_error)
app.add_exception_handler(Exception, handle_generic_error)

# API Key authentication (delegates to centralized src.auth module)
API_KEY = get_configured_api_key()


app.include_router(tear_sheet_router)
app.include_router(data_ops_router)
app.include_router(settings_router)
app.include_router(backtest_opt_router)
app.include_router(tasks_router)
app.include_router(batch_router)
app.include_router(paper_router)
app.include_router(portfolio_router)
app.include_router(ws_router)
app.include_router(alerts_router)
app.include_router(api_diagnostics_router)
app.include_router(oms_router)
app.include_router(audit_router)

# -------------------------------------------------------------------------
# Paper Trading Profiles Endpoints


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(content=b"", status_code=204)


# Enable CORS for local web UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _resolve_web_dir():
    from pathlib import Path

    return Path(os.path.join(os.path.dirname(__file__), "web"))


def _resolve_lookback(tf: str, days: int | None = None) -> tuple[int, int]:
    from src.config import get_timeframe_spec

    spec = get_timeframe_spec(tf, category="crypto")
    lookback_days = spec.default_lookback_days if days is None else days
    candles_limit = lookback_days * 1440
    return lookback_days, max(50, candles_limit)


if __name__ == "__main__":
    import uvicorn

    # Secure defaults (ema_vwap-xxb): bind to localhost, reload disabled.
    # Override with HOST / PORT / RELOAD env vars only when explicitly set.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    reload_flag = os.environ.get("RELOAD", "false").lower() == "true"
    uvicorn.run("src.app:app", host=host, port=port, reload=reload_flag)

# Mount Web Static Files (use resolver so monkeypatch works for tests)
web_dir = _resolve_web_dir()
if web_dir and web_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    @app.get("/")
    async def read_root():
        index_file = os.path.join(_resolve_web_dir(), "index.html")
        if os.path.exists(index_file):
            # Read the HTML and inject the API key
            try:
                from src.auth.security import get_configured_api_key

                api_key = get_configured_api_key()
                with open(index_file, "r", encoding="utf-8") as f:  # noqa: ASYNC230
                    html_content = f.read()

                # Inject API key as a global variable before other scripts load
                if api_key:
                    injection = (
                        f'<script>window.BOOTSTRAP_API_KEY = "{api_key}";</script>'
                    )
                    # Inject after <head> tag
                    html_content = html_content.replace("<head>", f"<head>{injection}")

                from fastapi.responses import HTMLResponse

                return HTMLResponse(content=html_content)
            except Exception:
                # Fallback to original file
                return FileResponse(index_file)
        return JSONResponse(
            {"message": "EMA + VWAP Trading System API running. Static UI loading..."}
        )

    @app.head("/")
    async def read_root_head():
        return await read_root()

    @app.get("/index.html")
    def read_index_html():
        index_file = os.path.join(_resolve_web_dir(), "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return JSONResponse(
            {"message": "EMA + VWAP Trading System API running. Static UI loading..."}
        )

    @app.head("/index.html")
    def read_index_html_head():
        return read_index_html()


__all__ = [
    "BacktestRequest",
    "BaseStrategyRequest",
    "BatchOptimizeRequest",
    "MonteCarloRequest",
    "OptimizeRequest",
    "ValidateStrategyRequest",
    "WFORequest",
    "_batch_db",
    "_candles_db",
    "_paper_db",
    "_paper_engine",
    "_paper_ledger",
    "_paper_profiles",
    "_resolve_web_dir",
    "_run_all_metrics_batch_task",
    "_settings_mgr",
    "_system_db",
    "app",
    "build_strategy_params",
    "global_task_manager",
]
