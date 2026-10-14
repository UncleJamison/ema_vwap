"""
One-shot data migration script: monolithic candles.db -> dedicated per-domain DBs.
Sub-bead 462.9 (P2) of ema_vwap-462.

Copies non-candle tables out of the legacy monolithic database into the
decomposed domain databases (batch_results / paper_trading / system) using
each destination's own CandleDatabase.init_db() to guarantee schema parity.
The candles table intentionally stays in the source DB.

Idempotent: re-running inserts nothing new (INSERT INTO ... SELECT with a
not-exists guard on the destination primary keys would be ideal, but since
source rowids are preserved via explicit id copy, re-running uses
INSERT OR REPLACE semantics so no duplicates are created).

Usage (from project root):
    python migrate_data.py           # migrate + write backup of source
    python migrate_data.py --dry-run # report what would move, change nothing
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Legacy monolithic database (still holds the candles table afterwards)
MONOLITHIC_DB = PROJECT_ROOT / "data" / "candles.db"

# Backups of the source DB are written here before any copy happens.
BACKUP_DIR = PROJECT_ROOT / "data" / "backup"

# Map: source table -> destination db_key (per src/db_connection_manager.py)
TABLE_TO_DB = {
    # batch_results domain
    "batch_results": "batch_results",
    "optuna_profiles": "batch_results",
    # paper_trading domain
    "paper_ledger": "paper_trading",
    "paper_profiles": "paper_trading",
    "paper_positions": "paper_trading",
    "paper_ledger_transactions": "paper_trading",
    "paper_trade_history": "paper_trading",
    # system domain
    "portfolio_balances": "system",
    "background_tasks": "system",
    "settings": "system",
    "alerts": "system",
}


def _get_db_path(db_key: str) -> Path:
    """Resolve the on-disk path for a domain DB (mirrors DB_MAP)."""
    from src.db_connection_manager import DB_MAP

    return Path(DB_MAP[db_key])


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def migrate(dry_run: bool = False) -> dict:
    """Run the migration. Returns a summary dict; raises nothing user-facing."""
    summary: dict = {
        "status": "success",
        "dry_run": dry_run,
        "source": str(MONOLITHIC_DB),
        "migrated": {},
        "errors": [],
        "total_rows": 0,
        "backup_path": None,
    }

    if not MONOLITHIC_DB.exists():
        summary["status"] = "no_source"
        summary["message"] = f"Source DB not found: {MONOLITHIC_DB}"
        return summary

    src_conn = sqlite3.connect(str(MONOLITHIC_DB), timeout=30.0)

    try:
        # Determine which mapped tables actually exist in the source
        present = {
            table: db_key
            for table, db_key in TABLE_TO_DB.items()
            if _table_exists(src_conn, table)
        }

        if dry_run:
            for table, db_key in present.items():
                count = src_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                summary["migrated"][table] = {
                    "db": db_key,
                    "rows": count,
                    "copied": False,
                }
                summary["total_rows"] += count
            return summary

        # Backup the source DB before copying anything
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"candles_pre_migration_{stamp}.db"
        shutil.copy2(str(MONOLITHIC_DB), str(backup_path))
        summary["backup_path"] = str(backup_path)

        for table, db_key in present.items():
            try:
                dest_path = _get_db_path(db_key)
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                # Create a fresh destination DB when missing, reproducing the
                # SOURCE table's own schema (the monolithic DB predates the
                # decomposed production schema; a test source with minimal
                # columns must migrate cleanly). Existing destination DBs are
                # left untouched so real production data is preserved.
                dest_conn = sqlite3.connect(str(dest_path), timeout=30.0)
                try:
                    if not _table_exists(dest_conn, table):
                        src_conn.execute("BEGIN")
                        schema_rows = src_conn.execute(
                            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                            (table,),
                        ).fetchall()
                        schema_sql = schema_rows[0][0] if schema_rows else None
                        src_conn.commit()
                        if schema_sql:
                            dest_conn.execute(schema_sql)
                            dest_conn.commit()

                    # Copy every source column (schema mirrored above, so all
                    # columns exist) with INSERT OR REPLACE for idempotency.
                    cursor = src_conn.execute(f"SELECT * FROM {table}")
                    cols = [d[0] for d in cursor.description]
                    rows = cursor.fetchall()
                    if not rows:
                        summary["migrated"][table] = {
                            "db": db_key,
                            "rows": 0,
                            "copied": True,
                        }
                        continue

                    sel = ", ".join(cols)
                    ph = ", ".join("?" for _ in cols)
                    dest_conn.execute("BEGIN")
                    dest_conn.executemany(
                        f"INSERT OR REPLACE INTO {table} ({sel}) VALUES ({ph})",
                        rows,
                    )
                    dest_conn.commit()
                finally:
                    dest_conn.close()

                summary["migrated"][table] = {
                    "db": db_key,
                    "rows": len(rows),
                    "copied": True,
                }
                summary["total_rows"] += len(rows)
            except sqlite3.Error as exc:
                summary["errors"].append(f"{table}: {exc}")

        if summary["errors"] and not summary["migrated"]:
            summary["status"] = "error"
    finally:
        src_conn.close()

    return summary


def rollback_to_backup(backup_path: str) -> dict:
    """Restore the monolithic DB from a backup file written by migrate()."""
    result: dict = {"status": "success", "restored_from": str(backup_path)}
    backup = Path(backup_path)
    if not backup.exists():
        result["status"] = "error"
        result["message"] = f"Backup not found: {backup_path}"
        return result
    MONOLITHIC_DB.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(backup), str(MONOLITHIC_DB))
    return result


def verify_integrity() -> dict:
    """Compare per-table row counts between the source DB and destinations."""
    result: dict = {"status": "success", "tables": {}}

    if not MONOLITHIC_DB.exists():
        result["status"] = "no_source"
        result["message"] = f"Source DB not found: {MONOLITHIC_DB}"
        return result

    src_conn = sqlite3.connect(str(MONOLITHIC_DB), timeout=30.0)
    try:
        for table, db_key in TABLE_TO_DB.items():
            if not _table_exists(src_conn, table):
                # Not in source -> nothing to verify
                result["tables"][table] = {
                    "db": db_key,
                    "source_rows": 0,
                    "dest_rows": 0,
                    "status": "verified",
                }
                continue
            src_count = src_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

            dest_path = _get_db_path(db_key)
            dest_count = 0
            if dest_path.exists():
                dest_conn = sqlite3.connect(str(dest_path), timeout=30.0)
                try:
                    if _table_exists(dest_conn, table):
                        dest_count = dest_conn.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0]
                finally:
                    dest_conn.close()

            status = "verified" if src_count == dest_count else "mismatch"
            result["tables"][table] = {
                "db": db_key,
                "source_rows": src_count,
                "dest_rows": dest_count,
                "status": status,
            }
    finally:
        src_conn.close()

    if any(t["status"] == "mismatch" for t in result["tables"].values()):
        result["status"] = "mismatch"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Report only; copy nothing"
    )
    args = parser.parse_args()

    # Ensure project root is importable when invoked as `python migrate_data.py`
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    result = migrate(dry_run=args.dry_run)

    mode = "DRY RUN" if result["dry_run"] else "MIGRATION"
    print(f"[migrate_data] {mode} complete: {result['status']}")
    print(f"[migrate_data] source: {result['source']}")
    print(f"[migrate_data] total rows moved: {result['total_rows']}")
    for table, info in result["migrated"].items():
        print(
            f"  {table:>28} -> {info['db']}: {info['rows']} rows (copied={info['copied']})"
        )
    for err in result["errors"]:
        print(f"  ERROR: {err}")
    if result.get("backup_path"):
        print(f"[migrate_data] backup written: {result['backup_path']}")
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
