#!/bin/sh
set -e

# Load .env if present (local testing / rebuild resilience)
if [ -f ".env" ]; then
    set -a; . ".env"; set +a
elif [ -f ".env" ]; then
    set -a; . ".env"; set +a
elif [ -f "/app/.env" ]; then
    set -a; . "/app/.env"; set +a
elif [ -f "/app/data/.env" ]; then
    set -a; . "/app/data/.env"; set +a
elif [ -f "/.env" ]; then
    set -a; . "/.env"; set +a
fi

# Ensure data and logs directories exist and are writable
mkdir -p /app/data /app/logs
chmod 777 /app/data /app/logs 2>/dev/null || true

# Clean up any leftover SQLite WAL/SHM files that could cause corruption
# when switching from WAL mode to DELETE mode
for db_file in candles.db system.db paper_trading.db batch_results.db; do
    rm -f /app/data/${db_file}-shm /app/data/${db_file}-wal 2>/dev/null || true
done

# Optional startup integrity check (opt-in via CHECK_DB_INTEGRITY=true to avoid slow boot on large volume-mounted databases)
if [ "${CHECK_DB_INTEGRITY}" = "true" ]; then
    echo "[Entrypoint] Running database integrity checks..."
    for db_file in candles.db system.db paper_trading.db batch_results.db; do
        db_path="/app/data/${db_file}"
        if [ -f "$db_path" ]; then
            if ! sqlite3 "$db_path" "PRAGMA quick_check;" 2>/dev/null | grep -q "ok"; then
                echo "[Entrypoint] WARNING: ${db_file} quick_check reported issues."
            fi
        fi
    done
fi

echo "==================================================="
echo "  EMA + VWAP Crypto Trading Suite Daemon Started"
echo "  Web UI is accessible at: http://localhost:${PORT:-8080}"
echo "                           http://127.0.0.1:${PORT:-8080}"
echo "  (Note: 0.0.0.0 is a bind target, not a browser URL)"
echo "==================================================="

# Start the application directly (signal handling is done within the Python app)
# Graceful shutdown: catch SIGTERM/SIGINT and stop the app cleanly
cleanup() {
    echo "[entrypoint] Received shutdown signal, stopping..."
    if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
        kill -TERM "$PID" 2>/dev/null || true
        wait "$PID" 2>/dev/null || true
    fi
    exit 0
}
trap 'cleanup' 15 2

exec "$@"
