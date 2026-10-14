# Phase 2 Infrastructure Outline — Background Tasks, Settings Store & Rotating Logging

## Goal Overview
Build foundational backend execution infrastructure (`ema_vwap-38x`):
1. **Background Job Manager (`src/tasks.py`)**: Async task tracking engine with progress polling, SQLite/in-memory task status storage, and task cancellation.
2. **Centralized Settings & Secrets Store (`src/settings.py`)**: `SettingsManager` for API keys, exchange credentials, and risk settings, storing to local SQLite/JSON with secret masking for API consumers.
3. **Structured Logging Infrastructure (`src/logger.py` & `src/app.py`)**: Rotating file logging to `logs/app.log` and `logs/paper.log` with custom log formatting and FastAPI lifecycle integration.

## Architectural Specification

### 1. `src/tasks.py` (`TaskManager`)
- `TaskStatus` Enum: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`.
- `TaskInfo` Dataclass / Model: `task_id`, `name`, `status`, `progress_pct`, `step_description`, `start_time`, `end_time`, `error`, `result`.
- `TaskManager`: Thread-safe manager storing tasks in memory and SQLite `background_tasks` table.
- Helper methods:
  - `create_task(name: str, total_steps: int = 100) -> str`
  - `update_progress(task_id: str, current_step: int, step_description: str)`
  - `complete_task(task_id: str, result: Dict[str, Any])`
  - `fail_task(task_id: str, error: str)`
  - `get_task(task_id: str) -> Optional[Dict[str, Any]]`
  - `list_tasks(limit: int = 50) -> List[Dict[str, Any]]`
  - `cancel_task(task_id: str) -> bool`

### 2. `src/settings.py` (`SettingsManager`)
- Persists system settings and exchange credentials in SQLite `settings` table or `.env` / `data/settings.json`.
- Automatic key masking for secret fields (`api_key`, `api_secret`, `password`, `token`):
  - `get_masked_settings()` replaces secret values with `"••••" + last4`.
  - `update_settings(updates: Dict[str, Any])` stores updated key-value pairs cleanly.

### 3. `src/logger.py` (Structured Logging)
- `setup_logging()` configures rotating file handlers (`RotatingFileHandler` with 10MB max size, 5 backups):
  - `logs/app.log` for general application, API requests, and job execution logs.
  - `logs/paper.log` for paper trading signals and order execution logs.
- Custom formatter including timestamps, log levels, logger names, and thread IDs.

### 4. FastAPI Endpoints (`src/app.py`)
- `GET /api/tasks` — List background tasks.
- `GET /api/tasks/{task_id}` — Poll specific background task progress.
- `POST /api/tasks/{task_id}/cancel` — Cancel background task.
- `GET /api/settings` — Fetch masked settings.
- `POST /api/settings` — Update system settings.
