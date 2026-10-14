# Deployment & Continuous Daemon Execution Guide

This guide details how to deploy the **EMA + VWAP Trading Suite** as a resilient, unattended background daemon that automatically recovers across server restarts, reboots, and crashes.

---

## 1. Quick Start with Docker Compose (Recommended)

Docker Compose provides the most robust multi-environment deployment with automatic SQLite WAL persistence, container health checking, and auto-restart policies.

### Prerequisites
- Docker Engine 20.10+ & Docker Compose v2.0+

### Launching the Daemon
```bash
# 1. Build and start the container in detached daemon mode
docker compose up -d --build

# 2. View live logs
docker compose logs -f

# 3. Check container health status
docker ps
```

### Stopping or Restarting
```bash
# Gracefully stop the daemon
docker compose down

# Restart the daemon
docker compose restart
```

### Persistence
The SQLite database (`./data/candles.db`, WAL, and SHM files) is mounted directly to `./data` on the host machine. All of your:
- Active deployed strategy profiles
- Open paper trading positions
- Cash balances and double-entry transaction ledgers
- Optuna multi-coin parameter sweep results
- System settings and credentials

**will automatically survive container rebuilds, host reboots, and updates.**

---

## 2. Windows Background Service / Startup (Native Host)

If running directly on Windows without Docker:

### Method A: Windows Startup Batch Script
1. Place a shortcut to [`run.bat`](file:///c:/crypto/ema_vwap/run.bat) inside your Windows Startup folder:
   ```
   Win + R -> shell:startup -> Paste shortcut to run.bat
   ```

### Method B: NSSM (Non-Sucking Service Manager)
To run the server as a true background Windows Service:
```cmd
nssm install EmaVwapService "C:\path\to\python.exe" "-m src.app"
nssm set EmaVwapService AppDirectory "C:\crypto\ema_vwap"
nssm set EmaVwapService Start SERVICE_AUTO_START
nssm start EmaVwapService
```

---

## 3. Auto-Resume & Downtime Reconciliation Architecture

1. **Auto-Resume**: When live polling is activated in the Web UI, `paper_runner_enabled = "true"` is saved in SQLite.
2. **On Boot / Startup**: The FastAPI lifespan manager checks this flag. If `true`, it immediately starts the background execution runner without requiring you to open the browser.
3. **Gap Reconciliation**: Before live polling begins, `PaperTradingEngine` scans all open positions and cross-references historical candles for any Stop-Loss or Take-Profit breaches that occurred during the downtime window, executing backfilled fills with historical timestamps.
