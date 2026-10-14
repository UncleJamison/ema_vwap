AUDIT SUMMARY — src/database.py + paper consumers (no edits; no backups removed)

AUTHORITATIVE SCHEMAS (database.py 27-48 _DOMAIN_TABLES + DDL 51-454)
- candles.db: candles
- batch_results.db: batch_results, optuna_profiles
- paper_trading.db: paper_ledger, paper_ledger_transactions, paper_positions, paper_profiles, paper_trade_history
- system.db: background_tasks, settings, alerts

CONSUMER ALIGNMENT (exact file:line evidence)
- paper/engine.py:34 get_database("paper_trading"); 143 get_position; 252 record_completed_trade; 358 save_position
- paper/ledger.py:19 db=get_database("paper_trading"); 26 PaperLedger init; 297 save_position -> db.save_paper_position
- paper/profiles.py:14 db; 43 save_paper_profile -> db.save_paper_profile; 54 get_profile -> db.get_paper_profile
- paper/models.py: PaperProfile (10), PaperPosition (33), LedgerTransaction (71), PaperTradeRecord (90) match DB DDL 119-301
- db_connection_manager.py:17-22 DB_MAP confirms 4-db routing

INCOMPATIBILITIES / LEGACY FLAGS
- portfolio_balances (database.py 358-393): legacy rebuild migration (old account-level JSON -> per-currency); NOT in _DOMAIN_TABLES
- optuna_profiles (302-357): drop/rebuild if best_params missing (line 330)
- batch_results (99-112): ALTER favorite/validation_status/validation_json if missing
- paper_profiles (167-214): rename/recreate if UNIQUE missing
- init_db drop guard (616-640): only drops if table in domain and required columns missing; protects orphaned cross-DB tables

DB STATE (C:/crypto/ema_vwap/data)
- candles.db (61KB), batch_results.db (28KB), paper_trading.db (73KB), system.db (40KB) — small/current
- Degraded: candles.db.corrupt_*(-5 files, 416MB-867MB), candles.db.current, .fixed, .backup, candles_backup/, db_backup_20260914_145120

REMEDIATION (clean reset — not executed)
- Delete all .corrupt_*, .backup, .current, .fixed, candles_backup/, db_backup_*
- Remove candles.db/paper_trading.db/batch_results.db/system.db; init_db recreates per _DOMAIN_TABLES with correct DDL/migrations
- DB_JOURNAL_MODE / docker logic (596-609) handles WAL vs DELETE automatically
