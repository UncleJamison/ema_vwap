"""
FastAPI Server and API Endpoints for EMA + VWAP Trading System.
Provides REST endpoints for candle fetching, indicator calculation, strategy backtesting,
background task management, system settings, and static file hosting for the Web Dashboard.
"""

import asyncio
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
from src.database import CandleDatabase
from src.logger import logger, setup_logging
from src.metrics import (
    API_REQUEST_COUNT,
    API_REQUEST_DURATION,
    get_content_type,
    get_metrics,
)
from src.paper import PaperLedger, PaperProfileRegistry, PaperTradingEngine
from src.pg_connection_manager import close_pg_pools, init_pg_pools

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
from src.tracing import (
    instrument_asyncpg,
    instrument_fastapi,
    instrument_httpx,
    shutdown_tracing,
)


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
_batch_db = CandleDatabase(domain="batch_results")
_candles_db = CandleDatabase(domain="candles")
_system_db = CandleDatabase(domain="system")
_paper_db = CandleDatabase(domain="paper_trading")
_settings_mgr = SettingsManager()
_paper_profiles = PaperProfileRegistry()
_paper_ledger = PaperLedger()
# settings_mgr wired in so start/stop polling persists `paper_runner_enabled`
# and the lifespan auto-resumes the runner after a container restart.
_paper_engine = PaperTradingEngine(db=_paper_db, settings_mgr=_settings_mgr)


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
        "[Lifespan] Initializing PostgreSQL and evaluating paper runner auto-start..."
    )
    # Register the main event loop for sync-bridge calls
    import asyncio as _aio

    from src.sync_bridge import get_main_loop, set_main_loop

    # Save any previously-set main loop (e.g., from pg_container test fixture)
    # so we can restore it on shutdown — avoids clobbering test infrastructure.
    _prev_main_loop = get_main_loop()
    set_main_loop(_aio.get_running_loop())

    # Initialize PostgreSQL connection pools first. If pools are already
    # initialized by an external owner (e.g. the pytest pg_container session
    # fixture), reuse them and do NOT close them on shutdown — closing pools
    # we don't own killed every subsequent PG test in the session.
    from src.pg_connection_manager import pg_pools_initialized

    _owns_pools = not pg_pools_initialized()
    await init_pg_pools()
    logger.info("[Lifespan] PostgreSQL connection pools initialized")

    if not _owns_pools:
        # Pools belong to an external initializer (e.g. the pytest pg_container
        # session fixture or an embedding orchestrator). They are bound to
        # THAT owner's event loop: using them from this (app) loop would be a
        # cross-loop asyncpg violation, and closing them would break the
        # owner. Schema creation and first-run bootstrap are the owner's
        # responsibility. In production nothing owns the pools before the app,
        # so this branch is test/embedding-only.
        logger.info(
            "[Lifespan] Externally-owned PG pools detected: skipping schema "
            "init, API-key bootstrap, and runner auto-resume (owner's "
            "responsibility). Paper trading runner stays in standby."
        )
        yield
    else:
        # Initialize all domain schemas (PostgreSQL)
        from src.pg_database import PostgresDatabase

        for domain in ("candles", "batch_results", "system", "paper_trading"):
            db = PostgresDatabase(domain=domain)
            await db.initialize()
        logger.info("[Lifespan] All domain schemas initialized")

        # First-run bootstrap: if no API key is configured anywhere (env var or
        # settings DB), generate a secure random key, persist it, and log it once.
        # This prevents lockout: without it every authenticated endpoint fails
        # closed and the Settings UI could never set a key.
        import secrets as _secrets

        from src.pg_database import get_setting_pg, save_setting_pg

        api_key_row = await get_setting_pg("app_api_key")
        api_key_val = (
            str(api_key_row["value"])
            if api_key_row and api_key_row.get("value")
            else None
        )

        if not api_key_val and not os.environ.get("API_KEY"):
            generated = _secrets.token_urlsafe(32)
            await save_setting_pg("app_api_key", generated, True)
            logger.warning(
                "[Lifespan] No API key configured - generated a new one. "
                "Retrieve it from the Settings UI or settings table."
            )

        # Check if live paper polling was active prior to restart
        runner_row = await get_setting_pg("paper_runner_enabled")
        runner_enabled = (
            str(runner_row["value"])
            if runner_row and runner_row.get("value")
            else "false"
        )
        is_enabled = runner_enabled.lower() == "true"
        interval_row = await get_setting_pg("paper_polling_interval")
        interval_str = (
            str(interval_row["value"])
            if interval_row and interval_row.get("value")
            else "15"
        )
        try:
            interval = int(interval_str or 15)
        except ValueError:
            interval = 15

        if is_enabled:
            logger.info(
                f"[Lifespan] Auto-resuming live paper trading runner (interval={interval}s)..."
            )
            # Run in a worker thread: start_polling() -> reconcile uses sync
            # ledger calls (run_async), which must not execute on the main loop.
            await asyncio.to_thread(
                _paper_engine.start_polling, interval_seconds=interval, persist=False
            )
        else:
            logger.info("[Lifespan] Paper trading runner is in standby mode.")

        yield

    # Shutdown tracing
    logger.info("[Lifespan] Shutting down OpenTelemetry tracing...")
    shutdown_tracing()
    logger.info("[Lifespan] Shutting down: stopping paper polling runner...")
    _paper_engine.stop_polling(persist=False)
    if _owns_pools:
        logger.info("[Lifespan] Closing PostgreSQL connection pools...")
        await close_pg_pools()
    else:
        logger.info(
            "[Lifespan] Pools owned by an external initializer; leaving them open."
        )
    # Restore previously-set main loop (e.g., pg_container test fixture)
    if _prev_main_loop is not None:
        set_main_loop(_prev_main_loop)


app = FastAPI(
    title="EMA + VWAP Crypto Trading Suite",
    description="Quantitative Crypto Trading System utilizing EMA timing and VWAP institutional bias.",
    version="1.0.0",
    lifespan=lifespan,
)

# Instrument FastAPI with OpenTelemetry
instrument_fastapi(app)
instrument_asyncpg()
instrument_httpx()


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

    # Trace ID Middleware for structured logging correlation
    @app.middleware("http")
    async def trace_id_middleware(request: Request, call_next):
        """Inject trace_id into request state for structured logging correlation."""
        import uuid

        from src.logger import clear_trace_id, set_trace_id

        # Generate or extract trace_id from headers
        trace_id = request.headers.get("X-Trace-ID", str(uuid.uuid4())[:8])
        set_trace_id(trace_id)

        # Add trace_id to response headers for client correlation
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id

        clear_trace_id()
        return response

    # Prometheus Metrics Middleware
    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        """Track API request duration and count for Prometheus metrics."""
        import time

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        # Record metrics
        endpoint = request.url.path
        method = request.method
        status = str(response.status_code)

        API_REQUEST_DURATION.labels(
            endpoint=endpoint, method=method, status=status
        ).observe(duration)
        API_REQUEST_COUNT.labels(endpoint=endpoint, method=method, status=status).inc()

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


@app.get("/metrics", include_in_schema=False)
def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=get_metrics(), media_type=get_content_type())


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
                # Async loader: read_root runs on the main event loop, where
                # sync wrappers cross-loop-crash against bound pools. Await
                # the PG read directly instead.
                from src.auth.security import get_configured_api_key_async

                api_key = await get_configured_api_key_async()
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
