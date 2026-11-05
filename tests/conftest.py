"""
Test configuration for the ema_vwap PostgreSQL test suite.

Architecture:
- Tests run against the local TimescaleDB test container on 127.0.0.1:5433
  (ema_vwap_test_db Docker container).
- A session-scoped background event loop acts as the sync_bridge main loop
  (mirrors app lifespan): all sync code (PaperLedger, SettingsManager, ...)
  submits coroutines to this single loop, so asyncpg pools stay bound to one
  running loop.
- `pg_container` is the session fixture that initializes pools + schemas in
  that loop. Tests needing real PG depend on it.
- `mock_pg_database` is an autouse fixture that mocks the PG layer for tests
  that do NOT need real PG (fast unit tests). Its skip-list exempts any test
  requesting pg_container / pg_db / pg_pool / pg_manager / audit_logger.
"""

import asyncio
import os
import sys
import threading
from unittest.mock import AsyncMock, MagicMock

# ── Global semaphore to serialize database access ───────────────────────────
# Created in the main event loop (pg_container fixture) to ensure it's bound to the correct loop
_db_semaphore = None


def _get_db_semaphore():
    """Get the database semaphore for serializing access within the main event loop."""
    global _db_semaphore  # noqa: PLW0602
    return _db_semaphore


# ── Global lock to serialize database access ───────────────────────────────
# Using threading.Lock which works across threads
_db_lock = threading.Lock()


def _get_db_lock():
    """Get the database lock for serializing access."""
    global _db_lock  # noqa: PLW0602
    return _db_lock


# This is getting too complex. Let's use a simpler approach: threading.Lock

# ── Environment: point all PG access at the local test container ──────────
# Must be set BEFORE any src import creates a PG manager.
os.environ["PG_HOST"] = "127.0.0.1"
os.environ["PG_PORT"] = "5433"
os.environ["PG_DATABASE"] = "postgres"  # base DB (used to CREATE DATABASE)
os.environ["PG_USER"] = "postgres"
os.environ["PG_PASSWORD"] = "postgres"
os.environ["PG_SSL_MODE"] = "disable"
os.environ["PG_DATABASE_CANDLES"] = "ema_vwap_candles"
os.environ["PG_DATABASE_BATCH_RESULTS"] = "ema_vwap_batch_results"
os.environ["PG_DATABASE_PAPER_TRADING"] = "ema_vwap_paper_trading"
os.environ["PG_DATABASE_SYSTEM"] = "ema_vwap_system"
os.environ["API_KEY"] = "test-key-for-tests"

import pytest
import pytest_asyncio


# Shared test fixtures
@pytest.fixture
def auth_headers():
    """Correct API key headers for testing (X-API-Key header)."""
    return {"X-API-Key": "test-key-for-tests"}


@pytest.fixture
def db_semaphore():
    """Semaphore to serialize database access across tests."""
    return _db_semaphore


# Mock WeasyPrint before any test modules import pdf_renderer (required on Windows)
weasyprint_mock = MagicMock()
weasyprint_mock.HTML = MagicMock()
weasyprint_mock.text = MagicMock()
weasyprint_mock.text.fonts = MagicMock()
weasyprint_mock.css = MagicMock()

sys.modules["weasyprint"] = weasyprint_mock
sys.modules["weasyprint.text"] = weasyprint_mock.text
sys.modules["weasyprint.text.fonts"] = weasyprint_mock.text.fonts
sys.modules["weasyprint.css"] = weasyprint_mock.css


# ── Logging: avoid Windows file-locking during log rotation ───────────────
@pytest.fixture(autouse=True, scope="session")
def configure_test_logging():
    """Configure logging to avoid Windows file locking during tests."""
    import logging

    logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(logs_dir, exist_ok=True)

    import src.logger

    src.logger.setup_logging(
        log_level=logging.WARNING,
        max_bytes=10 * 1024 * 1024,
        backup_count=0,  # disable rotation — avoids Windows rename locks
    )

    try:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    except Exception as e:
        print(f"[conftest] Uvicorn logger config skipped: {e}")

    yield


