# Phase 5 Architecture Outline — Persistent Live Trading Engine & Containerized Deployment

**Epic**: `ema_vwap-xh2` — *Persistent Live Trading Engine with Auto-Resume, Downtime Gap Reconciliation & Containerized Deployment*

## Architecture Overview

```mermaid
flowchart TD
    subgraph Host / Container
        A[Docker / OS Daemon Process] -->|Start / Restart| B[FastAPI Lifespan Boot]
        B --> C[Load SQLite DB & Settings]
        C --> D{paper_runner_enabled?}
        D -->|Yes| E[Downtime Position & Candle Reconciliation]
        E --> F[Evaluate Missed SL/TP Breaches]
        F --> G[Record Fills with Historical Timestamps]
        G --> H[Start Live Polling Runner Thread]
        D -->|No| I[Standby / Await API Activation]
        H --> J[Periodic Engine Step Loop (15s)]
        J --> K[Mark-to-Market & SL/TP Management]
        B -.->|Shutdown Signal| L[FastAPI Lifespan Teardown]
        L --> M[Stop Polling & Flush State]
    end
```

## Subtasks & Breakdown

### Subtask 1: Persistent Engine Runner State & FastAPI Lifespan Auto-Resume (`ema_vwap-xh2.1`)
- **State Persistence**:
  - Add `paper_runner_enabled` (boolean) and `paper_polling_interval` (int, default 15) to `system_settings`.
  - Update `POST /api/paper/engine/start` and `POST /api/paper/engine/stop` to persist state upon user trigger.
- **FastAPI Lifespan Lifecycle**:
  - Add `@asynccontextmanager async def lifespan(app: FastAPI)` in `src/app.py`.
  - On startup: Check `paper_runner_enabled`. If `True`, invoke `_paper_engine.start_polling()`.
  - On shutdown: Safely call `_paper_engine.stop_polling()`.

### Subtask 2: Downtime Historical Gap Reconciliation & SL/TP Backfill (`ema_vwap-xh2.2`)
- **Gap Detection & Re-Evaluation**:
  - In `PaperTradingEngine.reconcile_positions_on_startup()`:
    - Iterate over active open positions from `PaperLedger`.
    - Compare `pos.updated_time` / `pos.entry_time` with `datetime.now(timezone.utc)`.
    - Fetch missed candles spanning the downtime interval.
    - Evaluate if high/low breach `stop_loss` or `take_profit`.
    - Fill order at breach candle with accurate price and timestamp.
    - Close position and log `PaperTradeRecord`.

### Subtask 3: Production Dockerfile, Compose Configuration & Daemon Supervision (`ema_vwap-xh2.3`)
- **Multi-Stage Dockerfile**:
  - Lightweight Python 3.13-slim image.
  - Install dependencies, copy source and web static assets.
  - Non-root container user for security.
- **docker-compose.yml**:
  - Persistent volume mount for SQLite database directory (`./data:/app/data` or WAL database).
  - Environment variables configuration (API keys, ports).
  - Container healthcheck (`GET /api/paper/engine/status`).
  - Auto-restart policy (`restart: unless-stopped`).
