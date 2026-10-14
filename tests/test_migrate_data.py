"""Tests for migrate_data.py (462.9 migration script)."""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import migrate_data


@pytest.fixture
def clean_dest_dbs(tmp_path):
    """Remove destination DB files before/after each test."""
    leftovers = [
        tmp_path / "batch_results.db",
        tmp_path / "paper_trading.db",
        tmp_path / "system.db",
    ]
    for p in leftovers:
        if p.exists():
            p.unlink()
    yield leftovers
    for p in leftovers:
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


def _make_source(tmp_path):
    """Create a monolithic source DB with one row per mapped table."""
    src = tmp_path / "candles.db"
    conn = sqlite3.connect(str(src))
    for table in [
        "candles",
        "batch_results",
        "paper_ledger",
        "paper_profiles",
        "paper_positions",
        "paper_ledger_transactions",
        "paper_trade_history",
        "optuna_profiles",
        "portfolio_balances",
        "background_tasks",
        "settings",
        "alerts",
    ]:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, k TEXT)"
        )
        conn.execute(f"INSERT INTO {table} (k) VALUES ('src')")
    conn.commit()
    conn.close()
    return src


def test_migrate_copies_tables_to_dedicated_dbs(tmp_path, clean_dest_dbs):
    """Verify migration copies mapped tables to correct destination DBs."""
    src = _make_source(tmp_path)

    # Build a mock DB_MAP that points to tmp_path
    mock_db_map = {
        "batch_results": str(tmp_path / "batch_results.db"),
        "paper_trading": str(tmp_path / "paper_trading.db"),
        "system": str(tmp_path / "system.db"),
    }

    with (
        patch.object(migrate_data, "MONOLITHIC_DB", src),
        patch.object(migrate_data, "BACKUP_DIR", tmp_path / "backup"),
        patch.object(
            migrate_data, "_get_db_path", side_effect=lambda k: Path(mock_db_map[k])
        ),
    ):
        result = migrate_data.migrate()

    assert result["status"] == "success", f"Migration failed: {result}"
    assert result["errors"] == [], f"Unexpected errors: {result['errors']}"
    assert result["total_rows"] > 0

    # Verify candles stayed in source (not copied)
    src_conn = sqlite3.connect(str(src))
    assert src_conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 1
    src_conn.close()

    # Verify destination DBs exist and have correct tables
    dest_counts = {}
    for db_key in ("batch_results", "paper_trading", "system"):
        db_path = tmp_path / f"{db_key}.db"
        assert db_path.exists(), f"{db_key}.db missing"
        conn = sqlite3.connect(str(db_path))
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        dest_counts[db_key] = tables
        conn.close()

    assert (
        "background_tasks" in dest_counts["system"]
    ), "background_tasks not in system.db"
    assert "settings" in dest_counts["system"], "settings not in system.db"
    assert "alerts" in dest_counts["system"], "alerts not in system.db"
    assert (
        "optuna_profiles" in dest_counts["batch_results"]
    ), "optuna_profiles not in batch_results.db"
    assert (
        "paper_ledger" in dest_counts["paper_trading"]
    ), "paper_ledger not in paper_trading.db"


def test_migrate_idempotent_rerun(tmp_path, clean_dest_dbs):
    """Verify migration can be safely re-run without duplicating data."""
    src = _make_source(tmp_path)

    mock_db_map = {
        "batch_results": str(tmp_path / "batch_results.db"),
        "paper_trading": str(tmp_path / "paper_trading.db"),
        "system": str(tmp_path / "system.db"),
    }

    with (
        patch.object(migrate_data, "MONOLITHIC_DB", src),
        patch.object(migrate_data, "BACKUP_DIR", tmp_path / "backup"),
        patch.object(
            migrate_data, "_get_db_path", side_effect=lambda k: Path(mock_db_map[k])
        ),
    ):
        r1 = migrate_data.migrate()
        r2 = migrate_data.migrate()

    assert r1["status"] == "success", f"First run failed: {r1}"
    assert r2["status"] == "success", f"Second run failed: {r2}"
    # Verify counts match (no duplicates)
    conn = sqlite3.connect(str(tmp_path / "system.db"))
    alerts1 = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    conn.close()
    assert alerts1 == 1, f"Expected 1 alert row, got {alerts1}"


