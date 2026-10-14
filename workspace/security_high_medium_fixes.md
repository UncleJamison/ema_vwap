# Security High/Medium Fixes — ema_vwap audit (post-462.1)
Source files: src/app.py, src/database.py, src/settings.py
Review file: workspace/security_review.md

---

## 1. HIGH — Exception exposure (str(e)) in app.py (36 lines verified)
Verified all 36 occurrences present, unchanged from review.
Lines: 268, 370, 469, 574, 589, 603, 616, 770, 913, 992, 1088, 1413, 1586, 1644, 1678, 1721, 1798, 1838, 1877, 1911, 1940, 1978, 2019, 2044, 2067, 2087, 2110, 2129, 2149, 2180, 2207, 2235, 2284, 2308, 2336, 2396.

Protected endpoints (have `Depends(get_api_key)`):
- 606 (`/api/db/repair`) — line 616 str(e) is protected but still leaks to client if auth passes
- 619 (`/api/db/export/csv`) — line 638 FIXED (generic "Internal error...")
- 641 (`/api/db/export/json`) — line 660 FIXED (generic)

Open / sensitive endpoints (no auth):
- 268 (`/api/tear-sheet/pdf`), 370 (html), 469 (json) — tear sheet, open
- 574 (`/api/sync_history`), 589 (`clear_cache` message with str(e)) — open
- 603 (`/api/db/info`) — open; 616 (`/api/db/repair`) — protected but leaks
- 770 (`/api/backtest`) — open
- 913 (`/api/optimize`) — open; 992 (wfo) — open; 1088 (validate) — open
- 1413 (auto-validate) — open; 1586 (export_to_portfolio) — open
- 1644 (deploy profile), 1678 (list profiles), 1721 (get profile) — open
- 1798 (`/api/paper/engine/step`), 1838 (reconcile), 1877 (positions list) — open
- 1911 (close position), 1940 (sl_tp — also leaks ValueError str(ve) at 1937), 1978 (ledger txns) — open
- 2019, 2044, 2067, 2087, 2110, 2129, 2149, 2180, 2207, 2235, 2284, 2308, 2336, 2396 — portfolio/paper endpoints, all open

Fix for all 36: replace `str(e)` / `str(ve)` in detail and message with generic "Internal server error" (keep `logger.error` with `e` for server-side only).
Note: export endpoints 619/641 already fixed; apply same pattern to rest.

---

## 2. HIGH — database.py SQL f-string patterns (verified)
- Line 207: `cursor.execute(f"PRAGMA journal_mode = {mode};")` — mode derived from env/default (WAL/DELETE), not direct user input, but f-string remains. Fix: `cursor.execute("PRAGMA journal_mode = ?;", (mode,))` (or static since mode is controlled).
- Line 843: `cursor.execute(f"DELETE FROM batch_results WHERE id IN ({placeholders})", result_ids)` — placeholders programmatic (`",".join("?" ...)`), not user data; direct injection blocked. Fix: keep parameterized but eliminate f-string: build query string with `?` via `"?"` joining (already done), just avoid f-string wrapper: e.g., `query = "DELETE FROM batch_results WHERE id IN (" + placeholders + ")"` then `cursor.execute(query, result_ids)`.
- Prior fixes at ~1816/1861 (repair / integrity) remain parameter-bound (no string concat) — verified intact.

---

## 3. HIGH — database.py repair exception message (verified)
Line 1873 (inside `repair_database` except): `"message": f"repaired: {e}"` — leaks exception to API response. Fix:
```python
"message": "Repair completed with issues",
# optionally log exception server-side
logger.error(f"[DB repair] {e}")
```
Also line 1878 (`vacuum_database`): `return {"status":"error","message":str(e)}` — fix with generic message + server-side log.

---

## 4. MEDIUM — database.py exception exposure (verified)
- Line 50: `err_msg = str(e).lower()` inside retry decorator — leaks to retry logic only (internal), but could propagate. Fix: keep internal for retry logic; do not include `err_msg` in returned messages.
- Line 1878: `message: str(e)` confirmed above — same fix.

---

## 5. MEDIUM — settings masked-bypass (verified)
Source `src/settings.py` lines 107-112 (inside `update_bulk`):
```python
if val_str.startswith("••••"):
    continue
```
- Skips masked dummy inputs but does not validate value type/length; malicious client could submit non-placeholder masked values to overwrite secrets.
Fix: add validation — if `secret_flag` and not masked placeholder and not empty, enforce length limits / allowed chars; never allow direct overwrite of secret keys without secondary confirmation / raw-value check.
Also: masked dummy bypass only checks prefix; a value like `••••••••fake` passes as masked but could be used to overwrite. Add stricter check: `val_str.startswith("••••") and len(val_str) >= 8` or require explicit `raw=true` for secret updates.

---

## 6. Verification — 462 fixes status
- SQL injection fixes at ~1816 / ~1861 intact (parameterized queries, no concatenation in repair/integrity paths).
- Auth on 3 export/repair endpoints (`/api/db/export/csv`, `/json`, `/api/db/repair`) intact (`dependencies=[Depends(get_api_key)]`).
- 36 `str(e)` lines remain unfixed in app.py (only 638/660 fixed); need bulk replacement.
- `API_KEY` fallback at app.py:132 removed in current file (`os.environ.get("API_KEY")` only, no default) — fix intact.
- `database.py` repair message still leaks (`f"repaired: {e}"`) — NOT fixed by 462.

---

## Exact fixes to apply (ordered)
1. `app.py`: replace all 36 `str(e)` / `str(ve)` exception details/messages with "Internal server error"; keep `logger.error(...)`.
2. `database.py:207`: parameterize PRAGMA (`cursor.execute("PRAGMA journal_mode = ?;", (mode,))`).
3. `database.py:843`: eliminate f-string in DELETE (use concatenated query with `?` placeholders + params).
4. `database.py:1873`: replace `f"repaired: {e}"` with generic message; add server-side log.
5. `database.py:1883`: replace `str(e)` in vacuum with generic message.
6. `settings.py:107-112`: strengthen masked bypass (length/check + restrict secret overwrites); require raw-value confirmation for secret keys.
