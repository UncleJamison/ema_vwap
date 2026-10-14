with open("src/database.py", "r") as f:
    content = f.read()
content = content.replace(
    'where.append("exchange = ?"); params.append(exchange)',
    'where.append("exchange = ?")\n                params.append(exchange)',
)
content = content.replace(
    'where.append("symbol = ?"); params.append(symbol)',
    'where.append("symbol = ?")\n                params.append(symbol)',
)
content = content.replace(
    'where.append("timeframe = ?"); params.append(timeframe)',
    'where.append("timeframe = ?")\n                params.append(timeframe)',
)
with open("src/database.py", "w") as f:
    f.write(content)
print("Fixed E702 semicolon errors")
