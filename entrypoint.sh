#!/bin/sh
set -e

# Load .env if present (local testing / rebuild resilience)
if [ -f ".env" ]; then
    set -a; . ".env"; set +a
elif [ -f ".env" ]; then
    set -a; . ".env"; set +a
elif [ -f "/app/.env" ]; then
    set -a; . "/app/.env"; set +a
elif [ -f "/.env" ]; then
    set -a; . "/.env"; set +a
fi

# Ensure logs directory exists and is writable
mkdir -p /app/logs
chmod 777 /app/logs 2>/dev/null || true

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