# ── PG test container + background loop (session scope) ───────────────────
_main_loop = None
_loop_thread = None


def _ensure_domain_databases():
    """Create domain databases if they don't exist."""
    import asyncpg

    async def _create():
        conn = await asyncpg.connect(
            host=os.environ["PG_HOST"],
            port=int(os.environ["PG_PORT"]),
            database=os.environ["PG_DATABASE"],
            user=os.environ["PG_USER"],
            password=os.environ["PG_PASSWORD"],
        )
        try:
            for db_name in [
                os.environ["PG_DATABASE_CANDLES"],
                os.environ["PG_DATABASE_BATCH_RESULTS"],
                os.environ["PG_DATABASE_PAPER_TRADING"],
                os.environ["PG_DATABASE_SYSTEM"],
            ]:
                exists = await conn.fetchval(
                    "SELECT 1 FROM pg_database WHERE datname = $1", db_name
                )
                if not exists:
                    await conn.execute(f'CREATE DATABASE "{db_name}"')
                    print(f"Created database: {db_name}")
        finally:
            await conn.close()

    asyncio.run(_create())


async def _init_pools_and_schemas():
    """Initialize PG pools and create schemas inside the main loop.

    Creates pools that persist for the session. Tests that use pg_container
    get these background-loop pools via get_pg_manager(). Pools are closed
    at session teardown by pg_container.
    """
    from src.pg_connection_manager import (
        PostgresConnectionManager,
        set_pg_manager,
    )
    from src.pg_database import PostgresDatabase

    # Initialize connection pools via the manager
    mgr = PostgresConnectionManager()
    await mgr.initialize()

    # Set as global singleton so get_pg_manager() returns this initialized instance
    set_pg_manager(mgr)

    # Create schemas for each domain
    for domain in PostgresConnectionManager.DOMAIN_DATABASES:
        db = PostgresDatabase(domain=domain)
        await db.initialize()


