# Task Cards — Security / Optimization / Traceability Audit

Convert each bullet into a `bd create` command with appropriate assignee.

## Security (Critical/High)

### Card 1: Add authentication layer to `/api/*` routes
- **Title:** Add auth layer to `/api/*` routes (no auth on API endpoints)
- **Assignee:** security-engineer
- **Body:** Add authentication/authorization to all FastAPI routes in `src/app.py`. No auth checks exist on `/api/monte_carlo`, `/api/tear-sheet/pdf`, `/api/batch_results`, `/api/db/*`, `/ws/dashboard`, etc. Any network-accessible instance exposes full trading APIs. Add `Depends(get_current_user)` to all `/api/*` routes. Gate DB-maintenance endpoints to admin role only.
- **Reference:** `src/app.py:112+` (endpoint definitions, no auth decorators)

### Card 2: Replace bare exception catches in API endpoints
- **Title:** Replace bare `Exception` catches with specific types in API endpoints
- **Assignee:** security-engineer
- **Body:** In `src/app.py`, replace `except Exception as e: raise HTTPException(500, detail=str(e))` at line 1001 (and similar patterns) with specific exception types. Return generic message not raw exception str(). Prevents stack trace exposure.
- **Reference:** `src/app.py:1001`

### Card 3: Gate WebSocket alerts to auth + rate-limit
- **Title:** Add auth and rate-limit to WebSocket get_alerts/acknowledge_alert
- **Assignee:** websocket-specialist
- **Body:** `get_alerts` and `acknowledge_alert` WS handlers at `src/app.py:2422-2510` are accessible without auth and without rate-limit. Add authentication check and rate-limit per client IP.
- **Reference:** `src/app.py:2422-2430, 2503, 2508`

### Card 4: Validate payload params on DB-export endpoints
- **Title:** Validate limit/payload params on DB-export endpoints
- **Assignee:** security-engineer
- **Body:** Add input validation for `limit`, `symbol`, `exchange` in `/api/db/export_csv`, `/api/db/export_json`, `/api/batch_results` endpoints. Enforce max limits (e.g., `limit <= 10,000`) to prevent memory exhaustion/DoS.
- **Reference:** `src/app.py:603, 625`

### Card 5: Document/verify CORS origin restriction
- **Title:** Document and verify CORS origin restriction
- **Assignee:** security-engineer
- **Body:** `CORSMiddleware` added at `src/app.py:~118` — verify it reads from `CORS_ORIGIN` env var and defaults to restrictive. Add documentation comment.
- **Reference:** `src/app.py:118`

## Optimization (High/Medium)

### Card 6: Split monolithic app.py into routes package
- **Title:** Split monolithic `src/app.py` (2522 lines, 94KB) into routes package
- **Assignee:** dev-architect
- **Body:** Split into `src/routes/` package: `backtest.py`, `optimize.py`, `paper.py`, `db_maintenance.py`, `tear_sheet.py`, `websockets.py`, `validator.py` (or move to services layer). Reduces single-file complexity, enables parallel development.
- **Reference:** `src/app.py` 2522 lines

### Card 7: Decompose validate() into separate gate methods
- **Title:** Decompose `validate()` into `run_gate1/2/3()` methods
- **Assignee:** dev-architect
- **Body:** `src/validator.py:125-540` — `validate()` is 415 lines running 3 gates. Extract `run_gate1()`, `run_gate2()`, `run_gate3()` methods. Support `only_gate` parameter for testing/debugging.
- **Reference:** `src/validator.py:125-540`

### Card 8: Add BatchResultService layer
- **Title:** Add `BatchResultService` layer to eliminate repeated DB calls
- **Assignee:** dev-optimizer
- **Body:** Create `src/services/batch_result.py` with `BatchResultService` class wrapping `_batch_db.*` methods. Update `src/app.py` routes (555, 603, 625, 1271, 1282, 1318, 1363, 1395, 1410, 1442) to use service.
- **Reference:** `src/app.py:555,603,625,1271,1282,1318,1363,1395,1410,1442`

## Traceability (UI/DB)

### Card 9: Implement missing `get_alerts()` / `acknowledge_alert()` in CandleDatabase
- **Title:** Add missing `get_alerts()` / `acknowledge_alert()` methods to CandleDatabase
- **Assignee:** db-specialist
- **Body:** `src/app.py:2423`, `2503`, `2510` call `_batch_db.get_alerts()` and `_batch_db.acknowledge_alert()` — these methods **do not exist** in `CandleDatabase`. Implement them matching the signatures. Verified: `db.__dict__` shows only `save_alert` exists.
- **Reference:** `tests/test_alerting.py` expects `get_alerts()`, `acknowledge_alert()`; check `src/alerting.py:211` for usage patterns

### Card 10: Fix export/clear_cache to use correct DB domain
- **Title:** Fix DB-export and clear_cache to use correct database domain
- **Assignee:** db-specialist
- **Body:** `_batch_db.clear_cache()` and `export_candles_csv/json` calls target `batch_results.db`, but that DB has no `candles` table. Create `_candles_db` via `get_database("candles")` or adjust routing in `db_connection_manager.py`.
- **Reference:** `src/app.py:603,625,1271`; verify `db_connection_manager.py` routing

### Card 11: Create missing UI pages
- **Title:** Create missing UI pages: dashboard.html, maintenance.html, profile.html, forms.html, styles.css
- **Assignee:** frontend-specialist
- **Body:** UI audit found `index.html` references buttons/pages that don't exist: `dashboard.html`, `maintenance.html`, `profile.html`, `forms.html`, `styles.css`. Either create these files under `src/web/` or consolidate their content into `index.html` tabs.
- **Reference:** UI audit: `ls /c/crypto/ema_vwap/src/web/` shows only `index.html`, `style.css`, `tear_sheet_template.html`, `app.js`, `tear_sheet.js`

### Card 12: Cache get_alerts() result in WebSocket handler
- **Title:** Cache alerts result in WebSocket get_alerts handler
- **Assignee:** websocket-specialist
- **Body:** Each WS message calls `_batch_db.get_alerts()` — add caching in `window._validationReportCache` or server-side (in-memory with TTL). Update every 30s, not per message.
- **Reference:** `app.js:window._validationReportCache` exists; `src/app.py:2422-2423`

---

## Usage

```bash
# Create all cards
bd create --title "Add auth layer to /api/* routes" --type=security --assignee security-engineer --parent <previous-card-id>
# ... repeat for each card ...

# Or batch create via script after verifying all
```