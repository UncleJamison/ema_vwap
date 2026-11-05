@echo off
REM ============================================================
REM EMA + VWAP Trading Suite - Docker Restart Script
REM Handles SQLite cleanup, PostgreSQL/TimescaleDB restart
REM ============================================================

setlocal enabledelayedexpansion

echo ============================================================
echo   EMA + VWAP Trading Suite - Restart
echo ============================================================

REM 1. Stop and remove existing containers
echo [1/5] Stopping containers...
docker rm -f ema_vwap_trading_daemon 2>nul || echo (no trading daemon container)
docker compose down 2>nul || echo (no compose services)

REM 2. Clean old SQLite database files (legacy migration artifacts)
echo [2/5] Cleaning legacy SQLite database files...
if exist "C:\crypto\ema_vwap\data\*.db" (
    del /f /q "C:\crypto\ema_vwap\data\*.db" 2>nul
    del /f /q "C:\crypto\ema_vwap\data\*-shm" 2>nul
    del /f /q "C:\crypto\ema_vwap\data\*-wal" 2>nul
    echo    Removed old SQLite databases (*.db, *-shm, *-wal)
) else (
    echo    No legacy SQLite files found
)

REM 3. Optional: Reset TimescaleDB volume (uncomment for fresh DB)
REM echo [3/5] Resetting TimescaleDB volume...
REM docker volume rm ema_vwap_timescaledb_data 2>nul || echo (volume not found)

REM 4. Clear Python bytecode cache
echo [3/5] Clearing Python bytecode cache...
if exist "C:\crypto\ema_vwap\src\__pycache__" rmdir /s /q "C:\crypto\ema_vwap\src\__pycache__" 2>nul
if exist "C:\crypto\ema_vwap\src\*.pyc" del /f /q "C:\crypto\ema_vwap\src\*.pyc" 2>nul

REM 5. Rebuild and start
echo [4/5] Building and starting containers...
docker compose build --no-cache
if errorlevel 1 (
    echo BUILD FAILED
    exit /b 1
)

docker compose up --force-recreate -d
if errorlevel 1 (
    echo START FAILED
    exit /b 1
)

echo [5/5] Waiting for services to be healthy...
timeout /t 5 /nobreak >nul

echo.
echo ============================================================
echo   Restart Complete!
echo   Web UI: http://localhost:8080
echo   TimescaleDB: localhost:5432 (postgres/postgres)
echo   Logs: docker compose logs -f ema-vwap-daemon
echo ============================================================
