"""Report PostgreSQL candle counts and timestamp coverage by stored source tag."""

import asyncio
from datetime import datetime
from typing import Any

from src.pg_database import _ensure_pg_manager


async def fetch_candle_inventory() -> list[dict[str, Any]]:
    """Fetch compact per-exchange, symbol, and timeframe inventory statistics."""
    pg_manager = await _ensure_pg_manager()
    pool = pg_manager.get_pool("candles")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                exchange,
                symbol,
                timeframe,
                data_origin,
                source_timeframe,
                COUNT(*) AS candle_count,
                COUNT(DISTINCT timestamp) AS distinct_timestamps,
                MIN(timestamp) AS first_timestamp,
                MAX(timestamp) AS last_timestamp
            FROM candles
            GROUP BY exchange, symbol, timeframe, data_origin, source_timeframe
            ORDER BY exchange, symbol, timeframe, data_origin, source_timeframe
            """
        )

    return [dict(row) for row in rows]


def _format_timestamp(value: datetime | None) -> str:
    return value.isoformat() if value is not None else "n/a"


def audit_candles(inventory: list[dict[str, Any]]) -> None:
    """Print a database-wide source summary and detailed coverage inventory."""
    print("=== POSTGRESQL CANDLE INVENTORY ===")

    if not inventory:
        print("No candle rows found.")
        return

    totals: dict[tuple[str, str], int] = {}
    source_totals: dict[str, int] = {}
    origin_totals: dict[str, int] = {}
    overall_total = 0
    for row in inventory:
        exchange = row["exchange"]
        timeframe = row["timeframe"]
        origin = row["data_origin"]
        count = int(row["candle_count"])
        totals[(exchange, timeframe)] = totals.get((exchange, timeframe), 0) + count
        source_totals[exchange] = source_totals.get(exchange, 0) + count
        origin_totals[origin] = origin_totals.get(origin, 0) + count
        overall_total += count

    synthetic_total = origin_totals.get("synthetic", 0)
    synthetic_pct = synthetic_total / overall_total * 100

    print(f"Total candles: {overall_total:,}")
    print(
        f"Rows with synthetic data origin: {synthetic_total:,} "
        f"({synthetic_pct:.1f}%)"
    )
    print(
        "Rows by data origin: "
        + ", ".join(
            f"{origin}={count:,}" for origin, count in sorted(origin_totals.items())
        )
    )
    print(
        "Provider means the row was fetched from the configured provider after "
        "provenance tracking was enabled; unknown legacy rows are not verified."
    )

    print("\nCounts by stored exchange tag:")
    print(f"{'Exchange':<18} {'Candles':>14}")
    for exchange, count in sorted(source_totals.items()):
        print(f"{exchange:<18} {count:>14,}")

    print("\nCounts by stored exchange tag and timeframe:")
    print(f"{'Exchange':<18} {'TF':<8} {'Candles':>14}")
    for (exchange, timeframe), count in sorted(totals.items()):
        print(f"{exchange:<18} {timeframe:<8} {count:>14,}")

    print("\nCoverage by exchange, symbol, and timeframe:")
    print(
        f"{'Exchange':<18} {'Symbol':<16} {'TF':<8} {'Origin':<12} "
        f"{'Source TF':<10} {'Candles':>12} {'First timestamp':<28} "
        f"{'Last timestamp':<28} {'Duplicate timestamps':>20}"
    )
    for row in inventory:
        duplicate_count = int(row["candle_count"]) - int(row["distinct_timestamps"])
        print(
            f"{row['exchange']:<18} {row['symbol']:<16} {row['timeframe']:<8} "
            f"{row['data_origin']:<12} {row['source_timeframe'] or 'n/a'!s:<10} "
            f"{int(row['candle_count']):>12,} "
            f"{_format_timestamp(row['first_timestamp']):<28} "
            f"{_format_timestamp(row['last_timestamp']):<28} "
            f"{duplicate_count:>20,}"
        )


async def audit_candles_pg() -> None:
    """Run and print the inventory against the configured PostgreSQL candles DB."""
    audit_candles(await fetch_candle_inventory())


if __name__ == "__main__":
    asyncio.run(audit_candles_pg())