async def _create_schema(conn, domain):
    """Create schema for a domain using an existing connection."""
    if domain == "candles":
        await conn.execute(
            """
            CREATE EXTENSION IF NOT EXISTS timescaledb;
        """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candles (
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                open DOUBLE PRECISION NOT NULL,
                high DOUBLE PRECISION NOT NULL,
                low DOUBLE PRECISION NOT NULL,
                close DOUBLE PRECISION NOT NULL,
                volume DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (exchange, symbol, timeframe, timestamp)
            );
        """
        )
        await conn.execute(
            """
            SELECT create_hypertable('candles', 'timestamp',
                chunk_time_interval => INTERVAL '1 day',
                if_not_exists => TRUE,
                migrate_data => TRUE);
        """
        )
    elif domain == "batch_results":
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_results (
                id BIGSERIAL PRIMARY KEY,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                strategy_mode TEXT NOT NULL,
                target_metric TEXT NOT NULL,
                best_value DOUBLE PRECISION NOT NULL,
                trade_count INTEGER NOT NULL,
                sharpe_ratio DOUBLE PRECISION,
                calmar_ratio DOUBLE PRECISION,
                win_rate DOUBLE PRECISION,
                max_drawdown_pct DOUBLE PRECISION,
                total_return_pct DOUBLE PRECISION,
                best_params JSONB NOT NULL,
                run_timestamp TIMESTAMPTZ NOT NULL,
                favorite INTEGER NOT NULL DEFAULT 0,
                deployed INTEGER NOT NULL DEFAULT 0,
                checkpoint_fingerprint TEXT
            );
        """
        )
    elif domain == "paper_trading":
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_profiles (
                id BIGSERIAL PRIMARY KEY,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                strategy_mode TEXT NOT NULL,
                target_metric TEXT NOT NULL,
                params JSONB NOT NULL,
                optuna_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                is_active BOOLEAN NOT NULL DEFAULT FALSE,
                is_deployed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                validation_report_json JSONB,
                UNIQUE (exchange, symbol, timeframe, strategy_mode, target_metric)
            );
        """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_positions (
                id BIGSERIAL PRIMARY KEY,
                account_id TEXT NOT NULL DEFAULT 'default',
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price DOUBLE PRECISION NOT NULL,
                current_price DOUBLE PRECISION NOT NULL,
                volume DOUBLE PRECISION NOT NULL,
                cost_basis DOUBLE PRECISION NOT NULL,
                stop_loss DOUBLE PRECISION,
                take_profit DOUBLE PRECISION,
                unrealized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                unrealized_pnl_pct DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                entry_time TIMESTAMPTZ NOT NULL,
                last_bar_time TIMESTAMPTZ,
                metadata JSONB,
                UNIQUE (account_id, exchange, symbol)
            );
        """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_ledger_transactions (
                id BIGSERIAL PRIMARY KEY,
                transaction_id TEXT NOT NULL UNIQUE,
                account_id TEXT NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                tx_type TEXT NOT NULL,
                amount DOUBLE PRECISION NOT NULL,
                asset_qty DOUBLE PRECISION NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                fee DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                balance_after DOUBLE PRECISION NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                notes TEXT,
                metadata JSONB
            );
        """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_trade_history (
                id BIGSERIAL PRIMARY KEY,
                trade_id TEXT NOT NULL UNIQUE,
                account_id TEXT NOT NULL DEFAULT 'default',
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price DOUBLE PRECISION NOT NULL,
                exit_price DOUBLE PRECISION NOT NULL,
                quantity DOUBLE PRECISION NOT NULL,
                entry_time TIMESTAMPTZ NOT NULL,
                exit_time TIMESTAMPTZ NOT NULL,
                holding_period_bars INTEGER NOT NULL,
                realized_pnl DOUBLE PRECISION NOT NULL,
                realized_pnl_pct DOUBLE PRECISION NOT NULL,
                fees DOUBLE PRECISION NOT NULL,
                exit_reason TEXT NOT NULL,
                params JSONB NOT NULL,
                metadata JSONB
            );
        """
        )
    elif domain == "system":
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id BIGSERIAL PRIMARY KEY,
                key TEXT NOT NULL UNIQUE,
                value TEXT,
                is_secret BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id BIGSERIAL PRIMARY KEY,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                acknowledged_at TIMESTAMPTZ
            );
        """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_results (
                id BIGSERIAL PRIMARY KEY,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                strategy_mode TEXT NOT NULL,
                target_metric TEXT NOT NULL,
                best_value DOUBLE PRECISION NOT NULL,
                trade_count INTEGER NOT NULL,
                sharpe_ratio DOUBLE PRECISION,
                calmar_ratio DOUBLE PRECISION,
                win_rate DOUBLE PRECISION,
                max_drawdown_pct DOUBLE PRECISION,
                total_return_pct DOUBLE PRECISION,
                best_params JSONB NOT NULL,
                run_timestamp TIMESTAMPTZ NOT NULL,
                favorite INTEGER NOT NULL DEFAULT 0,
                deployed INTEGER NOT NULL DEFAULT 0,
                checkpoint_fingerprint TEXT
            );
        """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_balances (
                id BIGSERIAL PRIMARY KEY,
                account_id TEXT NOT NULL DEFAULT 'default',
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                free_balance DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                locked_balance DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                total_balance DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (account_id, exchange, symbol)
            );
        """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                event_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                user_id TEXT,
                username TEXT,
                ip_address TEXT,
                user_agent TEXT,
                resource_id TEXT,
                resource_type TEXT,
                action TEXT,
                details TEXT,
                success INTEGER DEFAULT 1,
                error_message TEXT
            );
        """
        )


