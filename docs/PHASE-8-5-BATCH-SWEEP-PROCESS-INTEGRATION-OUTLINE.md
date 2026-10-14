# Phase 8.5 Outline: Integrate Process Optimization with Batch Sweeps

> **Bead:** `ema_vwap-8ig.5`  
> **Parent Epic:** `ema_vwap-8ig` (Process-based Optuna optimization architecture)  
> **Status:** In Progress (Planning & Design)  
> **Date:** October 2026  

---

## 1. Executive Summary & Goals

`BatchOptimizer` currently iterates symbol and timeframe combinations sequentially, constructing an `OptunaOptimizer` for each combination and executing trials in-process. Under the multi-process Optuna architecture delivered in `8ig.2`, `8ig.3`, and `8ig.4`, individual optimization runs can execute across worker processes via `OptunaStudyCoordinator`.

The goal of `ema_vwap-8ig.5` is to integrate the process-based Optuna backend into batch optimization sweeps while satisfying these core requirements:
1. **Parallelism Topology:** Parallelism is executed across **trials within each combination**, while combinations are iterated sequentially. This strictly avoids nested process pools, prevents memory explosions from multiple large candle datasets in flight, and avoids SQLite database lock contention.
2. **Resource Budgeting:** Enforce `MAX_OPTUNA_WORKERS` bounds across batch configurations and ensure sequential mode remains the default.
3. **Cancellation & Progress Propagation:** Allow cooperative cancellation both between combinations and within active multi-process trial runs, propagating progress to batch state.
4. **Persistence Invariant:** Ensure invalid or failed trials/combinations are never persisted to the batch results table, and valid results maintain 100% schema parity with sequential execution.
5. **Robust Testing:** Provide regression tests for progress reporting, bounded cancellation, worker clamping, error isolation, and schema parity.

---

## 2. Concurrency Topology & Design Decision

### Topology Evaluation

| Model | Description | Pros | Cons | Decision |
|---|---|---|---|---|
| **Model 1: Trial Parallelism per Combo (Sequential Combos)** | Outer loop iterates combinations sequentially; inner Optuna runs use `OptunaStudyCoordinator` with $W$ workers. | Strict memory bound (1 candle frame loaded at a time); zero SQLite database write contention; exact reuse of `8ig.3`/`8ig.4` coordinator; clear progress semantics. | If combo has very few trials, worker pool startup cost per combo. | **ACCEPTED (Standard)** |
| **Model 2: Combo Parallelism (Sequential Trials)** | Outer loop runs $C$ combinations in parallel processes; each combination runs single-thread Optuna. | Faster when many small combos with few trials. | Loads $C$ candle datasets into memory simultaneously; SQLite write lock contention on `db.save_batch_result`; complex cross-combo progress tracking. | **REJECTED** |
| **Model 3: Nested Pools (Parallel Combos + Parallel Trials)** | $C$ combinations running $W$ workers each ($C \times W$ total processes). | Maximizes CPU saturation. | Severe CPU oversubscription, thrashing, Windows handle exhaustion, memory pressure, complex IPC deadlocks. | **STRICTLY FORBIDDEN** |

### Topology Invariant
- **Rule:** Under no circumstance shall nested process pools be instantiated.
- `BatchOptimizer` will iterate combinations sequentially.
- When `cfg.execution_mode == "multiprocess"`, each combination will execute its trials in parallel across up to `n_workers` processes (clamped by `MAX_OPTUNA_WORKERS`).
- When `cfg.execution_mode == "sequential"`, each combination runs in-process as before.

---

## 3. Configuration & Schema Additions

### `BatchOptimizerConfig` (`src/batch_optimizer.py`)
```python
execution_mode: str = "sequential"  # "sequential" | "multiprocess"
n_workers: int = 1                  # clamped to [1, MAX_OPTUNA_WORKERS]
```

### `BatchOptimizeRequest` (`src/app.py`)
```python
execution_mode: str = "sequential"
n_workers: int = 1
```

Validation rules:
- `execution_mode` must be either `"sequential"` or `"multiprocess"`.
- `n_workers` is clamped to `min(max(1, n_workers), MAX_OPTUNA_WORKERS)`.
- If `execution_mode == "sequential"`, `n_workers` is coerced to 1.

---

## 4. Lifecycle, Cancellation, and Progress Tracking

