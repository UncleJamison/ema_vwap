What I did: Analyzed the pytest log at C:/Users/Josh/AppData/Local/Temp/ema_vwap-pytest-debug/full.log and relevant source files to identify the root cause of 20 test failures after the database routing change.

What I found: The dominant root cause is in src/database.py lines 686-698 where init_db() unconditionally drops and recreates domain tables, plus lines 676-684 that drop all non-domain tables. When tests use generic/arbitrary test DB filenames (test_concurrency.db, test_app_lifespan.db, etc.), the domain resolution logic (lines 623-654) maps them to partial/wrong domains, causing init_db() to drop needed tables (settings, paper_profiles, portfolio_balances, etc.) before tests run. This explains:
- 15 "no such table" errors (settings, paper_profiles, portfolio_balances, etc.)
- 1 engine standby failure (missing settings/paper_profiles)
- 1 concurrency writer count zero (exceptions not caught properly)
- 2 migration test failures (init_db overwrote expected state)

Files examined: src/database.py (domain routing, _DOMAIN_TABLES, init_db wrapper), src/db_connection_manager.py, test files showing failures. No files were modified per instructions.

The detailed root-cause map with exact file/line references and recommended fixes is saved at: C:/crypto/ema_vwap/pytest_root_cause_map.md