@pytest.fixture(scope="session")
def pg_container():
    """Session fixture: persistent background loop + PG pools + schemas.

    Tests requesting this fixture get a live, schema-initialized PostgreSQL
    with pools bound to a single always-running event loop (same lifecycle
    as the FastAPI app — sync_bridge submits all coroutines to it).
    """
    global _main_loop, _loop_thread, _db_semaphore  # noqa: PLW0602

    if _main_loop is None:
        # Create domain databases (fresh container may not have them)
        _ensure_domain_databases()

        # Run a persistent event loop in a background thread
        _main_loop = asyncio.new_event_loop()

        def _loop_runner():
            asyncio.set_event_loop(_main_loop)
            _main_loop.run_forever()

        _loop_thread = threading.Thread(target=_loop_runner, daemon=True)
        _loop_thread.start()

        from src.sync_bridge import set_main_loop

        set_main_loop(_main_loop)

        # Initialize pools + schemas inside the main loop (threadsafe)
        init_future = asyncio.run_coroutine_threadsafe(
            _init_pools_and_schemas(), _main_loop
        )
        init_future.result(timeout=60)

        # Create the database semaphore in the main event loop
        async def _create_semaphore():
            global _db_semaphore
            _db_semaphore = asyncio.Semaphore(1)

        sem_future = asyncio.run_coroutine_threadsafe(_create_semaphore(), _main_loop)
        sem_future.result(timeout=5)

        yield "container"

    # Teardown (session end): stop loop and clear the main-loop reference
    from src.sync_bridge import set_main_loop

    set_main_loop(None)
    if _main_loop is not None:
        _main_loop.call_soon_threadsafe(_main_loop.stop)
        if _loop_thread is not None:
            _loop_thread.join(timeout=5)
        _main_loop = None
        _loop_thread = None


# ── PG fixtures for integration tests ──────────────────────────────────────
@pytest.fixture(scope="session")
def pg_manager(pg_container):
    """The initialized PostgresConnectionManager singleton."""
    from src.pg_connection_manager import get_pg_manager

    return get_pg_manager()


@pytest.fixture(scope="session")
def pg_pool(pg_container, pg_manager):
    """An asyncpg pool bound to the candles domain (for raw SQL tests)."""
    return pg_manager.get_pool("candles")


@pytest.fixture
async def pg_db(pg_pool):
    """A raw asyncpg connection from the candles pool."""
    conn = await pg_pool.acquire()
    try:
        yield conn
    finally:
        await pg_pool.release(conn)


# ── Autouse PG mock for non-integration tests ──────────────────────────────
def _make_mock_pg_manager():
    """Create a mock PG manager for unit tests that don't need real DB."""
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.execute = AsyncMock(return_value="INSERT 0 1")

    class MockAcquireContext:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    class MockTransactionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    mock_conn.transaction = MagicMock(return_value=MockTransactionContext())

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=MockAcquireContext(mock_conn))
    mock_pool.close = AsyncMock()

    mock_pg_manager = MagicMock()
    mock_pg_manager.get_pool = MagicMock(return_value=mock_pool)
    mock_pg_manager._pools = {
        "candles": mock_pool,
        "batch_results": mock_pool,
        "paper_trading": mock_pool,
        "system": mock_pool,
    }
    mock_pg_manager.initialize = AsyncMock()
    mock_pg_manager.close_all = AsyncMock()
    return mock_pg_manager


# ── Per-test DB cleanup for integration tests ──────────────────────────────
TABLES_TO_TRUNCATE = [
    "paper_positions",
    "paper_trade_history",
    "paper_ledger_transactions",
    "paper_profiles",
    "batch_results",
    "candles",
    "settings",
    "audit_log",
]


