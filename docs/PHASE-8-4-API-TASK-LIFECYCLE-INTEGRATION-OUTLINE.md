# Phase 8.4 Outline: Integrate Process Workers with API Task Lifecycle

## 1. Overview & Objectives
This phase connects the `OptunaStudyCoordinator` (Phase 8.3) and picklable worker evaluator (Phase 8.2) to the FastAPI `/api/optimize` endpoint and `TaskManager` background task lifecycle.

Key constraints & requirements:
1. **Explicit Execution Mode Selection:** Keep `execution_mode="sequential"` as the default for all requests. `execution_mode="multiprocess"` is explicitly opt-in.
2. **Windows `spawn` Safety:** Child processes must execute only the picklable worker entry point (`run_worker_trials`), never recursively executing `app.py` or starting web servers.
3. **Progress Aggregation:** Multi-process trial completion updates `TaskManager` progress percentage and status description in near-real-time.
4. **Bounded Cancellation:** If a user cancels via `/api/tasks/{task_id}/cancel`, worker processes must be joined or terminated within a bounded grace period (<5s) and temporary journal files cleanly unlinked.
5. **Worker Limits & Concurrency Protection:** Limit `n_workers` to bounded CPU resources ($\le \min(\text{CPU count}, 8)$) and ensure concurrent API requests operate on completely isolated study storage.

---

## 2. Request & Config Extensions
In `src/config.py`:
- Extend `OptunaConfig` with:
  ```python
  execution_mode: Literal["sequential", "multiprocess"] = "sequential"
  n_workers: int = 1
  ```
- Max worker cap: `MAX_OPTUNA_WORKERS = min(os.cpu_count() or 4, 8)`.

In `src/app.py`:
- Extend `OptimizeRequest` with:
  ```python
  execution_mode: str = "sequential"
  n_workers: int = 1
  ```
- Validate `execution_mode in ("sequential", "multiprocess")` and clamp `1 <= n_workers <= MAX_OPTUNA_WORKERS`.

---

## 3. Worker Coordination & Progress Loop
When `execution_mode == "multiprocess"` and `n_workers > 1`:
The background task thread:
1. Instantiates `OptunaOptimizer` with `_live_cfg`.
2. Inside `OptunaOptimizer.optimize()` or `OptunaStudyCoordinator.run_workers()`:
   - Polling loop checks `futures` every 0.5s–1.0s.
   - Queries `study.trials` count to report progress:
     $$\text{progress} = 0.2 + 0.75 \times \frac{\min(\text{completed\_trials}, N)}{N}$$
   - Checks `is_cancelled()` callback:
     - If True, cancels pending futures, shuts down executor with bounded timeout, and raises `TaskCancelledException`.
3. Ensures `finally:` block unlinks temporary journal storage and releases file locks.

---

## 4. Verification Plan
1. **API Endpoint Parameter Validation:** Test that `/api/optimize` accepts `execution_mode` ("sequential", "multiprocess") and `n_workers`, defaulting to `"sequential"`.
2. **Cancellation Graceful Shutdown Test:** Start a multiprocess optimization task, trigger cancellation via `TaskManager.cancel_task()`, verify workers terminate within bounded time (<5s) and task status transitions to `CANCELLED`.
3. **Progress Tracking Test:** Verify task progress increases monotonically as workers record trials to the journal.
4. **Concurrent Requests Isolation:** Launch two concurrent background multiprocess optimization tasks; verify both complete with distinct results and zero cross-contamination.
