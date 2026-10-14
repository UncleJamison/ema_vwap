PYTEST FAILURE ROOT-CAUSE MAP — 20 failed / 283 passed / 2 skipped (C:/crypto/ema_vwap)
Source: C:/Users/Josh/AppData/Local/Temp/ema_vwap-pytest-debug/full.log
Investigated: src/database.py (CandleDatabase domain routing, lines 27-665, 686-698, 1996+); db_connection_manager.py
No files edited.

=== SINGLE ROOT CAUSE (dominant) ===
Database domain routing init_db (src/database.py 686-698) unconditionally drops & recreates domain tables AND drops all non-domain tables (lines 676-684). Tests using generic/arbitrary test DB filenames (test_concurrency.db, test_app_lifespan.db, test_paper_1m_*.db, etc.) are resolved to partial or wrong domains via basename/keyword inference (lines 623-654), then the init drops needed tables (e.g., paper_profiles/settings/portfolio_balances) before operations run.
Evidence: log lines 121-144 (test_app_lifespan_auto_resumes_runner) show "Recreating domain table 'settings' in test_app_lifespan.db" — settings recreated but paper_profiles missing, so engine stays in standby (line 131) → assert False (line 115-117). Same pattern for all "no such table" errors.

=== FAILURE GROUPS (with exact refs) ===

GROUP A — "sqlite3.OperationalError: no such table: <table>" (15 tests, direct root cause above)
- test_app.py:342 test_paper_engine_and_ledger_endpoints → save_setting fails, table "settings" missing (line 104)
- test_database_concurrency.py:107 test_multi_threaded_read_write_concurrency → writer_counts sum 0 (line 149); thread exceptions at 466-473 show "portfolio_balances" missing; init dropped it from test_concurrency.db (keyword "wal"/"concurrency" resolves to all domains initially, but init then drops non-matching — actually here it resolves to all, then drop logic removes wrong ones — more precisely: basename "test_concurrency.db" hits line 641 "wal/concurrency" → all tables, but then some still missing due to domain mismatch with connector paths)
- test_database_concurrency.py:187 test_portfolio_balances_crud → line 159 "portfolio_balances" missing
- test_migrate_data.py:175 test_rollback_restores_monolithic_db → assertion 'candles' not in tables (line 163); destination DB rebuilt by init missing candles (monolithic rollback expects candles preserved in dest)
- test_migrate_data.py:233 test_verify_integrity_reports_mismatch_on_stale_dest → expected 'mismatch' got 'verified' (line 167) — stale dest DB was overwritten by init recreation
- test_paper_1m_live_path.py:26 / 80 → "paper_profiles" missing (line 181/191) from paper_trading.db init drop
- test_parameter_flow.py:267 / 307 → save_profile (line 203) + stop_polling save_setting (line 215) both missing (profiles/settings)
- test_portfolio_aggregator.py:48 / 97 / 133 → get_unified_snapshot → list_profiles needs paper_profiles (lines 236, 280, 311); endpoints return 500 (lines 248-249, 338-339, 411-412)
- test_portfolio_rebalancer.py:38 / 64 / 108 → same paper_profiles gap (lines 311, 334, 339)
- test_portfolio_risk.py:30 / 94 / 130 → same (lines 370, 399, 412, 415)
- test_validator.py:159 / 189 → settings table missing (lines 433, 448) via settings manager using default DB_PATH (candles.db) instead of system.db; init recreated candles domain on system DB? Actually settings uses db_path=DB_PATH (candles.db) but tries to access settings — domain routing didn't put settings into candles.db domain; settings lives only in system.db (line 43-47 of _DOMAIN_TABLES)

GROUP B — "assert False is True" (engine standby, 1 test)
- test_app.py:417 test_app_lifespan_auto_resumes_runner → engine.is_running False (line 117). Log 121-144: lifespan init recreates tables in test_app_lifespan.db (partial domain), settings created but paper_profiles/settings reconciliation missing → auto-resume condition fails, engine kept standby (line 131/104). Related: paper runner needs settings + paper_profiles both present to resume.

GROUP C — concurrency writer counts zero (1 test, symptom of Group A)
- test_database_concurrency.py:107: writer_counts [0,0,0,0,0] (line 150). Writers throw sqlite3 errors (portfolio_balances / settings / paper_profiles missing) caught by except block (line 77 catches RuntimeError/ValueError/TypeError/KeyError — NOT sqlite3.OperationalError!), so errors propagate as unhandled thread exceptions (warnings 458-657) rather than incrementing counts. Fix: either include sqlite3.OperationalError in catch or fix table presence.

GROUP D — migration integrity / rollback (2 tests)
- test_migrate_data.py:175 / 233: monolithic DB rollback and integrity-check against stale dest. Init_db recreation (lines 686-698) wipes and rebuilds destination DBs, destroying source/migrated data expected by rollback/integrity tests.

=== RECOMMENDED FIXES (no file edits made) ===
1. src/database.py line 686-698 (AND 676-684): Stop unconditional DROP/CREATE in init_db for test/non-default DBs. Use IF NOT EXISTS / schema-check instead of destructive recreation; only drop tables genuinely foreign to resolved domain.
2. src/database.py lines 623-654 (basename inference): Add "portfolio_balances" to paper_trading or system domain mapping (currently missing from _DOMAIN_TABLES, line 27-48); add it so concurrency tests work. Also ensure generic test DBs get ALL tables (line 645-649 already does for "test" keyword, but verify basename "test_concurrency" hits correctly — it does at 641 but may be overridden by earlier partial match).
3. src/settings.py / paper/engine.py / paper/profiles.py: If code uses default db (DB_PATH = candles.db) for settings/paper_profile operations, route to correct domain via db_connection_manager.get_database("system") / get_database("paper_trading") — or change default db_path per caller.
4. tests/test_database_concurrency.py line 77: Expand exception catch to include sqlite3.OperationalError so writer failures register instead of unhandled thread exceptions; or fix root table presence.
5. tests/test_migrate_data.py: Either mock init_db or use dedicated migration fixtures that bypass CandleDatabase init, since destructive init destroys rollback/integrity expectations.
6. src/app.py lifespan (~line 84-104): Verify both settings and paper_profiles tables exist in the lifespan DB before evaluating auto-resume; currently relies on init recreation which can leave partial domain.

=== VERIFICATION STATUS ===
Evidence from log verified at file/line references above; code inspected at src/database.py (init_db 591-700, _DOMAIN_TABLES 27-48, wrapper 468-501); no modifications performed; 20 distinct test failures mapped to 4 groups rooted in one routing/init change.
