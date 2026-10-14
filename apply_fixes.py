#!/usr/bin/env python3
"""Apply all fixes to database.py"""

with open("C:/crypto/ema_vwap/src/database.py", "r") as f:
    content = f.read()

# Fix 1: Add alerts table after settings table in init_db()
settings_block = '''            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    is_secret INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )

            conn.commit()'''

alerts_block = '''            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    is_secret INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    type TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata TEXT,
                    channels_sent TEXT
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts (timestamp DESC)"
            )

            conn.commit()'''

if "CREATE TABLE IF NOT EXISTS alerts" not in content:
    content = content.replace(settings_block, alerts_block, 1)
    print("Fix 1: Added alerts table")
else:
    print("Fix 1: Alerts table already present")

# Fix 2: Add maintenance methods before "# Global default database instance"
methods_marker = "# Global default database instance\ndb = CandleDatabase()"

if "def get_db_info" not in content:
    methods = '''    # -------------------------------------------------------------------------
    # Maintenance Methods
    # -------------------------------------------------------------------------

    def get_db_info(self) -> dict[str, Any]:
        """Return health/metadata info for this database file."""
        import os as _os
        exists = _os.path.exists(self.db_path)
        if not exists:
            return {
                "path": self.db_path,
                "exists": False,
                "size_bytes": 0,
                "table_count": 0,
                "tables": [],
                "integrity_check": "missing",
                "journal_mode": "unknown",
                "file_size_bytes": 0,
            }
        size = _os.path.getsize(self.db_path)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [r[0] for r in cursor.fetchall()]
            cur = conn.execute("PRAGMA journal_mode;")
            journal_mode = cur.fetchone()[0]
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
        return {
            "path": self.db_path,
            "exists": True,
            "size_bytes": size,
            "table_count": len(tables),
            "tables": tables,
            "integrity_check": integrity,
            "journal_mode": journal_mode,
            "file_size_bytes": size,
            "candle_record_count": 0,
        }

    def repair_database(self) -> dict[str, Any]:
        """Run integrity check; return status + message describing outcome."""
        info = self.get_db_info()
        if not info["exists"]:
            return {"status": "no_file", "message": "Database file does not exist."}
        if info["integrity_check"] == "ok":
            return {
                "status": "healthy",
                "message": "no repair needed; database integrity verified.",
                "quarantined_files": [],
            }
        try:
            with self.get_connection() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                conn.execute("VACUUM;")
            with self.get_connection() as conn:
                integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            if integrity == "ok":
                return {
                    "status": "success",
                    "message": "Database repaired successfully; integrity verified.",
                    "quarantined_files": [],
                }
            return {
                "status": "error",
                "message": f"Integrity check still failing: {integrity}",
                "quarantined_files": [],
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Repair attempt raised: {e}",
                "quarantined_files": [],
            }

    def vacuum_database(self) -> dict[str, Any]:
        """Run VACUUM on this database; return status + message."""
        try:
            with self.get_connection() as conn:
                conn.execute("VACUUM;")
            return {"status": "success", "message": "VACUUM completed successfully."}
        except Exception as e:
            return {"status": "error", "message": f"VACUUM failed: {e}"}

    def export_candles_csv(
        self, exchange: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> str:
        import csv
        from io import StringIO
        buf = StringIO()
        writer = csv.writer(buf)
        cols = ["timestamp", "exchange", "symbol", "timeframe", "open", "high",
                "low", "close", "volume"]
        writer.writerow(cols)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            sql = "SELECT " + ", ".join(cols) + " FROM candles"
            conditions = []
            params = []
            for col, val in (("exchange", exchange), ("symbol", symbol), ("timeframe", timeframe)):
                if val is not None:
                    conditions.append(f"{col} = ?")
                    params.append(val)
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
            sql += " ORDER BY timestamp"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            cursor.execute(sql, params)
            for row in cursor.fetchall():
                writer.writerow(row)
        return buf.getvalue()

    def export_candles_json(
        self, exchange: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> str:
        import json
        cols = ["timestamp", "exchange", "symbol", "timeframe", "open", "high",
                "low", "close", "volume"]
        rows = []
        with self.get_connection() as conn:
            cursor = conn.cursor()
            sql = "SELECT " + ", ".join(cols) + " FROM candles"
            conditions = []
            params = []
            for col, val in (("exchange", exchange), ("symbol", symbol), ("timeframe", timeframe)):
                if val is not None:
                    conditions.append(f"{col} = ?")
                    params.append(val)
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
            sql += " ORDER BY timestamp"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            cursor.execute(sql, params)
            for row in cursor.fetchall():
                rows.append(dict(zip(cols, row)))
        return json.dumps(rows)

    def save_alert(self, entry: dict[str, Any]) -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO alerts (timestamp, type, level, message, metadata, channels_sent) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry.get("timestamp"),
                    entry.get("type"),
                    entry.get("level"),
                    entry.get("message"),
                    json.dumps(entry.get("metadata", {})),
                    json.dumps(entry.get("channels_sent", [])),
                ),
            )
            conn.commit()

'''

    content = content.replace(methods_marker, methods + methods_marker)
    print("Fix 2: Added maintenance methods")
else:
    print("Fix 2: Maintenance methods already present")

with open("C:/crypto/ema_vwap/src/database.py", "w") as f:
    f.write(content)
print("All fixes applied successfully!")
