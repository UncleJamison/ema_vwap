# Phase 8.3 Outline: Shared Optuna Study Storage and Worker Coordination

## 1. Overview & Problem Statement
When running Optuna across multiple Python processes (especially under Windows `spawn`), workers cannot share Optuna's default `InMemoryStorage` across separate process memory spaces. 
Using SQLite (`sqlite:///...`) under multi-process concurrency causes severe lock contention (`sqlite3.OperationalError: database is locked`) because SQLite locks the entire database file during writes.

Optuna provides `optuna.storages.JournalStorage` paired with `optuna.storages.journal.JournalFileBackend`. This backend appends trial events sequentially and uses OS-level advisory file locks.
This phase implements the shared study storage manager and worker coordinator for multi-process optimization.

---

## 2. Storage Topology & Filesystem Requirements
1. **Backend:** `optuna.storages.JournalStorage(JournalFileBackend(file_path))`
2. **Local Filesystem Requirement:**
   > **Note on Filesystems:** `JournalFileBackend` uses OS file locks (`fcntl.flock` on Unix, `msvcrt.locking` on Windows). It assumes and requires a **local filesystem** (e.g., local SSD/HDD). Networked file systems (NFS, SMB, CIFS) do not provide reliable atomic advisory lock semantics and must not be used.
3. **Study Isolation:**
   - Each optimization run generates a unique study identity:
     `study_name = f"optuna_study_{task_id}_{uuid.uuid4().hex[:8]}"`
   - Each study operates on a dedicated temporary journal file:
     `journal_path = tempfile.NamedTemporaryFile(prefix=f"{study_name}_", suffix=".journal", delete=False).name`
   - This ensures absolute isolation: no two optimize requests share the same journal file or study namespace, eliminating cross-request data corruption or trial leakage.

---

## 3. Worker Coordination & Lifecycle
The coordinator (`OptunaStudyCoordinator`) orchestrates the end-to-end lifecycle:

```
[Master Process]
   │
   ├─► Create unique JournalStorage file & Study
   ├─► Seed Trial #0 (Baseline params) if enabled
   ├─► Allocate trial budgets & deterministic worker seeds
   │
   ├─► Spawn N Worker Processes (via ProcessPoolExecutor / multiprocessing.Process)
   │     │
   │     ├─► Worker 1: attach study -> run K_1 trials -> complete
   │     ├─► Worker 2: attach study -> run K_2 trials -> complete
   │     └─► Worker N: attach study -> run K_N trials -> complete
   │
   ├─► Wait with bounded timeout / monitor cancellation
   ├─► Ensure all workers join / terminate (bounded shutdown)
   │
   ├─► Load completed study from JournalStorage & extract results/Pareto frontier
   └─► Close storage handles & unlink temporary journal file
```

### 3.1 Bounded Worker Shutdown Guarantee
Cleanup of temporary journal storage must **strictly occur only after all worker processes have completely terminated**.
On Windows, unlinking an open file (`os.remove()`) fails with `PermissionError: [WinError 32] The process cannot access the file because it is being used by another process`.
Therefore:
1. Master process waits for worker futures/processes with bounded timeout (`pool.shutdown(wait=True, cancel_futures=True)`).
2. Explicitly release/close Optuna storage references in the master process.
3. Retry unlinking journal file with a small backoff if Windows file locks take a few milliseconds to be released by the OS.
4. Clean up in a `finally:` block so that even upon exceptions or user cancellation, temporary files do not leak on disk.

---

## 4. Stale Trials and Worker Fault Handling
If a worker process terminates abnormally (e.g. segmentation fault or killed by OS OOM):
- Optuna records trials as `RUNNING` until completion.
- The coordinator inspects `study.trials` upon collection:
  - Any trials remaining in `TrialState.RUNNING` are treated as incomplete/crashed and filtered out of Pareto analysis.
  - Only `TrialState.COMPLETE` trials with `valid=True` are considered for the best parameter candidate.
- Exact trial accounting: the coordinator tracks `completed_trials = sum(1 for t in study.trials if t.state == TrialState.COMPLETE)`.

---

## 5. Verification Plan
1. **Storage Isolation Test:** Verify that two concurrent coordinator studies with identical trial parameters run in complete isolation with zero cross-contamination.
2. **Windows Spawn & Coordination Test:** Run multi-process optimization using `ProcessPoolExecutor` with `mp.get_context("spawn")` and `JournalStorage`.
3. **Exact Accounting Test:** Validate that with $N$ requested trials and $W$ workers, exactly $N$ total trials are evaluated and recorded in the journal.
4. **Clean File Lifecycle Test:** Verify that the journal file is created, accessed by workers, and reliably removed upon completion or on worker error.