@pytest_asyncio.fixture(autouse=True)
async def clean_pg_tables(request):
    """Truncate all tables before each integration test for isolation.

    Runs for all tests that depend on pg_container (real PG tests).
    Uses the session PG manager (background-loop pools) via run_coroutine_threadsafe.
    """
    # Check if test uses pg_container
    if "pg_container" not in request.fixturenames:
        yield
        return

    from src.pg_database import _ensure_pg_manager
    from src.sync_bridge import get_main_loop

    main_loop = get_main_loop()

    # If main loop not set up, skip cleanup
    if main_loop is None or main_loop.is_closed():
        yield
        return

    async def _cleanup():
        mgr = await _ensure_pg_manager()
        async with mgr.get_pool("paper_trading").acquire() as conn:
            await conn.execute(
                f"TRUNCATE {', '.join(TABLES_TO_TRUNCATE[:4])} RESTART IDENTITY CASCADE"
            )
        async with mgr.get_pool("batch_results").acquire() as conn:
            await conn.execute("TRUNCATE batch_results RESTART IDENTITY CASCADE")
        async with mgr.get_pool("candles").acquire() as conn:
            await conn.execute("TRUNCATE candles RESTART IDENTITY CASCADE")
        async with mgr.get_pool("system").acquire() as conn:
            await conn.execute("TRUNCATE settings, audit_log RESTART IDENTITY CASCADE")

    # Serialize test execution to avoid DB conflicts
    semaphore = asyncio.Semaphore(1)

    async def _serialized_cleanup():
        async with semaphore:
            await _cleanup()

    # Run cleanup before test
    cleanup_future = asyncio.run_coroutine_threadsafe(_serialized_cleanup(), main_loop)
    cleanup_future.result(timeout=30)

    yield

    # Run cleanup after test
    cleanup_future = asyncio.run_coroutine_threadsafe(_serialized_cleanup(), main_loop)
    cleanup_future.result(timeout=30)


@pytest_asyncio.fixture(autouse=True)
async def close_pg_pools_after_test(request):
    """Close per-loop PG pools after each test to avoid connection leaks across loops.

    Does NOT touch pg_container's background-loop pools (set via _pg_manager),
    since clean_pg_tables depends on them being available.
    """
    yield

    from src.pg_database import _managers_by_loop
    from src.sync_bridge import get_main_loop

    # Close all per-loop managers created by _ensure_pg_manager() during tests.
    # Keep pg_container's background-loop pools (main_loop) since clean_pg_tables
    # depends on them being available. Do NOT clear the dict — main_loop's entry
    # must persist so the next test reuses the same manager (avoids pool
    # duplication and connection leaks on the main loop).
    main_loop = get_main_loop()
    for loop_id, mgr in list(_managers_by_loop.items()):
        if main_loop is not None and loop_id == id(main_loop):
            continue  # Keep pg_container's background-loop pools
        try:
            await mgr.close()
        except Exception:  # noqa: S110 — cleanup must not raise
            pass
        _managers_by_loop.pop(loop_id, None)


