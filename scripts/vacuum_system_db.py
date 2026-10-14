"""
Utility script to prune bloated background_tasks records and VACUUM system.db.
Reduces system.db from ~800MB to ~1-2MB.
"""

import json
import os
import sqlite3
import sys

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "system.db"
)


def main():
    if not os.path.exists(DB_PATH):
        print(f"Error: {DB_PATH} not found.")
        sys.exit(1)

    initial_size = os.path.getsize(DB_PATH)
    print(f"Initial system.db size: {initial_size / (1024 * 1024):.2f} MB")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 1. Count current tasks
    cur.execute("SELECT count(*) FROM background_tasks")
    total_tasks = cur.fetchone()[0]
    print(f"Total background tasks: {total_tasks}")

    # 2. Prune old tasks, keeping latest 30
    cur.execute(
        """
        DELETE FROM background_tasks
        WHERE task_id NOT IN (
            SELECT task_id FROM background_tasks
            ORDER BY created_at DESC
            LIMIT 30
        )
    """
    )
    pruned_count = cur.rowcount
    print(f"Pruned {pruned_count} older tasks (kept latest 30).")

    # 3. Strip massive chart_data from remaining tasks if present
    cur.execute(
        "SELECT task_id, result_json FROM background_tasks WHERE result_json IS NOT NULL"
    )
    rows = cur.fetchall()
    stripped_count = 0
    for task_id, rj in rows:
        try:
            data = json.loads(rj)
            if isinstance(data, dict) and "chart_data" in data:
                del data["chart_data"]
                cur.execute(
                    "UPDATE background_tasks SET result_json = ? WHERE task_id = ?",
                    (json.dumps(data), task_id),
                )
                stripped_count += 1
        except Exception as e:
            print(f"  skip {task_id}: {e}")

    print(f"Stripped bloated chart_data from {stripped_count} tasks.")
    conn.commit()

    # 4. VACUUM to reclaim disk space
    print("Running VACUUM to reclaim unallocated pages...")
    cur.execute("VACUUM")
    conn.close()

    final_size = os.path.getsize(DB_PATH)
    saved_mb = (initial_size - final_size) / (1024 * 1024)
    print(f"Final system.db size: {final_size / (1024 * 1024):.2f} MB")
    print(f"Successfully reclaimed {saved_mb:.2f} MB!")


if __name__ == "__main__":
    main()