def test_migrate_no_source_returns_no_source(tmp_path):
    """Verify migration handles missing source DB gracefully."""
    missing = tmp_path / "missing.db"
    with patch.object(migrate_data, "MONOLITHIC_DB", missing):
        result = migrate_data.migrate()
    assert result["status"] == "no_source"
    assert "not found" in result["message"]


def test_rollback_restores_monolithic_db(tmp_path):
    """Verify rollback restores the monolithic DB from backup."""
    import shutil

    src = _make_source(tmp_path)
    backup = tmp_path / "candles_backup_1.db"
    with (
        patch.object(migrate_data, "MONOLITHIC_DB", src),
        patch.object(migrate_data, "BACKUP_DIR", tmp_path / "backup"),
    ):
        shutil.copy2(str(src), str(backup))

        # Corrupt the source
        conn = sqlite3.connect(str(src))
        conn.execute("DROP TABLE candles")
        conn.commit()
        conn.close()
        assert not any(
            r[0] == "candles"
            for r in sqlite3.connect(str(src))
            .execute("SELECT name FROM sqlite_master WHERE type='table'")
            .fetchall()
        )

        migrate_data.rollback_to_backup(str(backup))
        restored = sqlite3.connect(str(src))
        assert "candles" in [
            r[0]
            for r in restored.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        restored.close()


def test_verify_integrity_passes_after_migration(tmp_path, clean_dest_dbs):
    """Verify integrity check passes after successful migration."""
    src = _make_source(tmp_path)

    mock_db_map = {
        "batch_results": str(tmp_path / "batch_results.db"),
        "paper_trading": str(tmp_path / "paper_trading.db"),
        "system": str(tmp_path / "system.db"),
    }

    with (
        patch.object(migrate_data, "MONOLITHIC_DB", src),
        patch.object(migrate_data, "BACKUP_DIR", tmp_path / "backup"),
        patch.object(
            migrate_data, "_get_db_path", side_effect=lambda k: Path(mock_db_map[k])
        ),
    ):
        migrate_data.migrate()

    with (
        patch.object(migrate_data, "MONOLITHIC_DB", src),
        patch.object(
            migrate_data, "_get_db_path", side_effect=lambda k: Path(mock_db_map[k])
        ),
    ):
        v = migrate_data.verify_integrity()

    assert v["status"] == "success", f"Integrity check failed: {v}"
    assert all(tv["status"] == "verified" for tv in v["tables"].values())


def test_verify_integrity_reports_mismatch_on_stale_dest(tmp_path, clean_dest_dbs):
    """Verify integrity check detects row count mismatches."""
    src = _make_source(tmp_path)

    # Create dest with no rows for background_tasks (simulate stale/missing data)
    dest = tmp_path / "system.db"
    conn = sqlite3.connect(str(dest))
    conn.execute("CREATE TABLE background_tasks (id INTEGER PRIMARY KEY, k TEXT)")
    conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, k TEXT)")
    conn.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY, k TEXT)")
    conn.commit()
    conn.close()

    with (
        patch.object(migrate_data, "MONOLITHIC_DB", src),
        patch.object(migrate_data, "BACKUP_DIR", tmp_path / "backup"),
        patch.object(
            migrate_data,
            "_get_db_path",
            side_effect=lambda k: Path(tmp_path / f"{k}.db" if k != "system" else dest),
        ),
    ):
        v = migrate_data.verify_integrity()

    assert v["tables"]["background_tasks"]["status"] == "mismatch"
    assert any(tv["status"] == "mismatch" for tv in v["tables"].values())
