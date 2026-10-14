---
name: enqueue-failure-propagation
description: "How enqueue failures from Optuna optimization flow from optimizer through the task system to the UI response"
related: crypto-trading-system-dev
---

# Enqueue Failure Propagation (bw7)

When `OptunaConfig.seed_base_params=True`, the optimizer attempts to enqueue the baseline strategy parameters as Trial #0. If `study.enqueue_trial()` fails, the failure must propagate to the API response so the UI can surface it.

## Propagation Path

| Layer | File / Line | What it does |
|-------|-------------|--------------|
| Optimizer | `src/optimizer.py:612-616` | Capture `enqueue_failure = str(e)` in result dict when `enqueue_trial()` raises |
| Logging | `src/optimizer.py:283-285` | Log the error; preserve existing logging |
| Task DB | `src/database.py:1810/1842` | `get_background_task()` reads `result_json` (now includes `enqueue_failure`) |
| API endpoint | `src/app.py` (`_optimize_worker`) | Completes task with full result dict |
| UI poll | `/api/tasks/{task_id}` | Returns `enqueue_failure` in JSON; UI can display it |

## Rule

Never let `except Exception: pass` silently swallow errors when the UI layer needs visibility. Capture the failure key in the result dict, propagate it through the task system, and let the frontend poll `/api/tasks/{task_id}` to read it.