### Cancellation Flow
`BatchOptimizer.run()` supports an optional `is_cancelled: Callable[[], bool] | None`:
1. **Pre-combo check:** Before starting combination $(i+1)$, check `is_cancelled()`. If true, set `_update_state(running=False, error="Batch sweep cancelled")` and return immediately.
2. **In-combo check:** Pass `is_cancelled` into `optimizer.optimize(df, is_cancelled=is_cancelled, progress_callback=combo_cb)`.
3. If `TaskCancelledException` is raised by `run_workers()`, catch it, mark batch state as cancelled, perform cleanup, and exit cleanly without saving results.

### Progress Flow
`_update_state` in `src/batch_optimizer.py` will track:
- `done`: Number of completed combinations.
- `total`: Total combinations in sweep.
- `current_combo`: E.g. `"BTC/USD (5m)"`.
- `combo_progress`: Float $0.0 \dots 1.0$ within current combination.
- `running`: Boolean flag.
- `error`: Error string if failed/cancelled.

---

## 5. Persistence & Invariants

1. **Trade Count Filter:** Combos with fewer than `min_trades` valid trades are skipped and never persisted.
2. **Invalid Trials Filter:** Combos where `best_value_valid is False` or `best_params is None` are skipped and never persisted.
3. **Database Write Safety:** Since combinations execute sequentially on the main batch thread, all `db.save_batch_result()` operations occur sequentially from a single thread, guaranteeing zero SQLite lock collisions.
4. **Schema Parity:** The dictionary written to `db.save_batch_result()` contains the identical fields and data types regardless of `execution_mode`.

---

## 6. Self-Critique & Edge Cases (5 In-Depth Reviews)

1. **Edge Case 1: Cancellation during child process execution within a combination.**
   - *Risk:* Child processes keep running and consuming CPU after batch sweep cancellation is requested.
   - *Mitigation:* `OptunaStudyCoordinator.run_workers` terminates all active child worker processes within 1 second and raises `TaskCancelledException`. `BatchOptimizer` catches this exception, aborts the loop, and does not record partial results.
2. **Edge Case 2: Insufficient candles for one combination in a sweep.**
   - *Risk:* Skipping an invalid combo could leave state inconsistent or crash worker pools.
   - *Mitigation:* The check `df.empty or len(df) < cfg.min_candles` occurs *before* any `OptunaOptimizer` is instantiated, gracefully skipping to the next combination.
3. **Edge Case 3: Worker crash in child process during combination $K$.**
   - *Risk:* An unhandled exception in a worker process crashes the entire batch sweep without recording prior completed combinations.
   - *Mitigation:* Prior completed combinations are already committed to SQLite via `db.save_batch_result()`. An unhandled worker failure raises an exception that updates `_batch_state(running=False, error=...)` and safely releases any temporary study coordinator files.
4. **Edge Case 4: High memory footprint across 20+ combinations.**
   - *Risk:* Memory leaks from Optuna studies or precomputed indicator DataFrames over long sweeps.
   - *Mitigation:* Sequential combo execution ensures each `OptunaOptimizer` and `OptunaStudyCoordinator` is garbage collected (and temporary journal files unlinked in `finally:`) at the end of each combo before loading the next dataset.
5. **Edge Case 5: Resource competition between concurrent API optimization and Batch sweep.**
   - *Risk:* An ongoing batch sweep and an API `/api/optimize` request both spawning `MAX_OPTUNA_WORKERS` processes, overloading host CPUs.
   - *Mitigation:* Clamping `n_workers` to `MAX_OPTUNA_WORKERS` (capped at 4) ensures even overlapping operations do not exhaust Windows process limits.

---

## 7. Test Plan (`tests/test_optuna_batch_process_8ig5.py`)

- `test_batch_config_execution_mode_and_clamping`: Validates defaults and clamping for `execution_mode` and `n_workers`.
- `test_batch_optimizer_multiprocess_execution_parity`: Verifies that a batch sweep over multiple symbols/timeframes in `multiprocess` mode produces valid results matching sequential schema.
- `test_batch_optimizer_skips_invalid_combos_multiprocess`: Ensures insufficient candle and trade-count filters work correctly in multi-process mode without persisting invalid records.
- `test_batch_optimizer_cancellation_bounded`: Confirms cooperative cancellation cleanly stops execution between and within combinations within 5 seconds.
- `test_batch_optimizer_progress_tracking`: Verifies per-combination and intra-combination progress callback updates.
