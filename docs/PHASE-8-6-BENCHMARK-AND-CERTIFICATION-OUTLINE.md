# Phase 8.6 Outline: Benchmark and Certify Process Optuna Backend

> **Bead:** `ema_vwap-8ig.6`  
> **Parent Epic:** `ema_vwap-8ig` (Process-based Optuna optimization architecture)  
> **Status:** In Progress (Planning & Design)  
> **Date:** October 2026  

---

## 1. Executive Summary & Goals

The multi-process Optuna backend implemented across `8ig.1` through `8ig.5` provides:
1. A picklable objective evaluator and spec contract (`8ig.2`).
2. Journal storage locking compatible with Windows non-admin privileges (`JournalFileOpenLock`) and an isolated coordinator (`8ig.3`).
3. Seamless integration with the API `TaskManager` background lifecycle with bounded child process termination and isolated cancellation (`8ig.4`).
4. Sequential combination iteration with inner trial multiprocessing in `BatchOptimizer` (`8ig.5`).

The objective of `ema_vwap-8ig.6` is to certify this implementation across four pillars:
1. **Benchmark Performance:** Quantify timing distributions and speedup across worker counts (1, 2, 4) versus sequential execution across small (300 bars), medium (1,000 bars), and larger (3,000 bars) candle datasets.
2. **Correctness Parity:** Verify that multi-process results produce valid trades, metrics within defined ranges, valid Pareto frontiers in multi-objective mode, and parameters matching strategy constraints.
3. **Resilience & Storage Hygiene:** Verify worker crash resilience, timeout expiration, cancellation bounds, and 100% cleanup of temporary storage journal/lock files.
4. **Certification Policy:** Formulate an empirical decision on default execution mode (certifying that sequential remains the safe default while multiprocess is certified for opt-in high-trial workloads).

---

## 2. Benchmark Design & Methodology

### Workload Matrix
We evaluate combinations across 3 dimensions:
- **Bar counts:** 300 bars, 1,000 bars, 3,000 bars.
- **Trial budgets:** 10 trials (light), 30 trials (medium).
- **Execution modes:**
  - `sequential` (baseline in-process)
  - `multiprocess` with $W=1$ worker (overhead measurement)
  - `multiprocess` with $W=2$ workers
  - `multiprocess` with $W=4$ workers

### Measured Metrics
- **Wall-clock latency ($T_{\text{wall}}$):** Total seconds from `optimize()` start to finish.
- **Startup & Coordination Overhead:** $\Delta T = T_{\text{multiprocess}, W=1} - T_{\text{sequential}}$.
- **Speedup Ratio:** $S_W = T_{\text{sequential}} / T_{\text{multiprocess}, W}$.
- **Storage Cleanup Invariant:** Ensure 0 orphaned temporary files in the journal directory.

### Windows Process Spawn Observations
Under Windows `spawn` semantics:
- Each worker process requires Python interpreter startup, module imports, and unpickling of data structures.
- On Windows, this incurs an initial fixed cost of approximately $150 \text{--} 350\text{ ms}$ per worker.
- Consequently, for small trial counts ($N < 15$), sequential execution may exhibit lower wall-clock latency than multiprocess execution.
- For workloads with $N \ge 30$ trials or large bar counts, parallel backtest execution amortizes this spawn cost.
- **Conclusion:** Sequential execution MUST remain the default, with multiprocess mode available via explicit opt-in (`execution_mode="multiprocess"`).

---

## 3. Resilience & Hygiene Gates

1. **Worker Crash Resilience:** If a worker process exits abruptly or raises an exception during trial execution, `OptunaStudyCoordinator.run_workers` catches the failure, terminates all sibling processes, releases locks, and raises an informative error without hanging the main process.
2. **Timeout Enforcement:** If `timeout_seconds` is reached, remaining futures are cancelled and worker processes are terminated within bounded time.
3. **Cooperative Cancellation:** Verification that `is_cancelled` triggers immediate worker process termination and unlinks all temporary journal and lock files.
4. **Temporary File Leak Prevention:** `OptunaStudyCoordinator.cleanup()` explicitly handles Windows retry logic (`max_attempts=5`) for delayed file release by OS handles.

---

## 4. Self-Critique & Edge Cases (5 In-Depth Reviews)

1. **Edge Case 1: Windows file handle locking preventing immediate journal deletion.**
   - *Risk:* Windows antivirus or file system delays may hold open file handles briefly, causing `os.remove()` to fail with `PermissionError`.
   - *Mitigation:* `OptunaStudyCoordinator.cleanup()` contains exponential backoff and retry up to 5 attempts, ensuring files are cleaned up without failing the parent workflow.
2. **Edge Case 2: Flaky benchmark results from CPU throttling or background OS tasks.**
   - *Risk:* Running single-shot benchmarks on a shared desktop can produce noise.
   - *Mitigation:* The benchmark test suite computes average timings and avoids strict wall-clock assertions that could fail on slow CI environments; rather, it verifies deterministic monotonic properties and logs timing statistics.
3. **Edge Case 3: Pickling large candle frames repeatedly across workers.**
   - *Risk:* Transferring 100k candles could cause high IPC overhead.
   - *Mitigation:* `OptunaObjectiveEvaluator` and `OptunaTrialSpec` precompute indicator frames once in the parent process, transmitting the ready-to-evaluate frame once per worker invocation rather than per trial.
4. **Edge Case 4: Multi-objective Utopia point selection in multi-process studies.**
   - *Risk:* Pareto frontier trials collected from multi-process workers having disparate or non-finite values.
   - *Mitigation:* The sanitization and validation pipeline verified in `8ig.2` prunes non-finite and invalid trials before Utopia point distance calculation.
5. **Edge Case 5: Sampler seed divergence.**
   - *Risk:* Per-worker seed allocation causing duplicate or correlated sampling sequences.
   - *Mitigation:* `OptunaStudyCoordinator.allocate_worker_seeds` allocates explicit distinct seeds derived from `master_seed + i * 10007`, ensuring workers explore independent regions of parameter space.

---

## 5. Certification Artifacts & Implementation Plan

1. **Certification Test Suite:** `tests/test_optuna_benchmark_certification_8ig6.py`
   - Parity and correctness verification across 300 and 1,000 bars.
   - Pareto front sanity and multi-objective metric consistency.
   - Worker crash, timeout, and cancellation resilience.
   - Guaranteed temporary file cleanup verification.
2. **Benchmark Execution Script:** `scripts/benchmark_optuna_process.py`
   - Executes systematic runs comparing Sequential vs Multiprocess (W=1, 2, 4) on 300, 1000, and 3000 bars.
   - Prints benchmark scorecard.
3. **Certification Report:** Record findings and confirm `execution_mode="sequential"` default policy.
