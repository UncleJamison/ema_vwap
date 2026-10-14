@echo off
title EMA + VWAP Crypto Trading Suite
echo ===================================================
echo   EMA + VWAP Crypto Trading Suite
echo ===================================================
echo Starting FastAPI server at http://127.0.0.1:8080...
echo Press Ctrl+C to stop the server.
echo.

python -m src.app

pause
