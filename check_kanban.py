import sqlite3

path = r"C:/Users/Josh/AppData/Local/hermes/kanban.db"
conn = sqlite3.connect(path)
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
print(cursor.fetchall())
conn.close()
