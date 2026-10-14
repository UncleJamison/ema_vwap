#!/usr/bin/env python
"""Apply all database.py fixes: alerts schema, alert methods, repair_database, conn.close."""
import ast

DB_PATH = "src/database.py"

with open(DB_PATH, "r") as f:
    content = f.read()

lines = content.splitlines(True)

# 1. Fix conn.close() after init_db (may already exist)
for i, line in enumerate(lines):
    if (
        "            conn.commit()" in line
        and i + 1 < len(lines)
        and "    @retry_on_db_lock()" in lines[i + 1]
    ):
        if "conn.close()" not in lines[i + 1]:
            indent = len(line) - len(line.lstrip())
            lines.insert(i + 1, " " * indent + "conn.close()\n")
            print("Added conn.close() after init_db commit")
        break

# 2. Add alerts schema + indexes + migration
for i, line in enumerate(lines):
    if "# Background Tasks Schema" in line and i > 0:
        if "CREATE TABLE IF NOT EXISTS alerts" not in "\n".join(lines):
            alerts_block = '''            # Alerts Schema (ema_vwap-6ic)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    level TEXT NOT NULL,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    acknowledged_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_alerts_created_at
                ON alerts (created_at DESC)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_alerts_type
                ON alerts (type)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_alerts_level
                ON alerts (level)
                """
            )

            # Migration: legacy alerts table (rename alert_type -> type, add missing columns)
            alert_cols = {
                row[1]
                for row in cursor.execute("PRAGMA table_info(alerts)").fetchall()
            }
            if "type" not in alert_cols and "alert_type" in alert_cols:
                logger.info("[CandleDatabase] Migrating alerts: adding missing columns")
                cursor.execute("ALTER TABLE alerts RENAME COLUMN alert_type TO type")
                if "source" not in alert_cols:
                    cursor.execute("ALTER TABLE alerts ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown'")
                if "category" not in alert_cols:
                    cursor.execute("ALTER TABLE alerts ADD COLUMN category TEXT NOT NULL DEFAULT 'general'")
                if "acknowledged_at" not in alert_cols:
                    cursor.execute("ALTER TABLE alerts ADD COLUMN acknowledged_at TEXT")
                if "created_at" not in alert_cols:
                    cursor.execute("ALTER TABLE alerts ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
                logger.info("[CandleDatabase] alerts migration complete")

'''
            lines[i:i] = [alerts_block]
            print("Added alerts schema + indexes + migration")
        break

# 3. Add alert methods + get_db_info + repair_database before get_connection
for i, line in enumerate(lines):
    if "    def get_connection(self) -> sqlite3.Connection:" in line:
        if "def save_alert" not in "\n".join(lines):
            methods = '''    def get_db_info(self) -> dict[str, Any]:
        """Return basic database integrity info."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            result = cur.execute("PRAGMA integrity_check").fetchall()
            integrity = str(result[0][0]) if result else "unknown"
            return {"integrity_check": integrity}
        finally:
            conn.close()

    def save_alert(self, alert: dict[str, Any]) -> str:
        """Persist an alert to the alerts table."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            ts = alert.get("timestamp") or alert.get("created_at") or datetime.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            cur.execute(
                """
                INSERT INTO alerts (timestamp, type, message, level, source, category, metadata, acknowledged, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                (
                    ts,
                    alert.get("type") or "unknown",
                    alert.get("message") or "",
                    alert.get("level") or "INFO",
                    alert.get("source") or "unknown",
                    alert.get("category") or "general",
                    str(alert.get("metadata", {})),
                    1 if alert.get("acknowledged") else 0,
                    ts,
                ),
            )
            conn.commit()
            return str(cur.lastrowid)
        finally:
            conn.close()

    def get_alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT id, timestamp, type, message, level, source, category, metadata, acknowledged FROM alerts ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {
                    "id": row[0],
                    "timestamp": row[1],
                    "type": row[2],
                    "message": row[3],
                    "level": row[4],
                    "source": row[5],
                    "category": row[6],
                    "metadata": row[7],
                    "acknowledged": bool(row[8]),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def acknowledge_alert(self, alert_id: str) -> None:
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE alerts SET acknowledged = 1, acknowledged_at = datetime('now') WHERE id = ?",
                (alert_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_alert(self, alert_id: str) -> None:
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
            conn.commit()
        finally:
            conn.close()

    def repair_database(self, db_path: str | None = None) -> dict[str, Any]:
        """Repair a corrupted database by quarantining it and re-initializing."""
        path = db_path or self.db_path
        if not os.path.exists(path):
            return {
                "status": "no_file",
                "message": "no repair needed",
                "quarantined_files": [],
            }
        try:
            with open(path, "rb") as f:
                header = f.read(16)
            if header != b"SQLite format 3\\x00":
                logger.warning(
                    f"[Database] File at {path} is not a valid SQLite database (bad header). Quarantining."
                )
                quarantined = self._recover_corrupted_db(
                    sqlite3.DatabaseError("Not a valid SQLite database file")
                )
                self.init_db()
                return {
                    "status": "repaired",
                    "message": "repaired: corrupt database quarantined and re-initialized",
                    "quarantined_files": quarantined,
                }
        except Exception:
            pass
        conn = None
        try:
            conn = sqlite3.connect(path, timeout=30.0)
            cur = conn.cursor()
            rows = cur.execute("PRAGMA integrity_check").fetchall()
            conn.close()
            is_ok = len(rows) == 1 and str(rows[0][0]).strip().lower() == "ok"
            if is_ok:
                return {
                    "status": "healthy",
                    "message": "no repair needed",
                    "quarantined_files": [],
                }
            logger.warning(
                f"[Database] Integrity check failed on {path}: {rows}. Quarantining and rebuilding."
            )
            quarantined = self._recover_corrupted_db(
                sqlite3.DatabaseError(f"Integrity check failed: {rows}")
            )
            self.init_db()
            return {
                "status": "repaired",
                "message": "repaired: corrupt database quarantined and re-initialized",
                "quarantined_files": quarantined,
            }
        except (sqlite3.DatabaseError, sqlite3.OperationalError) as db_err:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            conn = None
            logger.error(f"[Database] repair_database failed: {db_err}")
            try:
                if os.path.exists(path):
                    quarantined = self._recover_corrupted_db(db_err)
                    self.init_db()
                    return {
                        "status": "repaired",
                        "message": "repaired: corrupt database quarantined and re-initialized",
                        "quarantined_files": quarantined,
                    }
            except Exception as e:
                logger.error(f"[Database] repair_database failed: {e}")
                return {
                    "status": "error",
                    "message": "repair failed",
                    "quarantined_files": [],
                }

'''
            lines[i:i] = [methods]
            print("Added alert methods + get_db_info + repair_database")
        break

# 4. Fix _recover_corrupted_db return type
for i, line in enumerate(lines):
    if "def _recover_corrupted_db(self, exc: Exception) -> None:" in line:
        lines[i] = "    def _recover_corrupted_db(self, exc: Exception) -> list[str]:\n"
        # Add time.sleep after ts = int(time.time())
        for j in range(i, min(i + 30, len(lines))):
            if "ts = int(time.time())" in lines[j]:
                lines.insert(
                    j + 1,
                    "        # Brief pause to release any file handles before rename (Windows can be slow)\n",
                )
                lines.insert(j + 2, "        time.sleep(0.5)\n")
                break
        break

with open(DB_PATH, "w") as f:
    f.writelines(lines)

print("Done applying fixes")

# Verify
ast.parse(open(DB_PATH).read())  # noqa: SIM115 - one-shot verify script
print("File parses OK")
