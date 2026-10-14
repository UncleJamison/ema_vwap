# Session-Specific Fixes (2026-09)

## Missing DB Schema / Method Alerts
- **Symptom:** `AttributeError: 'CandleDatabase' object has no attribute 'save_alert'` when the alerting module calls `db.save_alert(entry)`.
- **Root cause:** The `CandleDatabase` class was missing both the `alerts` table in `init_db()` and the `save_alert()` method.
- **Fix:** Add `CREATE TABLE IF NOT EXISTS alerts` in `init_db()` and implement `save_alert(self, entry)` that persists to the alerts table with `(timestamp, type, level, message, metadata, channels_sent)`.
- **Pitfall:** Do not assume `CandleDatabase` has arbitrary methods — verify the class's public interface before integrating modules that call them.

## Windows pytest File-Lock Issue
- **Symptom:** `PermissionError: [WinError 5] Access is denied: 'C:\Users\Josh\AppData\Local\Temp\pytest-of-Josh\pytest-current'` on every pytest cleanup.
- **Root cause:** Windows locks the pytest temp directory (`pytest-current`), and pytest's `tmp_path_factory._exit_stack.close()` cannot unlink it.
- **Fix:** Set `TMP=C:/tmp/pytest_isolated_root` (or any isolated directory) before running pytest to redirect temp files.
- **Pitfall:** Do not rely on `--rootdir` to control pytest temp cleanup — it does not. Always use the `TMP` environment variable on Windows.

## Test Expectation Mismatches
- **Symptom:** Tests fail due to case sensitivity or column order mismatches (for example, `"no repair needed"` versus `"No repair needed"`, or `lines[0].startswith("timestamp,open")` versus an exact match).
- **Root cause:** Maintenance method messages or CSV column order do not match test assertions exactly.
- **Fix:** Ensure exact string and case matching between implementation and test expectations.
- **Pitfall:** When adding new methods or changing schema, run the affected tests first and fix mismatches before considering the work complete.

## Export Column Order
- **Symptom:** `test_export_candles_csv_with_data` fails because CSV column order does not match test expectations.
- **Root cause:** `export_candles_csv` uses columns `["timestamp", "exchange", "symbol", "timeframe", "open", "high", "low", "close", "volume"]`, but the test expects `lines[0]` to start with `"timestamp,open"`.
- **Fix:** Match column order to test expectations — CSV: `["timestamp", "open", "high", "low", "close", "volume", "exchange", "symbol", "timeframe"]`; JSON: `["timestamp", "exchange", "symbol", "timeframe", "open", "high", "low", "close", "volume"]`.
- **Pitfall:** Do not assume column order — always verify against test assertions when adding export methods.
