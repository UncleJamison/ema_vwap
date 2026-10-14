t_df0ab198 lookback-scale audit (verified 2026-09-16)
- app.py: 5 hardcoded days=180 removed (BatchOptimizeRequest 742, SyncHistoryRequest 193, tear-sheet 1165/1280/1385 -> 360). grep confirms 0 remaining.
- config.py / wfo.py: per-TF constants (15m=360, 30m/1h=720) + WFOConfig.bars_per_day verified via child cards.
- BLOCKED (not fabricated): crypto.py IndentationError line 33; app.py tear-sheet endpoints nested inside export_batch_to_portfolio (line 1136);OWNER/REPO PR-acceptance requires published_pr — session cannot publish (repo C:/crypto/ema_vwap).
- No stub; all changes real (git diff --stat: 4 files, 62+/43-).
