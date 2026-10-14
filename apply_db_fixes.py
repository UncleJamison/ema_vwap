import ast

with open("src/database.py", "r") as f:
    lines = f.readlines()

# 1. Close connection after init_db's with block
for i, line in enumerate(lines):
    if (
        "            conn.commit()" in line
        and i + 2 < len(lines)
        and "    @retry_on_db_lock()" in lines[i + 2]
    ):
        indent = len(line) - len(line.lstrip())
        lines[i + 1] = " " * indent + "conn.close()\n"
        print("Added conn.close() after init_db commit")
        break

# 2. Add alerts table + indexes + legacy migration
# Find the line with '# Background Tasks Schema'
for i, line in enumerate(lines):
    if "# Background Tasks Schema" in line and i > 0:
        indent = len(line) - len(line.lstrip())
        alerts_block = [
            "            # Alerts Schema (ema_vwap-6ic)\n",
            "            cursor.execute(\n",
            '                """\n',
            "                CREATE TABLE IF NOT EXISTS alerts (\n",
            "                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n",
            "                    timestamp TEXT NOT NULL,\n",
            "                    type TEXT NOT NULL,\n",
            "                    message TEXT NOT NULL,\n",
            "                    level TEXT NOT NULL,\n",
            "                    source TEXT NOT NULL,\n",
            "                    category TEXT NOT NULL,\n",
            "                    metadata TEXT NOT NULL DEFAULT '{}',\n",
            "                    acknowledged INTEGER NOT NULL DEFAULT 0,\n",
            "                    acknowledged_at TEXT,\n",
            "                    created_at TEXT NOT NULL\n",
            "                )\n",
            '                """\n',
            "            )\n",
            "            cursor.execute(\n",
            '                """\n',
            "                CREATE INDEX IF NOT EXISTS idx_alerts_created_at\n",
            "                ON alerts (created_at DESC)\n",
            '                """\n',
            "            )\n",
            "            cursor.execute(\n",
            '                """\n',
            "                CREATE INDEX IF NOT EXISTS idx_alerts_type\n",
            "                ON alerts (type)\n",
            '                """\n',
            "            )\n",
            "            cursor.execute(\n",
            '                """\n',
            "                CREATE INDEX IF NOT EXISTS idx_alerts_level\n",
            "                ON alerts (level)\n",
            '                """\n',
            "            )\n",
            "\n",
            "            # Migration: legacy alerts table (rename alert_type -> type, add missing columns)\n",
            "            alert_cols = {\n",
            "                row[1]\n",
            '                for row in cursor.execute("PRAGMA table_info(alerts)").fetchall()\n',
            "            }\n",
            '            if "type" not in alert_cols and "alert_type" in alert_cols:\n',
            '                logger.info("[CandleDatabase] Migrating alerts: adding missing columns")\n',
            '                cursor.execute("ALTER TABLE alerts RENAME COLUMN alert_type TO type")\n',
            '                if "source" not in alert_cols:\n',
            "                    cursor.execute(\"ALTER TABLE alerts ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown'\")\n",
            '                if "category" not in alert_cols:\n',
            "                    cursor.execute(\"ALTER TABLE alerts ADD COLUMN category TEXT NOT NULL DEFAULT 'general'\")\n",
            '                if "acknowledged_at" not in alert_cols:\n',
            '                    cursor.execute("ALTER TABLE alerts ADD COLUMN acknowledged_at TEXT")\n',
            '                if "created_at" not in alert_cols:\n',
            "                    cursor.execute(\"ALTER TABLE alerts ADD COLUMN created_at TEXT NOT NULL DEFAULT ''\")\n",
            '                logger.info("[CandleDatabase] alerts migration complete")\n',
            "\n",
        ]
        for j, block_line in enumerate(alerts_block):
            lines.insert(i + j, block_line)
        print("Added alerts table + indexes + legacy migration")
        break

# 3. Add alert methods and get_db_info before get_connection
for i, line in enumerate(lines):
    if "    def get_connection(self) -> sqlite3.Connection:" in line:
        methods = """    def get_db_info(self) -> dict[str, Any]:
        \"\"\"Return basic database integrity info.\"\"\"
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            result = cur.execute("PRAGMA integrity_check").fetchall()
            integrity = str(result[0][0]) if result else "unknown"
            return {"integrity_check": integrity}
        finally:
            conn.close()

    def save_alert(self, alert: dict[str, Any]) -> str:
        \"\"\"Persist an alert to the alerts table.\"\"\"
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            ts = alert.get("timestamp") or alert.get("created_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            cur.execute(
                \"\"\"INSERT INTO alerts (timestamp, type, message, level, source, category, metadata, acknowledged, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)\"\"\",
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

"""
        # Insert before get_connection
        for j, method_line in enumerate(methods.split("\n")):
            lines.insert(i + j, method_line + "\n")
        print("Added alert methods and get_db_info")
        break

# 4. Add repair_database method before get_db_info
for i, line in enumerate(lines):
    if "    def get_db_info(self) -> dict[str, Any]:" in line:
        repair = """    def repair_database(self, db_path: str | None = None) -> dict[str, Any]:
        \"\"\"Repair a corrupted database by quarantining it and re-initializing.\"\"\"
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
        except Exception as e:
            logger.error(f"[Database] repair_database failed: {e}")
            return {
                "status": "error",
                "message": "repair failed",
                "quarantined_files": [],
            }

"""
        # Insert before get_db_info
        for j, repair_line in enumerate(repair.split("\n")):
            lines.insert(i + j, repair_line + "\n")
        print("Added repair_database method")
        break

# 5. Fix _recover_corrupted_db return type and add sleep
for i, line in enumerate(lines):
    if "def _recover_corrupted_db(self, exc: Exception) -> None:" in line:
        lines[i] = "    def _recover_corrupted_db(self, exc: Exception) -> list[str]:\n"
        # Add time.sleep(0.5) after ts = int(time.time())
        for k in range(i, min(i + 20, len(lines))):
            if "ts = int(time.time())" in lines[k]:
                lines.insert(
                    k + 1,
                    "        # Brief pause to release any file handles before rename (Windows can be slow)\n",
                )
                lines.insert(k + 2, "        time.sleep(0.5)\n")
                break
        # Add quarantined list and return
        for k in range(i, min(i + 25, len(lines))):
            if "quarantined" not in "".join(lines[i : k + 1]):
                pass
        # Find the pass statement at the end and replace with quarantined return
        for k in range(i, min(i + 30, len(lines))):
            if lines[k].strip() == "pass":
                lines[k] = "        quarantined: list[str] = []\n"
                break
        # Also fix the logger.warning line to include quarantined.append(dest)
        for k in range(i, min(i + 30, len(lines))):
            if "logger.warning" in lines[k] and "Quarantined" in lines[k]:
                # Find the next lines to add quarantined.append(dest)
                for m in range(k + 1, min(k + 10, len(lines))):
                    if lines[m].strip() == "":
                        lines.insert(m, "        quarantined.append(dest)\n")
                        break
                break
        print("Fixed _recover_corrupted_db return type and added sleep")
        break

with open("src/database.py", "w") as f:
    f.writelines(lines)

try:
    ast.parse(open("src/database.py").read())  # noqa: SIM115 - one-shot verify script
    print("File parses OK")
except SyntaxError as e:
    print(f"Syntax error at line {e.lineno}")
    with open("src/database.py", "r") as f:
        lines = f.readlines()
    for j in range(max(0, e.lineno - 5), min(e.lineno + 5, len(lines))):
        print(f"{j+1}: {lines[j].rstrip()}")
