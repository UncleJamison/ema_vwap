# System-Wide DB Routing Audit

**Context:** During the monolithic→multi-DB refactor, `CandleDatabase()` (no args) defaults to  
`data/candles.db`. Any module instantiated before being updated to use `get_database("domain")`  
silently writes to the wrong file. The API layer reads from the correct, domain-specific file,  
creating a write-here/read-there split.

---

## Live Contamination Evidence

```
candles.db:
  alerts:           8 rows   ← should be in system.db
  background_tasks: 67 rows  ← should be in system.db
  batch_results:    2 rows   ← should be in batch_results.db (fixed)
  settings:         2 rows   ← should be in system.db

paper_trading.db:
  settings:         2 rows   ← should be in system.db

system.db (correct):
  alerts:           232 rows
  background_tasks: 119 rows
  settings:         50 rows
```

Every `CandleDatabase()` call without an explicit path is a misfire.

---

## Bug Inventory

### Bug 1 — `batch_optimizer.py` ✅ FIXED
| | |
|---|---|
| **File** | [`src/batch_optimizer.py:22`](file:///c:/crypto/ema_vwap/src/batch_optimizer.py#L22) |
| **Severity** | 🔴 Critical — batch sweep results silently discarded from UI |
| **Impact** | Leaderboard always empty after sweep; "Load Saved Results" never works |
| **Status** | **Fixed** — now uses `get_database("batch_results")` |

---

### Bug 2 — `tasks.py` global singleton
| | |
|---|---|
| **File** | [`src/tasks.py:186`](file:///c:/crypto/ema_vwap/src/tasks.py#L186) |
| **Severity** | 🟡 Medium — functional but split-brain |
| **Code** | `global_task_manager = TaskManager()` → defaults to `candles.db` |
| **Impact** | 67 background task records written to `candles.db`. The API reads from `system.db` (via `_system_db`). However, `app.py` passes `_system_db` explicitly when calling `TaskManager` in backtest/optimize workers — so those tasks **are** correctly written to `system.db`. The global singleton is used for the `/api/tasks` polling endpoints, which **read from `candles.db`** instead of `system.db`, so the task progress UI shows a **different** 67-task history than what the system DB has. |
| **Fix** | `global_task_manager = TaskManager(get_database("system"))` |

---

### Bug 3 — `settings.py` fallback default  
| | |
|---|---|
| **File** | [`src/settings.py:44`](file:///c:/crypto/ema_vwap/src/settings.py#L44) |
| **Severity** | 🟡 Medium — stale settings risk |
| **Code** | `self.db = db or CandleDatabase()` |
| **Impact** | `app.py` correctly passes `_system_db` when constructing `_settings_mgr = SettingsManager(_system_db)`. **But** paper trading code constructs its own `SettingsManager()` with no args at some point (evidenced by `paper_trading.db` having 2 settings rows: `paper_runner_enabled` and `paper_polling_interval` written 2026-09-18). This means paper runner auto-start on restart reads from `system.db` (correct), but a code path is writing its on/off state to `paper_trading.db` instead — the two can **diverge** and cause the paper runner to fail to restart correctly after a server reboot. |
| **Fix** | Remove the `CandleDatabase()` fallback — require callers to always pass a `db`. Add `get_database("system")` in `tasks.py` and any other instantiation site. |

---

### Bug 4 — `app.py` export route stray instantiation
| | |
|---|---|
| **File** | [`src/app.py:1653-1656`](file:///c:/crypto/ema_vwap/src/app.py#L1653-L1656) |
| **Severity** | 🔴 Critical — Export to Portfolio silently finds nothing |
| **Code** | `_test_batch_db = CandleDatabase()` inside `export_batch_to_portfolio()` |
| **Comment in code** | `# Use default database (candles.db) which is what tests use` ← **stale comment from before refactor** |
| **Impact** | `POST /api/batch_results/export_to_portfolio` always loads from `candles.db.batch_results` (0 useful rows post-fix) → immediately raises HTTP 404 "No matching batch results found". **Export to Portfolio is currently broken for all users.** |
| **Fix** | Replace with `_batch_db.load_batch_results(limit=1000)` — `_batch_db` is already the correct module-level handle |

---

### Bug 5 — `data_loader.py` (benign)
| | |
|---|---|
| **File** | [`src/data_loader.py:18`](file:///c:/crypto/ema_vwap/src/data_loader.py#L18) |
| **Severity** | ✅ Benign |
| **Code** | `db = CandleDatabase()` |
| **Assessment** | `DataLoader` only reads/writes the `candles` table, which genuinely lives in `candles.db`. No action needed. |

---

### Bug 6 — `database.py` global instance (benign)
| | |
|---|---|
| **File** | [`src/database.py:2211`](file:///c:/crypto/ema_vwap/src/database.py#L2211) |
| **Severity** | ✅ Benign — legacy backward-compat export |
| **Code** | `db = CandleDatabase()` |
| **Assessment** | This is an old module-level export. Nothing in the active server imports this `db`. Safe to leave or eventually remove. |

---

## Root Cause: Missing Domain Guards in `init_db`

All four specialized databases are initialized with the **same full schema** (all 13 tables in every `.db` file). The domain gate only prevents 3 tables:

```
batch_results table  → only created in "batch_results" or "system" domains  ✅
paper_ledger         → only created in "paper_trading" domain                ✅
paper_profiles       → only created in "paper_trading" domain                ✅
```

But `background_tasks`, `settings`, `alerts`, `candles`, `paper_positions`, etc. are created **in every DB with no domain gate**. This means any stray `CandleDatabase()` call can find the table it needs and write to it silently.

---

## Prioritized Fix Plan

| Priority | File | Change |
|---|---|---|
| 1 🔴 | [`app.py:1655`](file:///c:/crypto/ema_vwap/src/app.py#L1655) | Replace `CandleDatabase()` with `_batch_db.load_batch_results(...)` |
| 2 🟡 | [`tasks.py:186`](file:///c:/crypto/ema_vwap/src/tasks.py#L186) | `global_task_manager = TaskManager(get_database("system"))` |
| 3 🟡 | [`settings.py:44`](file:///c:/crypto/ema_vwap/src/settings.py#L44) | Remove `CandleDatabase()` fallback; audit all `SettingsManager()` call sites |
| 4 🔵 | [`database.py init_db`](file:///c:/crypto/ema_vwap/src/database.py) | Add domain guards for `background_tasks`, `settings`, `alerts` tables to prevent future silent misrouting |

> [!IMPORTANT]
> Paper trading and the paper runner are **not data-loss risks right now** — `_paper_db` is correctly wired in `app.py`. The `paper_trading.db` settings contamination is a stale-write risk (wrong value winning on restart), not an active read-path bug. Fix priority 2 & 3 before the next server restart cycle.

> [!WARNING]
> **Export to Portfolio (Bug 4) is fully broken today.** Any attempt returns 404. Fix priority 1 immediately.
