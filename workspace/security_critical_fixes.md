# Critical Security Fixes — ema_vwap (`src/app.py`)

Audit date: post-462.1 verification. Source: `workspace/security_review.md`. Verified against current `src/app.py` (197KB, 2550 lines).

---

## 1. Hardcoded auth bypass (CRITICAL)
- **File:Line**: `src/app.py:132`
- **Status VERIFIED**: `API_KEY = os.environ.get("API_KEY")` — fallback `"default-key-for-dev"` from review **NOT present in current file** (already removed or never re-added). If environment is unset, `API_KEY` is `None`; auth fails-closed for the 3 protected endpoints (`/api/db/repair`, `/api/db/export/csv`, `/api/db/export/json` at lines 606/619/641).
- **Fix needed**: enforce mandatory env (`os.environ["API_KEY"]` with no default); add startup guard; create `src/auth/` directory (missing per review §6).

---

## 2. 11 sensitive endpoints — ALL OPEN (no `Depends(get_api_key)`)
Verified with `grep -n` + sed at listed lines; 0 matches for `Depends(get_api_key)` on any of the 11.

| Endpoint | Line | Auth? |
|---|---|---|
| `/api/db/info` | 593 | **OPEN** |
| `/api/optimize` | 824 | **OPEN** |
| `/api/batch_optimize` | 1249 | **OPEN** |
| `/api/paper/profiles/deploy` | 1603 | **OPEN** |
| `/api/paper/engine/step` | 1781 | **OPEN** |
| `/api/paper/engine/start` | 1805 | **OPEN** |
| `/api/paper/engine/stop` | 1816 | **OPEN** |
| `/api/settings` POST | 1136 | **OPEN** |
| `/api/portfolio/*` (snapshot/venues/positions/history/correlation/var/size/route/rebalance-plan) | 2052,2070,2090,2132,2161,2183,2210,2263,2287 | **ALL OPEN** |
| `/api/portfolio/rebalance/execute` | 2311 | **OPEN** |
| `/api/portfolio/refresh` | 2113 | **OPEN** |

Only **3** endpoints protected (lines 606, 619, 641). Prior fix scope confirmed — no new auth added since.
- **Fix per endpoint**: add `dependencies=[Depends(get_api_key)]` to `@app.get/post(...)` decorator, or wrap handler with `Depends`. Example: `@app.get("/api/db/info", dependencies=[Depends(get_api_key)])` at 593.

---

## 3. Exception exposure — 36 `str(e)` leaks CONFIRMED
- **Count**: exactly **36** (`grep -o -n "str(e)" src/app.py | wc -l` = 36)
- **Lines** (sorted): 268, 370, 469, 574, 589, 603, 616, 770, 913, 992, 1088, 1413, 1586, 1644, 1678, 1721, 1798, 1838, 1877, 1911, 1940, 1978, 2019, 2044, 2067, 2087, 2110, 2129, 2149, 2180, 2207, 2235, 2284, 2308, 2336, 2396
- Plus `str(e)` embedded in message strings at ~589, 1939 (review note).
- **Fix**: replace all `detail=str(e)` / `"..." + str(e)` with generic `"Internal server error"`; log full exception server-side only (`logging.error` with `exc_info=True`).

---

## 4. Additional noted (review + verification)
- **Missing auth module**: `src/auth/` directory absent. Only inline `get_api_key` (line 135) exists.
- **SQL injection f-strings** (review refs `database.py:207`, `database.py:843`) — not in `app.py`; out of this audit scope but should be tracked.
- **Settings masked dummy bypass** (`settings.py:107-112`) — not corrected.
- **Repair endpoint leak** (`database.py` repair except block, `str(e)` embedded in response message) — verified unpatched.

---

## Recommended fix order (file:line)
1. `app.py:132` — enforce mandatory `API_KEY`; no fallback.
2. `app.py:593,824,1249,1603,1781,1805,1816,1136,2052,2113,2311` — add `dependencies=[Depends(get_api_key)]`.
3. `app.py:2052-2360` (+ other portfolio lines) — same auth addition for the full `/api/portfolio/*` group.
4. `app.py` lines listed above (36 lines) — sanitize all `str(e)` to generic message + server-side logging.
5. Create `src/auth/dependencies.py` and migrate `get_api_key` out of `app.py`.

No files modified; audit-only report written.
