@echo off
REM Get API key from PostgreSQL system database
docker exec ema_vwap_timescaledb psql -U postgres -d ema_vwap_system -t -c "SELECT value FROM settings WHERE key = 'app_api_key';"
