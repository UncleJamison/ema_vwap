@echo off
echo Removing any stray container named ema_vwap_trading_daemon...
docker rm -f ema_vwap_trading_daemon 2>nul || echo (none to remove)
docker compose down 2>nul || echo (none to down)

echo Clearing Python bytecode cache (host workspace)...
rm -rf "C:\crypto\ema_vwap\src\__pycache__" 2>nul || rm -rf ./src/__pycache__ 2>nul || echo (already clean)

docker compose build --no-cache && docker compose up --force-recreate -d