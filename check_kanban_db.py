import sqlite3

path = r"C:/Users/Josh/AppData/Local/hermes/kanban.db"
conn = sqlite3.connect(path)
cursor = conn.cursor()

# Schema of tasks
cursor.execute("PRAGMA table_info(tasks)")
print("Tasks schema:")
for row in cursor.fetchall():
    print(row)

# Fetch all tasks
cursor.execute("SELECT * FROM tasks")
print("\nAll tasks:")
for row in cursor.fetchall():
    print(row)

conn.close()