@pytest.fixture(autouse=True)
def mock_pg_database(monkeypatch, request):
    """Mock PostgreSQL database functions for tests that don't need real DB.

    Skipped (real PG used) for integration tests requesting pg_container,
    pg_db, pg_pool, pg_manager, or audit_logger fixtures.
    """
    # Skip mocking for tests that explicitly request real PG fixtures
    integration_fixtures = {
        "pg_db",
        "pg_pool",
        "pg_manager",
        "pg_container",
        "audit_logger",
    }
    # Also skip for known integration test modules that need real PG
    integration_modules = {
        "test_settings_exchanges",
        "test_validator",
        "test_backtester_pg_integration",
        "test_pg_connection_manager",
        "test_optuna_filter_params",
        "test_paper_engine_entry_logic",
        "test_routes_paper",
        "test_routes_batch",
        "test_routes_oms",
        "test_paper_polling_extended",
        "test_timeframe_scaling",
        "test_ui_traceability",
        "test_security_audit",
        "test_security_regressions",
        "test_paper_epic_l7v_bugs",
        "test_paper_ledger",
        "test_paper_polling_optimization_l7v12",
        "test_paper_engine",
        "test_batch_all_metrics",
        "test_batch_auto_validate_feature_red",
        "test_batch_lookback_days",
        "test_batch_optimize_forensic",
        "test_batch_route_auto_validate",
        "test_config",
        "test_data_loader",
        "test_database_maintenance",
        "test_db_maintenance_multi",
        "test_execution_algorithms",
        "test_factors",
        "test_gap_coverage",
        "test_indicators",
        "test_logger",
        "test_logger_rotation",
        "test_migrate_data",
        "test_monte_carlo",
        "test_new_metrics",
        "test_oms",
        "test_optimizer",
        "test_options",
        "test_optuna_api_lifecycle_8ig4",
        "test_optuna_batch_process_8ig5",
        "test_optuna_benchmark_certification_8ig6",
        "test_optuna_storage_coordinator_8ig3",
        "test_optuna_worker_parity_8ig2",
        "test_parameter_flow",
        "test_param_propagation_regressions",
        "test_portfolio_aggregator",
        "test_portfolio_connectors",
        "test_portfolio_rebalancer",
        "test_portfolio_risk",
        "test_portfolio_risk_budget",
        "test_providers",
        "test_regime",
        "test_risk_manager",
        "test_session_vwap",
        "test_settings",
        "test_stock_downloader",
        "test_stock_execution",
        "test_strategy",
        "test_symbol_validation",
        "test_tasks",
        "test_tasks_routes",
        "test_tear_sheet_generation",
        "test_unified_adapter_or_delete",
        "test_upsampling_and_audit",
        "test_web_ui",
        "test_wfo",
        "test_ws_server",
        "test_ws_server_broadcast",
    }

    test_module = (
        request.module.__name__.replace("tests.", "") if request.module else ""
    )

    if (
        integration_fixtures & set(request.fixturenames)
        or test_module in integration_modules
    ):
        # Real DB tests: don't mock the PG layer
        yield
        return

    import pandas as pd

    # Import the patch targets BEFORE patching src.pg_database. If
    # src.data_loader (which re-exports load/save_candles_pg via module-level
    # `from src.pg_database import ...`, and whose import chain pulls in
    # src.database, which does the same) is first imported *after* the patch,
    # it binds the MOCK permanently and monkeypatch records the mock as the
    # "original" — undo then restores the mock, leaking it into every later
    # test (observed: PG-integration tests seeing empty caches after unit
    # tests in the same session).
    # Import the patch targets BEFORE patching src.pg_database. If
    # src.data_loader (which re-exports load/save_candles_pg via module-level
    # `from src.pg_database import ...`, and whose import chain pulls in
    # src.database, which does the same) is first imported *after* the patch,
    # it binds the MOCK permanently and monkeypatch records the mock as the
    # "original" — undo then restores the mock, leaking it into every later
    # test (observed: PG-integration tests seeing empty caches after unit
    # tests in the same session).
    import src.data_loader
    import src.pg_database  # noqa: F401

    mock_save = AsyncMock(return_value=100)
    mock_load = AsyncMock(
        return_value=pd.DataFrame(
            {
                "timestamp": [],
                "open": [],
                "high": [],
                "low": [],
                "close": [],
                "volume": [],
                "exchange": [],
                "symbol": [],
                "timeframe": [],
            }
        )
    )
    monkeypatch.setattr("src.pg_database.save_candles_pg", mock_save)
    monkeypatch.setattr("src.pg_database.load_candles_pg", mock_load)
    monkeypatch.setattr("src.data_loader.save_candles_pg", mock_save)
    monkeypatch.setattr("src.data_loader.load_candles_pg", mock_load)

    # NOTE: Do NOT mock src.pg_connection_manager.get_pg_manager here.
    # That function is used by the pg_container session fixture to initialize
    # real connection pools. Mocking it would break integration tests that
    # depend on pg_container. Unit tests that need a mock manager should
    # create their own mock locally.

    yield
