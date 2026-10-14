# Security Review — ema_vwap (post-462.1)
Files reviewed: src/app.py, src/database.py, src/validator.py, src/settings.py, auth/

## Executive
- Auth directory (`src/auth/`) is MISSING entirely.
- Only 3 endpoints (`/api/db/repair`, `/api/db/export/csv`, `/api/db/export/json`) enforce `get_api_key`; all sensitive paper/optimizer/portfolio/db/settings endpoints are open.
- Hardcoded fallback API_KEY (`"default-key-for-dev"`) allows authentication bypass.
- Exception exposure (`str(e)`) leaks internal errors to API clients on 36 lines in app.py, plus database repair.
- SQL injection: line 207 (`PRAGMA journal_mode`) uses f-string; line 843 (`DELETE ...`) uses f-string placeholders (low user impact, bad practice). Prior fixes at 1816/1861 remain intact but repair endpoint still leaks exception.
- No hardcoded secrets in settings (uses DB + env), but secret masking relies on substring heuristics and masked dummy bypass (`val_str.startswith("••••")`) is weak.

---

## CRITICAL

1. **Hardcoded default API key / auth bypass**
   - `app.py:132`: `API_KEY = os.environ.get("API_KEY", "default-key-for-dev")`
   - With no `API_KEY` env set, anyone using the default header bypasses auth on the 3 protected endpoints.

2. **Missing authorization on sensitive endpoints** (`auth/` directory missing; `Depends(get_api_key)` absent)
   - `app.py:593` `/api/db/info` (no auth)
   - `app.py:824` `/api/optimize` (no auth)
   - `app.py:1249` `/api/batch_optimize` (no auth)
   - `app.py:1603` `/api/paper/profiles/deploy` (no auth)
   - `app.py:1781` `/api/paper/engine/step` (no auth)
   - `app.py:1805` `/api/paper/engine/start` (no auth)
   - `app.py:1816` `/api/paper/engine/stop` (no auth)
   - `app.py:1136` `/api/settings` POST (no auth) — allows changing settings/credentials
   - `app.py:2052` `/api/portfolio/*` (no auth)
   - `app.py:2311` `/api/portfolio/rebalance/execute` (no auth)
   - `app.py:2113` `/api/portfolio/refresh` (no auth)

3. **Exception exposure to clients**
   - `app.py` has 36 occurrences of `detail=str(e)` (lines: 268, 370, 469, 574, 603, 616, 770, 913, 992, 1088, 1413, 1586, 1644, 1678, 1721, 1798, 1838, 1877, 1911, 1940, 1978, 2019, 2044, 2067, 2087, 2110, 2129, 2149, 2180, 2207, 2235, 2284, 2308, 2336, 2396) plus `str(e)` in messages (589, 1939, etc.). Internal DB/file errors are returned to clients.

---

## HIGH

4. **Exception exposure in database repair**
   - `database.py:~1861` (inside `repair_database` except block): `"message": f"repaired: {e}"` leaks exception message to API response. Fix from 462.1 did not fully sanitize; message still embeds `e`.

5. **SQL injection (f-string query construction)**
   - `database.py:207`: `cursor.execute(f"PRAGMA journal_mode = {mode};")` — `mode` derived from `else: mode = "WAL"`, not direct user input, but f-string pattern remains risky.
   - `database.py:843`: `cursor.execute(f"DELETE FROM batch_results WHERE id IN ({placeholders})", result_ids)` — placeholders are programmatically generated (`"?"`), not user data, so direct injection is blocked; still a bad practice.

6. **Missing auth directory (`src/auth/`)**
   - Entire authorization layer (e.g. `auth/dependencies.py`, middleware, token validators) is absent. Only inline `get_api_key` exists in `app.py`.

---

## MEDIUM

7. **Exception exposure in database info/connection**
   - `database.py:50`: `err_msg = str(e).lower()` leaks exception message into error response strings.
   - `database.py:1878`: `return {"status": "error", "message": str(e)}` leaks exception message.

8. **Input validation gaps / weak request validation**
   - `validator.py`: no explicit input sanitization on `StrategyParams`/`WFOConfig` before passing to database/backtest; assumes caller validates. No SQL usage in validator, but no input bounds checks visible for numeric thresholds (`min_backtest_return_pct`, `min_backtest_sharpe`, etc.) that could be manipulated.
   - `app.py` endpoints (e.g., `/api/optimize`, `/api/batch_optimize`, `/api/paper/profiles/deploy`) accept JSON payloads without explicit authorization checks; payload injection risk depends on downstream validation, not enforced at API layer.

9. **Masked secret bypass in settings bulk update**
   - `settings.py:107-112`: `update_bulk` skips masked dummy inputs (`val_str.startswith("••••")`) but does not validate value type/length; a malicious client could submit non-placeholder masked values to overwrite secrets.

---

## LOW

10. **No hardcoded secrets/URLs in settings**
    - `settings.py` uses DB + environment variables for all credentials; webhook URLs (`ALERT_DISCORD_WEBHOOK_URL`, etc.) are config keys, not embedded URLs. Secret masking uses substring heuristics (`SECRET_KEYWORDS`) which can miss edge cases but no direct exposure.

11. **Token/credential exposure in code**
    - No API tokens, private keys, or webhook URLs are hardcoded in `app.py`, `database.py`, or `validator.py`. `API_KEY` default (`line 132`) is the only embedded secret-related value.

---

## Verification / Previous Fixes Status
- Prior SQL injection fixes (lines 1816/1861 in `database.py`) remain intact (queries use parameter binding); repair endpoint no longer uses string concatenation for queries, but exception message still leaks (`database.py` repair block).
- Auth layer added to export endpoints (`/api/db/export/csv`, `/api/db/export/json`) and `/api/db/repair` remains protected; no new auth added for paper/optimizer/portfolio endpoints since previous work.
- Exception exposure fix at line 1017/629/651 (previous work) not present in current file; those lines have moved/changed (current `str(e)` lines listed above remain unfixed).

---

## Recommendations (concise)
- Remove `default-key-for-dev` fallback (`app.py:132`) or make auth mandatory.
- Add `Depends(get_api_key)` to all sensitive endpoints (`/api/db/*`, `/api/paper/*`, `/api/optimizer/*`, `/api/portfolio/*`, `/api/settings`).
- Replace all `str(e)` with generic error messages (`"Internal server error"`) in `app.py` and `database.py`.
- Replace f-string query in `database.py:207` with parameterized/static query.
- Create `src/auth/` module (dependencies, middleware) rather than inline `get_api_key`.
- Add input validation and authorization middleware layers.

Report generated: workspace/security_review.md
