PR #1 / scale-lookback-windows audit — 2026-09-16
Branch checked: scale-lookback-windows (remote) vs main (working)

VERIFIED INTACT (working tree vs origin/main):
- src/config.py: regime_sweep_days=360, REGIME_SWEEP_* constants added; bars_per_day=288.0 in WFOConfig.
- src/wfo.py: auto-detect replaced with WFOConfig.bars_per_day; compute_wfo_auto_params updated.
- src/web/app.js: Optuna validation guard added (line ~1446).
- src/app.py, execution/algorithms.py, regime.py, risk_manager.py, three_kt.py, validator.py: diffs present.

DISCREPANCIES FOUND:
1. src/config.py max_holding_bars_max = 96 (should be 500 per PR / ask). PR branch sets 500; working tree still 96.
2. src/config.py regime_sweep_days = 360 (ask specifies 365). PR and working tree both 360.
3. src/data_loader.py tf_days_map NOT updated — still old (1m/3m/5m=1, 15m=2, 30m=4, 1h=8, 1d=180). PR branch has scaled (15m=280, 30m=500, 1h=1000, 1d=7300). Critical: data load does not match lookback scaling.
4. src/batch_optimizer.py (src/ path): days=360 (correct per PR), but max_holding_bars_max=96 (should be 500).
5. Root-level batch_optimizer.py (untracked): days=180, max_holding_bars_max=96 — stale duplicate, not aligned with PR.
6. UI index.html: no meaningful change vs PR (only CRLF); lookback buttons unchanged (30/90/180D) — not scaled to 360/365.

INTEGRATION GAPS:
- Data loader (old tf_days_map) vs config (scaled regime sweep / 360 days) = load size mismatch.
- Batch optimizer max_holding (96) vs config OptunaConfig (96) consistent but wrong target (500).
- WFO auto-detect removed; relies solely on WFOConfig.bars_per_day — ok if config correct.

FILES CREATED: PR_AUDIT_REPORT.md (this file). No modifications made to source.
