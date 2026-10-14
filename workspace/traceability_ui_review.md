# UI Layer Traceability Review — ema_vwap
Audit date: 2026-09-15. Inspect scope: templates/ (src/web/index.html, tear_sheet_template.html), static JS (app.js, ws_dashboard.js, tear_sheet.js), backend endpoint mapping (src/app.py). Focus endpoints: /api/paper/profiles, /api/db/repair, /api/optimizer/* (actual /api/optimize, /api/batch_optimize), /api/strategy/* (/api/strategy/validate).

## 1. Template / Static HTML inventory
- src/web/index.html: single-page dashboard; loads /static/app.js, /static/tear_sheet.js, /static/ws_dashboard.js, /static/style.css (mounted at /static). No separate templates/ directory outside web/.
- src/web/tear_sheet_template.html: tear-sheet HTML template (used by pdf_renderer); no direct API links inside.
- No broken HTML links to missing static assets (style.css, scripts present).

## 2. Endpoint → UI mapping and argument verification

### /api/paper/profiles (GET list) — CORRECT
- Endpoint (app.py 1647): params `exchange: str|None`, `active_only: bool = False`.
- UI (app.js 3573): `fetch("/api/paper/profiles")` — no args; optional params default fine. Returns profiles list; UI uses response.profiles.

### /api/paper/profiles/deploy (POST deploy) — CORRECT, payload matches model
- Endpoint model (app.py 1592): `exchange, symbol, timeframe, strategy_mode, target_metric, params (dict), optuna_score (float=0.0), is_active (bool=True)`.
- UI (app.js 1971): sends exactly those 8 fields via JSON.stringify. No mismatched names. `params` comes from optimized trial params; always an object in deploy flow.

### /api/paper/profiles/{exchange}/{symbol} (GET single) — NOT CALLED FROM UI
- Endpoint exists (1681) with optional `timeframe`, `strategy_mode`. UI only uses list + deploy; no broken link because no reference in app.js.

### /api/db/repair (POST) — AUTH MISMATCH / BROKEN
- Endpoint (app.py 606): `@app.post("/api/db/repair", dependencies=[Depends(get_api_key)])`. Requires `X-API-Key` header matching env API_KEY.
- UI (app.js 403): `fetch("/api/db/repair", {method:"POST"})` — NO `X-API-Key` header sent, and no authorization header at all. Will receive 403 on every repair click.
- Maintenance buttons (repair, vacuum, clear) all reuse repair endpoint; all affected.
- Also: endpoint takes zero body args; UI does not send body — correct on payload, incorrect on auth.

### /api/db/export/csv, /api/db/export/json — NOT DIRECTLY CALLED IN APP.JS (referenced by buttons but not verified in snippet); low risk.

### /api/optimize (POST) — CORRECT
- Endpoint (824): `OptimizeRequest` extends `BaseStrategyRequest`; fields include `target_metric`, `n_trials`, `enable_multi_objective`, `multi_objective_metrics`, `fast_ema_min/max`, `vwap_slope_max_bound`, `seed_base_params`, etc.
- UI payload (app.js 1679): includes all matching fields (`...baseParams` + opt config keys). Uses `multi_objective_metrics: multiMetricList || undefined`; JSON.stringify omits undefined — okay.
- Note: endpoint expects `vwap_slope_max_bound` (not `vwap_slope_max`); payload uses `vwap_slope_max_bound` — match.
- Potential minor: payload sends `strategy_mode: optScope === "auto" ? "auto" : baseParams.strategy_mode`. Endpoint `build_strategy_params` handles `strategy_mode` — fine.

### /api/batch_optimize (POST) — NOT VERIFIED IN UI SNIPPET; no broken reference found (only `batch_optimize` key found, likely used elsewhere).

### /api/optimizer/* — MISSING / NOT IMPLEMENTED
- User task referenced `/api/optimizer/*`. Actual backend has `/api/optimize` and `/api/batch_optimize`; no `/api/optimizer/*` sub-routes exist. UI does not reference `/api/optimizer/*`; no broken links, just absent endpoint family.

### /api/strategy/validate (POST) — CORRECT
- Endpoint model (1040): `exchange, symbol, timeframe, limit, days, params (dict={}), criteria, n_trials, target_metric, enable_multi_objective, multi_objective_metrics`.
- UI (app.js 2131): sends all corresponding fields. `params` is passed through; if caller omits it, property omitted → endpoint uses default `{}`. `criteria` not sent (optional). No mismatch.

### Additional endpoints observed and status
- `/api/backtest` (POST), `/api/monte_carlo` (POST), `/api/walk_forward` (POST): exist; UI references found (`api/backtest`, `api/monte_carlo`, `api/walk_forward`) — payload structures align with `BacktestRequest`, `MonteCarloRequest`, `WFORequest`.
- `/ws/dashboard`: websocket endpoint exists; UI `ws_dashboard.js` connects.
- `/api/tear-sheet/pdf|html|json`: UI tear_sheet.js references; endpoint models (`TearSheetRequest`) match fields (`exchange`, `symbol`, `timeframe`, `params`, `validation_report`, `mc_simulations`, `target_metric`, etc.).
- `/api/portfolio/*` family: 10+ endpoints; UI references (`portfolio/snapshot`, `portfolio/var`, `portfolio/rebalance/plan`, etc.) — arguments (e.g., `include_synthetic`, `days`) match query/body params.
- `/api/paper/engine/step`, `/start`, `/stop`, `/status`: UI / ws_dashboard reference; endpoints accept correct request types (`PaperStepRequest`, `PaperPollingRequest`).
- `/api/paper/positions` (GET with `exchange` opt) and `/api/paper/positions/{exchange}/{symbol:path}/close`: UI uses position list + close; match.

## 3. Broken / missing links summary
- BROKEN (auth): `/api/db/repair` — UI missing `X-API-Key` header; 403 guaranteed.
- BROKEN (auth impact): `maintenance-vacuum-btn` (line 453) also POSTs to `/api/db/repair`; same failure.
- MISSING (not broken): `/api/optimizer/*` endpoint family not implemented; UI never references it.
- MISSING (optional): UI has no direct link to `/api/paper/profiles/{exchange}/{symbol}` (GET single profile); not required for workflow.
- VERIFIED intact: all `/api/paper/profiles/*` deploy/list links, `/api/optimize`, `/api/strategy/validate`, tear-sheet, portfolio, and paper-engine links pass correct parameter names/types.

## 4. Recommendations
1. Add `headers: {"X-API-Key": ...}` to `/api/db/repair` fetch, or expose repair via unauthenticated admin path if intended for local use.
2. If `/api/optimizer/*` sub-routes are planned, create them or remove references from docs; currently no UI impact.
3. For full traceability, capture `baseParams` construction in app.js (line ~1600) and assert `params` is always an object before `/api/strategy/validate` calls; currently safe due to caller control.

File: workspace/traceability_ui_review.md (this file). Source inspected: C:/crypto/ema_vwap/src/app.py (95k lines), src/web/app.js, src/web/index.html, src/web/ws_dashboard.js, src/web/tear_sheet.js.
