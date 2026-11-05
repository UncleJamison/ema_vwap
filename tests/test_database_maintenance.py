"""
Tests for DB maintenance features: get_db_info, repair_database, vacuum_database,
and candle data round-trip (export equivalent).

These tests were written for the old SQLite CandleDatabase. After the migration to
PostgreSQL, the equivalent maintenance functions live in:
  - src.db_connection_manager.get_db_info()
  - src.db_maintenance_multi.get_db_info_all()
  - src.db_maintenance_multi.repair_database_all()
  - src.db_maintenance_multi.vacuum_database_all()

Export (CSV/JSON) is no longer a CandleDatabase method; tests verify the candle
data round-trips through save_candles() -> load_candles() -> pandas to_csv/to_json.
"""

import pandas as pd

from src.data_loader import DataLoader
from src.database import CandleDatabase
from src.db_connection_manager import get_db_info
from src.db_maintenance_multi import (
    get_db_info_all,
    repair_database_all,
    vacuum_database_all,
)

# ---------------------------------------------------------------------------
# get_db_info / get_db_info_all — require real PostgreSQL (pg_container)
# ---------------------------------------------------------------------------


def test_get_db_info_on_healthy_db(pg_container):
    """get_db_info() should return per-domain info with postgresql engine."""
    info = get_db_info()
    assert isinstance(info, dict)
    for domain_info in info.values():
        assert domain_info["type"] == "postgresql"
        assert domain_info["pool_available"] is True


def test_get_db_info_all_on_healthy_db(pg_container):
    """get_db_info_all() should report all domains healthy."""
    info = get_db_info_all()
    assert info["engine"] == "postgresql"
    assert info["all_healthy"] is True
    assert "per_db" in info
    for domain in ("candles", "batch_results", "paper_trading", "system"):
        assert domain in info["per_db"]
        assert info["per_db"][domain]["status"] == "healthy"


# ---------------------------------------------------------------------------
# repair_database — require real PostgreSQL (pg_container)
# ---------------------------------------------------------------------------


def test_repair_database_healthy_reports_healthy(pg_container):
    """repair_database_all() should report success on healthy domains."""
    result = repair_database_all()
    assert result["status"] == "success"
    assert result["cross_db_consistent"] is True
    for domain in ("candles", "batch_results", "paper_trading", "system"):
        assert result["results_per_db"][domain]["status"] == "success"


def test_repair_database_all_cross_db_consistent(pg_container):
    """repair_database_all() should be cross-db consistent."""
    result = repair_database_all()
    assert result["cross_db_consistent"] is True


# ---------------------------------------------------------------------------
# vacuum_database — require real PostgreSQL (pg_container)
# ---------------------------------------------------------------------------


def test_vacuum_database(pg_container):
    """vacuum_database_all() should report success."""
    result = vacuum_database_all()
    assert result["status"] == "success"
    assert result["engine"] == "postgresql"


# ---------------------------------------------------------------------------
# Candle data round-trip (replaces export_candles_csv / export_candles_json)
# ---------------------------------------------------------------------------


def _clean_candles(exchange: str, symbol: str, timeframe: str) -> None:
    """Delete a symbol's candles so round-trip counts are exact."""
    import asyncio as _asyncio

    from src.sync_bridge import get_main_loop

    async def _delete():
        from src.pg_connection_manager import get_pg_manager

        pool = get_pg_manager().get_pool("candles")
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM candles WHERE exchange = $1 AND symbol = $2 "
                "AND timeframe = $3",
                exchange.lower(),
                symbol,
                timeframe.lower(),
            )

    _asyncio.run_coroutine_threadsafe(_delete(), get_main_loop()).result(timeout=10)


def test_candles_round_trip_csv(pg_container):
    """Save candles, load them back, and verify CSV serialization."""
    _clean_candles("kucoin", "BTC/USD", "5m")
    db = CandleDatabase(domain="candles")
    df = DataLoader.generate_synthetic_candles(num_bars=5, seed=42)
    saved = db.save_candles("kucoin", "BTC/USD", "5m", df)
    assert saved == 5

    loaded = db.load_candles("kucoin", "BTC/USD", "5m")
    assert len(loaded) == 5

    csv_str = loaded.to_csv(index=False)
    lines = csv_str.splitlines()
    assert "timestamp" in lines[0]
    assert len(lines) == 6  # header + 5 rows


def test_candles_round_trip_json(pg_container):
    """Save candles, load them back, and verify JSON serialization."""
    _clean_candles("gemini", "ETH/USD", "1h")
    db = CandleDatabase(domain="candles")
    df = DataLoader.generate_synthetic_candles(num_bars=3, seed=99)
    saved = db.save_candles("gemini", "ETH/USD", "1h", df)
    assert saved == 3

    loaded = db.load_candles("gemini", "ETH/USD", "1h")
    assert len(loaded) == 3

    json_str = loaded.to_json(orient="records", date_format="iso")
    assert "timestamp" in json_str or '"timestamp"' in json_str


def test_candles_round_trip_empty_csv(pg_container):
    """Loading candles for an empty symbol should produce a valid empty CSV."""
    db = CandleDatabase(domain="candles")
    loaded = db.load_candles("kucoin", "EMPTY/USD", "5m")
    assert isinstance(loaded, pd.DataFrame)
    assert len(loaded) == 0

    # to_csv on an empty DataFrame should not crash and should include timestamp column
    csv_str = loaded.to_csv(index=False)
    assert isinstance(csv_str, str)
    if loaded.columns.tolist():
        assert "timestamp" in loaded.columns


def test_candles_round_trip_empty_json(pg_container):
    """Loading candles for an empty symbol should produce a valid empty JSON."""
    db = CandleDatabase(domain="candles")
    loaded = db.load_candles("kucoin", "EMPTY/USD", "5m")
    assert len(loaded) == 0

    json_str = loaded.to_json(orient="records", date_format="iso")
    assert json_str == "[]"


# ---------------------------------------------------------------------------
# Legacy SQLite-only concepts — adapted for PG
# ---------------------------------------------------------------------------


def test_repair_database_empty_path_no_file(pg_container):
    """PG repair has no concept of a missing file; repair_all handles gracefully."""
    result = repair_database_all()
    assert result["status"] in ("success", "partial_failure")


def test_get_db_info_integrity_check_on_new_db(pg_container):
    """PG domains report healthy on a fresh container."""
    info = get_db_info_all()
    assert info["all_healthy"] is True


def test_repair_database_quarantine_and_rebuild(pg_container):
    """PG repair_all runs VACUUM ANALYZE on all domains without quarantine."""
    result = repair_database_all()
    assert result["any_quarantined"] is False
    assert result["status"] == "success"
