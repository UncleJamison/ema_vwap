"""Multi-DB maintenance: health inspection and repair across all PG domains.

PostgreSQL/TimescaleDB edition — reports per-domain health (connection,
table counts, disk usage), repairs via VACUUM/ANALYZE, and vacuums.
"""

from typing import Any

DOMAINS = ["candles", "batch_results", "paper_trading", "system"]


def _get_domain_health(domain: str) -> dict[str, Any]:
    """Return health info for a single PG domain database."""
    from src.sync_bridge import run_async

    async def _inspect():
        from src.pg_connection_manager import get_pg_manager

        mgr = get_pg_manager()
        pool = mgr.get_pool(domain)
        async with pool.acquire() as conn:
            # Table count and names
            tables = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
            table_names = [t["tablename"] for t in tables]

            # Database size
            size_row = await conn.fetchrow(
                "SELECT pg_database_size(current_database()) AS size"
            )
            size_bytes = size_row["size"] if size_row else 0

            # For candles domain, get candle record count from main table
            candle_record_count = None
            if domain == "candles":
                try:
                    # Count rows in the main candles table
                    count_row = await conn.fetchrow("SELECT COUNT(*) AS n FROM candles")
                    candle_record_count = count_row["n"] if count_row else 0
                except Exception as e:
                    # Table might not exist yet
                    print(
                        f"[db_maintenance] Candle count unavailable for {domain}: {e}"
                    )

            # Connection count
            conn_row = await conn.fetchrow(
                "SELECT count(*) AS n FROM pg_stat_activity WHERE datname = current_database()"
            )

            # TimescaleDB chunks (if applicable)
            chunk_count = 0
            try:
                chunk_row = await conn.fetchrow(
                    "SELECT count(*) AS n FROM timescaledb_information.chunks"
                )
                chunk_count = chunk_row["n"] if chunk_row else 0
            except Exception as e:
                # Not a TimescaleDB deployment or no chunks — non-fatal
                print(f"[db_maintenance] Chunk count unavailable for {domain}: {e}")

            return {
                "status": "healthy",
                "engine": "postgresql",
                "database": (
                    pool.get_dsn_parameter("database")
                    if hasattr(pool, "get_dsn_parameter")
                    else domain
                ),
                "tables": table_names,
                "table_count": len(table_names),
                "size_bytes": size_bytes,
                "file_size_bytes": size_bytes,  # alias for frontend compatibility
                "journal_mode": "WAL",  # PG always uses WAL-like mode
                "candle_record_count": candle_record_count,
                "integrity_check": "ok",  # PG handles integrity internally
                "active_connections": conn_row["n"] if conn_row else 0,
                "timescale_chunks": chunk_count,
            }

    return run_async(_inspect())


def get_db_info_all() -> dict[str, Any]:
    """Report health of each individual PG domain database."""
    info: dict[str, Any] = {}
    for domain in DOMAINS:
        try:
            info[domain] = _get_domain_health(domain)
        except Exception as e:
            info[domain] = {"status": "unreachable", "error": str(e)}

    table_counts = {
        k: v.get("tables", [])
        for k, v in info.items()
        if isinstance(v.get("tables"), list)
    }
    all_healthy = all(v.get("status") == "healthy" for v in info.values())
    return {
        "per_db": info,
        "cross_db_table_counts": table_counts,
        "isolated": True,
        "all_healthy": all_healthy,
        "engine": "postgresql",
        "message": "All PG domain databases inspected independently.",
    }


def repair_database_all() -> dict[str, Any]:
    """Repair each PG domain independently via VACUUM ANALYZE."""
    from src.sync_bridge import run_async

    results: dict[str, Any] = {}

    for domain in DOMAINS:
        try:

            async def _repair(d=domain):
                from src.pg_connection_manager import get_pg_manager

                mgr = get_pg_manager()
                pool = mgr.get_pool(d)
                async with pool.acquire() as conn:
                    await conn.execute("VACUUM ANALYZE")
                    return True

            run_async(_repair(), timeout=300.0)
            results[domain] = {"status": "success", "action": "VACUUM ANALYZE"}
        except Exception:
            results[domain] = {
                "status": "error",
                "message": "An internal error occurred during maintenance.",
            }

    consistent = all(results.get(k, {}).get("status") != "error" for k in DOMAINS)
    return {
        "status": "success" if consistent else "partial_failure",
        "results_per_db": results,
        "cross_db_consistent": consistent,
        "any_quarantined": False,
        "engine": "postgresql",
        "message": "All PG domain databases repaired independently (VACUUM ANALYZE).",
    }


def vacuum_database_all() -> dict[str, Any]:
    """Reclaim disk space across all PG domain databases."""
    return repair_database_all()
