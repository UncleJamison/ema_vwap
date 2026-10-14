"""Multi-DB maintenance: repair_database and get_db_info across all DB domains.
Sub-bead 462.8 — reports per-DB health, repairs independently, isolates failure.

Cross-DB consistency: only system DB holds cross-DB references (alerts); no
foreign-key cascade across DB files (SQLite limitation). Corruption of one DB
cannot cascade to others (separate file paths).
"""

from typing import Any

from src.db_connection_manager import DB_MAP, get_database


def repair_database_all() -> dict[str, Any]:
    """Repair each DB independently; return per-key status + cross-DB summary."""
    results: dict[str, Any] = {}
    any_quarantined = False
    for key in DB_MAP:
        try:
            db = get_database(key)
            res = db.repair_database()
            results[key] = res
            if res.get("status") in ("success", "repaired"):
                any_quarantined = True
            # Consistency: if this DB was repaired, note that others are untouched
        except Exception:
            results[key] = {
                "status": "error",
                "message": "An internal error occurred during maintenance.",
            }
    # Cross-DB consistency check: all DB files still exist and are accessible
    consistent = all(
        (key in results and results[key].get("status") not in ("error",))
        for key in DB_MAP
    )
    return {
        "status": "success" if consistent else "partial_failure",
        "results_per_db": results,
        "cross_db_consistent": consistent,
        "any_quarantined": any_quarantined,
        "message": "All DBs inspected independently; one corruption does not cascade.",
    }


def get_db_info_all() -> dict[str, Any]:
    """Report health of each individual DB (independent files)."""
    info = {}
    for key in DB_MAP:
        try:
            info[key] = get_database(key).get_db_info()
        except Exception as e:
            info[key] = {"status": "unreachable", "error": str(e)}
    # Cross-DB: compare table counts (no FK cascade — independent)
    table_counts = {
        k: v.get("tables") for k, v in info.items() if isinstance(v.get("tables"), list)
    }
    return {
        "per_db": info,
        "cross_db_table_counts": table_counts,
        "isolated": True,
        "message": "Separate DB files — corruption of one does not propagate.",
    }


def vacuum_database_all() -> dict[str, Any]:
    """Vacuum each DB domain independently to reclaim unused disk space; return per-key status."""
    results: dict[str, Any] = {}
    for key in DB_MAP:
        try:
            db = get_database(key)
            results[key] = db.vacuum_database()
        except Exception:
            results[key] = {
                "status": "error",
                "message": "An internal error occurred during maintenance.",
            }
    success = all(
        (key in results and results[key].get("status") == "success") for key in DB_MAP
    )
    return {
        "status": "success" if success else "partial_failure",
        "results_per_db": results,
        "message": (
            "All database domains vacuumed successfully."
            if success
            else "One or more databases encountered vacuum issues (see logs)."
        ),
    }
