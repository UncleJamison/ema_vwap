# TRACEABILITY AUDIT — BUSINESS LOGIC LAYER

**Generated**: 2025-10-07  
**Scope**: Map every function in business logic modules called by API endpoints in `src/app.py`  
**Modules audited**: `src/paper/engine.py`, `src/paper/ledger.py`, `src/paper/profiles.py`, `src/backtester.py`, `src/optimizer.py`, `src/batch_optimizer.py`, `src/data_loader.py`, `src/wfo.py`, `src/monte_carlo.py`, `src/risk_manager.py`, `src/portfolio/*.py`, `src/validator.py`, `src/strategy.py`

---

## Summary

| Module | Functions Called by API | API Endpoints |
|--------|------------------------|---------------|
| `src/paper/engine.py` | 10 | `/api/paper/engine/step`, `/api/paper/engine/start`, `/api/paper/engine/stop`, `/api/paper/engine/reconcile`, `/api/paper/engine/status`, `/api/paper/positions`, `/api/paper/positions/{ex}/{sym}/close`, `/api/paper/positions/{ex}/{sym}/sl_tp` |
| `src/paper/ledger.py` | 12 | `/api/paper/ledger/transactions`, `/api/paper/ledger/deposit`, `/api/paper/ledger/reset`, `/api/paper/trades/history`, `/api/paper/performance` |
| `src/paper/profiles.py` | 8 | `/api/paper/profiles/deploy`, `/api/paper/profiles`, `/api/paper/profiles/{ex}/{sym}`, `/api/paper/profiles/{id}/toggle-active`, `/api/paper/profiles/{id}`, `/api/paper/profiles/{id}/trade-direction`, `/api/batch_results/{id}/import_profile` |
| `src/backtester.py` | 2 | `/api/backtest`, `/api/tear-sheet/*`, `/api/monte_carlo`, `/api/strategy/validate`, `/api/optimize`, `/api/walk_forward` |
| `src/optimizer.py` | 1 | `/api/optimize`, `/api/batch_optimize` |
| `src/batch_optimizer.py` | 2 | `/api/batch_optimize`, `/api/batch_results/auto-validate` |
| `src/data_loader.py` | 4 | Nearly all endpoints (candle loading) |
| `src/wfo.py` | 2 | `/api/walk_forward`, `/api/strategy/validate` |
| `src/monte_carlo.py` | 2 | `/api/monte_carlo`, `/api/tear-sheet/*`, `/api/strategy/validate` |
| `src/risk_manager.py` | 3 | `/api/paper/engine/step`, `/api/backtest`, `/api/portfolio/size_position` |
| `src/portfolio/aggregator.py` | 3 | `/api/portfolio/snapshot`, `/api/portfolio/venues`, `/api/portfolio/positions`, `/api/portfolio/refresh`, `/api/portfolio/history` |
| `src/portfolio/risk_engine.py` | 1 | `/api/portfolio/var`, `/api/portfolio/size_position` |
| `src/portfolio/risk_budget.py` | 1 | `/api/portfolio/size_position` |
| `src/portfolio/portfolio_risk_budget.py` | 1 | `/api/batch_results/export_to_portfolio` (risk_budget mode), `/api/portfolio/risk_budget` |
| `src/portfolio/rebalancer.py` | 2 | `/api/portfolio/rebalance/plan`, `/api/portfolio/rebalance/execute` |
| `src/portfolio/order_router.py` | 1 | `/api/portfolio/route_order`, `/api/portfolio/rebalance/execute` |
| `src/validator.py` | 1 | `/api/strategy/validate`, `/api/tear-sheet/*` |
| `src/strategy.py` | 2 | `/api/backtest`, `/api/tear-sheet/*`, `/api/optimize`, `/api/walk_forward`, `/api/strategy/validate` |

---

## Detailed Mapping

### 1. Paper Trading Engine (`src/paper/engine.py`)

#### `PaperTradingEngine.evaluate_symbol`
- **Signature**: `evaluate_symbol(self, exchange: str, symbol: str, timeframe: str = "5m", force_candles: pd.DataFrame | None = None, fetch_live: bool = False, force_signals: pd.DataFrame | None = None) -> dict[str, Any]`
- **Called by**: `POST /api/paper/engine/step` (line 1964), `POST /api/paper/engine/step` via `step_all_active_profiles` (line 840)
- **Input params source**: 
  - `PaperStepRequest.exchange`, `PaperStepRequest.symbol`, `PaperStepRequest.timeframe`
  - `PaperProfile` from registry (params, timeframe)
  - Internal: `fetch_live=True` when manual step, `False` for polling
- **Return type**: `dict[str, Any]` with keys: `exchange`, `symbol`, `timeframe`, `action` (ENTER_LONG/ENTER_SHORT/EXIT_STOP_LOSS/EXIT_TAKE_PROFIT/EXIT_TIMEOUT/HOLD/NONE), `entry_price`/`exit_price`/`current_price`, `quantity`, `stop_loss`, `take_profit`, `unrealized_pnl`, `unrealized_pnl_pct`, `realized_pnl`, `realized_pnl_pct`, `timestamp`, `status`, `signal`, `note`, `error`
- **Side effects**: 
  - DB writes via `ledger.save_position`, `ledger.record_close_position`, `ledger.record_completed_trade`, `ledger.close_position`
  - In-memory cache `_record_evaluation`
  - Ledger transactions (BUY/SELL/SELL_SHORT/BUY_TO_COVER/DEPOSIT/WITHDRAWAL)
- **Error handling**: Returns `{"status": "insufficient_data"}` or `{"status": "error", "error": str(e)}` on exception; caught by API endpoint and raised as `HTTPException(500)`

#### `PaperTradingEngine.close_position_market`
- **Signature**: `close_position_market(self, exchange: str, symbol: str, current_price: float | None = None) -> PaperTradeRecord | None`
- **Called by**: `POST /api/paper/positions/{exchange}/{symbol:path}/close` (line 2062)
- **Input params source**: Path params `exchange`, `symbol`; optional `current_price` (None = use position's current_price)
- **Return type**: `PaperTradeRecord | None`
- **Side effects**: 
  - DB writes via `ledger.record_close_position`, `ledger.record_completed_trade`, `ledger.close_position`
  - Ledger transactions (SELL/BUY_TO_COVER)
- **Error handling**: Returns `None` if position not found; API raises `HTTPException(404)`

#### `PaperTradingEngine.step_all_active_profiles`
- **Signature**: `step_all_active_profiles(self) -> list[dict[str, Any]]`
- **Called by**: `POST /api/paper/engine/step` (line 1972) when no specific symbol, WebSocket `step_engine` message (line 2701), background polling loop (line 1174)
- **Input params source**: Active profiles from `registry.list_profiles(active_only=True)`
- **Return type**: `list[dict[str, Any]]` — list of `evaluate_symbol` results
- **Side effects**: 
  - Multiple `evaluate_symbol` calls with cached candles/signals
  - Updates `_last_poll_time`, `_last_poll_metrics`
- **Error handling**: Individual symbol errors caught, logged, returns error dict in results; overall continues

#### `PaperTradingEngine.start_polling`
- **Signature**: `start_polling(self, interval_seconds: int = 15, persist: bool = True) -> bool`
- **Called by**: `POST /api/paper/engine/start` (line 1986), lifespan startup (line 120)
- **Input params source**: `PaperPollingRequest.interval_seconds`; `persist=True` saves to settings
- **Return type**: `bool` (True if started, False if already running)
- **Side effects**: 
  - Starts background thread `_poll_loop`
  - Calls `reconcile_positions_on_startup()` first
  - Persists `paper_runner_enabled=true` and `paper_polling_interval` to settings DB
- **Error handling**: Returns False if already running; thread errors logged, circuit-breaker after 10 consecutive errors

#### `PaperTradingEngine.stop_polling`
- **Signature**: `stop_polling(self, persist: bool = True) -> bool`
- **Called by**: `POST /api/paper/engine/stop` (line 1997), lifespan shutdown (line 127)
- **Input params source**: `persist=True` saves to settings
- **Return type**: `bool` (True if stopped, False if not running)
- **Side effects**: Sets `_stop_event`, joins thread, persists `paper_runner_enabled=false`

#### `PaperTradingEngine.reconcile_positions_on_startup`
- **Signature**: `reconcile_positions_on_startup(self, force_candles_map: dict[str, pd.DataFrame] | None = None) -> list[dict[str, Any]]`
- **Called by**: `POST /api/paper/engine/reconcile` (line 2008), `start_polling` (line 1152)
- **Input params source**: Open positions from ledger; optional `force_candles_map` for testing
- **Return type**: `list[dict[str, Any]]` — each with `exchange`, `symbol`, `action` (RECONCILE_EXIT_*/RECONCILE_UPDATED_MTM), `exit_price`, `realized_pnl`, `current_price`, `unrealized_pnl`, `timestamp`
- **Side effects**: 
  - DB writes via `ledger.record_close_position`, `ledger.record_completed_trade`, `ledger.close_position`, `ledger.save_position` (updated MTM)
- **Error handling**: Logs errors per position, continues; API wraps in try/except → `HTTPException(500)`

#### `PaperTradingEngine.get_status`
- **Signature**: `get_status(self) -> dict[str, Any]`
- **Called by**: `GET /api/paper/engine/status` (line 2022), WebSocket init (line 2644), WebSocket `get_status` (line 2670)
- **Input params source**: None (reads internal state)
- **Return type**: `dict` with `is_running`, `account_id`, `cash_balance`, `total_equity`, `unrealized_pnl`, `active_profiles_count`, `open_positions_count`, `last_poll_time`, `last_evaluations`, `last_poll_metrics`, `performance`
- **Side effects**: Prunes evaluation cache
- **Error handling**: None (read-only)

#### `PaperTradingEngine.close_position_manual`
- **Signature**: `close_position_manual(self, exchange: str, symbol: str, exit_price: float | None = None) -> dict[str, Any]`
- **Called by**: `POST /api/paper/positions/{exchange}/{symbol:path}/close` (line 2062) — thin wrapper
- **Input params source**: Path params; optional `exit_price`
- **Return type**: `dict` with `exchange`, `symbol`, `action`, `exit_price`, `realized_pnl`, `realized_pnl_pct`, `timestamp`
- **Side effects**: Delegates to `close_position_market`
- **Error handling**: Raises `ValueError` if no position; API catches → `HTTPException(404)`

#### `PaperTradingEngine.update_position_sl_tp`
- **Signature**: `update_position_sl_tp(self, exchange: str, symbol: str, stop_loss: float | None = None, take_profit: float | None = None) -> dict[str, Any]`
- **Called by**: `POST /api/paper/positions/{exchange}/{symbol:path}/sl_tp` (line 2103)
- **Input params source**: Path params; `UpdateSLTPRequest.stop_loss`, `UpdateSLTPRequest.take_profit`
- **Return type**: `dict` (position dict via `pos.to_dict()`)
- **Side effects**: `ledger.save_position(pos)`
- **Error handling**: Raises `ValueError` if no position; API catches → `HTTPException(404)`

#### `PaperTradingEngine.get_balance_snapshot`
- **Signature**: `get_balance_snapshot(self) -> dict[str, Any]`
- **Called by**: `PortfolioAggregator.get_unified_snapshot` via `PaperTradingBalanceConnector` (indirect)
- **Input params source**: None
- **Return type**: `dict` with `cash_usd`, `stablecoin_usd`, `crypto_usd`, `stock_usd`, `positions`, `total_nav_usd`, `buying_power_usd`, `unrealized_pnl_usd`
- **Side effects**: None (read-only)

---

### 2. Paper Ledger (`src/paper/ledger.py`)

#### `PaperLedger.get_balance`
- **Signature**: `get_balance(self, account_id: str = "default") -> float`
- **Called by**: Engine (sizing), `GET /api/paper/performance` (line 2267), portfolio connector
- **Input params source**: `account_id` (default "default")
- **Return type**: `float`
- **Side effects**: None (read)
- **Error handling**: Returns 0.0 if no account

#### `PaperLedger.get_total_equity`
- **Signature**: `get_total_equity(self, account_id: str = "default") -> float`
- **Called by**: Engine (position sizing)
- **Input params source**: `account_id`
- **Return type**: `float` (cash + position values)
- **Side effects**: None (read)

#### `PaperLedger.deposit`
- **Signature**: `deposit(self, account_id: str = "default", amount: float = 10000.0, notes: str = "Initial deposit") -> LedgerTransaction`
- **Called by**: `POST /api/paper/ledger/deposit` (line 2180)
- **Input params source**: Query/body `account_id`, `amount`, `notes`
- **Return type**: `LedgerTransaction`
- **Side effects**: DB `record_ledger_transaction` (DEPOSIT)
- **Error handling**: Raises `ValueError` for invalid amount; API catches → `HTTPException(400)`

#### `PaperLedger.withdraw`
- **Signature**: `withdraw(self, account_id: str = "default", amount: float = 1000.0, notes: str = "Withdrawal") -> LedgerTransaction`
- **Called by**: Not directly by API (available for future)
- **Input params source**: N/A
- **Return type**: `LedgerTransaction`
- **Side effects**: DB `record_ledger_transaction` (WITHDRAWAL)
- **Error handling**: Raises `ValueError` for insufficient funds

#### `PaperLedger.record_buy`
- **Signature**: `record_buy(self, account_id: str, exchange: str, symbol: str, quantity: float, price: float, fee: float = 0.0, notes: str = "", metadata: dict | None = None) -> LedgerTransaction`
- **Called by**: Engine `evaluate_symbol` (line 640), `close_position_market` (manual close LONG)
- **Input params source**: Engine-computed values
- **Return type**: `LedgerTransaction`
- **Side effects**: DB `record_ledger_transaction` (BUY), updates balance
- **Error handling**: Raises `ValueError` if insufficient funds

#### `PaperLedger.record_sell`
- **Signature**: `record_sell(self, account_id: str, exchange: str, symbol: str, quantity: float, price: float, fee: float = 0.0, notes: str = "", metadata: dict | None = None) -> LedgerTransaction`
- **Called by**: Engine `close_position_market` (LONG exit), `reconcile_positions_on_startup` (LONG exit)
- **Input params source**: Engine-computed values
- **Return type**: `LedgerTransaction`
- **Side effects**: DB `record_ledger_transaction` (SELL), updates balance
- **Error handling**: None (sell adds to balance)

#### `PaperLedger.record_sell_short`
- **Signature**: `record_sell_short(self, account_id: str, exchange: str, symbol: str, quantity: float, price: float, cost_basis: float, fee: float = 0.0, notes: str = "", metadata: dict | None = None) -> LedgerTransaction`
- **Called by**: Engine `evaluate_symbol` (line 629) for SHORT entries
- **Input params source**: Engine-computed values
- **Return type**: `LedgerTransaction`
- **Side effects**: DB `record_ledger_transaction` (SELL_SHORT), updates balance
- **Error handling**: Raises `ValueError` if insufficient funds

#### `PaperLedger.record_close_position`
- **Signature**: `record_close_position(self, account_id: str, pos: PaperPosition, exit_price: float, fee: float = 0.0, notes: str = "", metadata: dict | None = None) -> LedgerTransaction`
- **Called by**: Engine (SL/TP/Timeout exits), `reconcile_positions_on_startup`, `close_position_market`
- **Input params source**: Position object, exit price, fee
- **Return type**: `LedgerTransaction`
- **Side effects**: DB `record_ledger_transaction` (SELL or BUY_TO_COVER), updates balance
- **Error handling**: None

#### `PaperLedger.save_position`
- **Signature**: `save_position(self, pos: PaperPosition) -> int`
- **Called by**: Engine (entry, MTM updates, SL/TP updates), `reconcile_positions_on_startup`, `update_position_sl_tp`
- **Input params source**: `PaperPosition` object
- **Return type**: `int` (row ID)
- **Side effects**: DB `save_paper_position` (upsert)
- **Error handling**: None

#### `PaperLedger.get_position`
- **Signature**: `get_position(self, exchange: str, symbol: str, account_id: str = "default") -> PaperPosition | None`
- **Called by**: Engine (entry/exit checks), `GET /api/paper/positions` (line 2029), `close_position_market`, `update_position_sl_tp`
- **Input params source**: Path/query params
- **Return type**: `PaperPosition | None`
- **Side effects**: None (read)
- **Error handling**: Returns None if not found

#### `PaperLedger.list_positions`
- **Signature**: `list_positions(self, exchange: str | None = None, account_id: str = "default") -> list[PaperPosition]`
- **Called by**: `GET /api/paper/positions` (line 2029), `GET /api/paper/performance` (line 2268), Engine `get_status`, `reconcile_positions_on_startup`
- **Input params source**: Query `exchange` filter
- **Return type**: `list[PaperPosition]`
- **Side effects**: None (read)

#### `PaperLedger.close_position`
- **Signature**: `close_position(self, exchange: str, symbol: str, account_id: str = "default") -> bool`
- **Called by**: Engine (after exit recorded), `reconcile_positions_on_startup`
- **Input params source**: Engine context
- **Return type**: `bool`
- **Side effects**: DB `close_paper_position` (removes from active)
- **Error handling**: Returns False if not found

#### `PaperLedger.record_completed_trade`
- **Signature**: `record_completed_trade(self, trade: PaperTradeRecord) -> int`
- **Called by**: Engine (all exits), `reconcile_positions_on_startup`
- **Input params source**: `PaperTradeRecord` built by engine
- **Return type**: `int` (row ID)
- **Side effects**: DB `record_paper_trade_history`
- **Error handling**: None

#### `PaperLedger.list_trade_history`
- **Signature**: `list_trade_history(self, exchange: str | None = None, symbol: str | None = None, exit_reason: str | None = None, limit: int = 100) -> list[PaperTradeRecord]`
- **Called by**: `GET /api/paper/trades/history` (line 2228)
- **Input params source**: Query params
- **Return type**: `list[PaperTradeRecord]`
- **Side effects**: None (read)

#### `PaperLedger.get_statistics`
- **Signature**: `get_statistics(self, exchange: str | None = None, symbol: str | None = None) -> dict[str, Any]`
- **Called by**: `GET /api/paper/performance` (line 2266), Engine `get_status`
- **Input params source**: Query filters
- **Return type**: `dict` (trade stats: count, win_rate, avg_pnl, etc.)
- **Side effects**: None (read)

#### `PaperLedger.list_transactions`
- **Signature**: `list_transactions(self, account_id: str = "default", exchange: str | None = None, symbol: str | None = None, limit: int = 100) -> list[LedgerTransaction]`
- **Called by**: `GET /api/paper/ledger/transactions` (line 2134)
- **Input params source**: Query params
- **Return type**: `list[LedgerTransaction]`
- **Side effects**: None (read)

#### `PaperLedger.reset`
- **Signature**: `reset(self, account_id: str = "default") -> dict[str, int]`
- **Called by**: `POST /api/paper/ledger/reset` (line 2208)
- **Input params source**: `account_id` query
- **Return type**: `dict` with deleted counts
- **Side effects**: DB `reset_paper_account` (wipes all paper data, re-seeds $10k)
- **Error handling**: None

---

### 3. Paper Profiles (`src/paper/profiles.py`)

#### `PaperProfileRegistry.save_profile`
- **Signature**: `save_profile(self, exchange: str, symbol: str, timeframe: str, strategy_mode: str, target_metric: str, params: dict | StrategyParams | str, optuna_score: float = 0.0, is_active: bool = True, deployed: bool = False, deployed_at: str | None = None, validation_report: dict | None = None) -> int`
- **Called by**: `POST /api/paper/profiles/deploy` (line 1782), `import_batch_result_as_profile` (line 84)
- **Input params source**: `DeployPaperProfileRequest` fields; batch result fields
- **Return type**: `int` (profile_id)
- **Side effects**: DB `save_paper_profile` (upsert by exchange+symbol+timeframe+strategy_mode)
- **Error handling**: None

#### `PaperProfileRegistry.import_batch_result_as_profile`
- **Signature**: `import_batch_result_as_profile(self, exchange: str, symbol: str, timeframe: str, strategy_mode: str, target_metric: str, best_value: float, best_params: dict | str, risk_per_trade_pct: float | None = None, is_active: bool = True) -> int`
- **Called by**: `POST /api/batch_results/{result_id}/import_profile` (line 1473)
- **Input params source**: Batch result row from DB
- **Return type**: `int` (profile_id)
- **Side effects**: Calls `save_profile` with `deployed=False`
- **Error handling**: Wrapped in try/except → `HTTPException(500)`

#### `PaperProfileRegistry.get_profile`
- **Signature**: `get_profile(self, exchange: str, symbol: str, timeframe: str | None = None, strategy_mode: str | None = None) -> PaperProfile | None`
- **Called by**: `GET /api/paper/profiles/{exchange}/{symbol:path}` (line 1868), Engine `evaluate_symbol` via `get_strategy_params`, `reconcile_positions_on_startup`
- **Input params source**: Path/query params
- **Return type**: `PaperProfile | None`
- **Side effects**: None (read)
- **Error handling**: Returns None if not found

#### `PaperProfileRegistry.list_profiles`
- **Signature**: `list_profiles(self, exchange: str | None = None, active_only: bool = False, deployed_only: bool = False) -> list[PaperProfile]`
- **Called by**: `GET /api/paper/profiles` (line 1831), Engine `step_all_active_profiles` (line 793), `get_status` (line 1223), `get_balance_snapshot` (line 1252)
- **Input params source**: Query params
- **Return type**: `list[PaperProfile]`
- **Side effects**: None (read)

#### `PaperProfileRegistry.delete_profile`
- **Signature**: `delete_profile(self, profile_id: int) -> bool`
- **Called by**: `DELETE /api/paper/profiles/{profile_id}` (line 1920)
- **Input params source**: Path param `profile_id`
- **Return type**: `bool`
- **Side effects**: DB `delete_paper_profile`
- **Error handling**: Returns False if not found; API → `HTTPException(404)`

#### `PaperProfileRegistry.set_active`
- **Signature**: `set_active(self, profile_id: int, is_active: bool, deployed: bool | None = None) -> bool`
- **Called by**: `POST /api/paper/profiles/{profile_id}/toggle-active` (line 1911), `import_batch_result_as_profile` (line 1490)
- **Input params source**: Path `profile_id`, body `is_active`
- **Return type**: `bool`
- **Side effects**: DB `set_paper_profile_active`
- **Error handling**: Returns False if not found; API → `HTTPException(404)`

#### `PaperProfileRegistry.update_trade_direction`
- **Signature**: `update_trade_direction(self, profile_id: int, trade_direction: str) -> bool`
- **Called by**: `PATCH /api/paper/profiles/{profile_id}/trade-direction` (line 1935)
- **Input params source**: Path `profile_id`, body `trade_direction`
- **Return type**: `bool`
- **Side effects**: DB `update_paper_profile_trade_direction`
- **Error handling**: Returns False if not found; API → `HTTPException(404)`

#### `PaperProfileRegistry.get_strategy_params`
- **Signature**: `get_strategy_params(self, exchange: str, symbol: str, timeframe: str | None = None, strategy_mode: str | None = None, fallback_params: StrategyParams | None = None) -> StrategyParams`
- **Called by**: Engine `evaluate_symbol` (line 153), `step_all_active_profiles` (line 820), `reconcile_positions_on_startup` (line 910)
- **Input params source**: Exchange, symbol, timeframe, strategy_mode; fallback to default `StrategyParams()`
- **Return type**: `StrategyParams`
- **Side effects**: None (read)
- **Error handling**: Returns fallback or default `StrategyParams()` on any error

#### `PaperProfileRegistry.get_deployed_profile`
- **Signature**: `get_deployed_profile(self, exchange: str, symbol: str, timeframe: str | None = None, strategy_mode: str | None = None) -> PaperProfile | None`
- **Called by**: `get_strategy_params` (line 157) — internal fallback chain
- **Input params source**: Same as `get_profile`
- **Return type**: `PaperProfile | None`
- **Side effects**: None (read)

---

### 4. Backtester (`src/backtester.py`)

#### `BacktestEngine.run`
- **Signature**: `run(self, df: pd.DataFrame, warmup_bars: int = 0) -> BacktestResult`
- **Called by**: 
  - `POST /api/backtest` (line 747)
  - `POST /api/tear-sheet/*` (lines 199, 305, 402)
  - `POST /api/monte_carlo` (line 1054)
  - `POST /api/strategy/validate` → `StrategyValidator.validate` (line 185)
  - `POST /api/optimize` → `OptunaOptimizer.optimize` (line 448)
  - `POST /api/walk_forward` → `WalkForwardEngine.run` (lines 257, 264)
- **Input params source**: `StrategyParams` from request; `df` from `DataLoader.load_candles`
- **Return type**: `BacktestResult` (dataclass with 30+ fields: metrics, equity_curve, trades list)
- **Side effects**: None (pure computation)
- **Error handling**: Returns zero-trade `BacktestResult` on empty data; exceptions propagate to API → `HTTPException(500)`

#### `BacktestEngine._finalize_trade` (private, internal)
- **Signature**: `_finalize_trade(self, trade: Trade, exit_price: float, exit_time: str, exit_reason: str, duration_bars: int, is_stock: bool) -> float`
- **Called by**: Internal to `run()`
- **Return type**: `float` (portfolio PnL cash flow)
- **Side effects**: Mutates `Trade` object

---

### 5. Optimizer (`src/optimizer.py`)

#### `OptunaOptimizer.optimize`
- **Signature**: `optimize(self, df: pd.DataFrame, is_cancelled: Any = None, progress_callback: Any = None) -> dict[str, Any]`
- **Called by**: 
  - `POST /api/optimize` worker (line 953)
  - `POST /api/batch_optimize` → `BatchOptimizer._run_single_optuna` (line 433)
  - `POST /api/walk_forward` → `WalkForwardEngine.run` (line 256)
  - `POST /api/strategy/validate` → `StrategyValidator.validate` (via WFO)
- **Input params source**: `StrategyParams` (base), `OptunaConfig` (from request), `df` from `DataLoader`
- **Return type**: `dict` with keys: `target_metric`, `best_params`, `param_importance`, `top_trials`, `best_backtest_result`, `reproducible`, `parallelism_mode`, `optimization_mode`, `pareto_objectives`, `num_pareto_trials`, `selected_pareto_trial`, `best_value`, `best_value_valid`, `best_metric_value`, `pareto_frontier`
- **Side effects**: Creates Optuna study (in-memory or journal), runs trials
- **Error handling**: 
  - Returns `{"status": "cancelled"}` if cancelled
  - Raises `ValueError` for insufficient data (< 50 bars)
  - Config validation in `_validate_config` raises `ValueError` for invalid ranges
  - Invalid trials pruned via `optuna.TrialPruned`
  - Exceptions propagate to caller (API → `HTTPException(500)` or task failure)

#### `OptunaOptimizer._validate_config` (private)
- **Signature**: `_validate_config(self) -> None`
- **Called by**: `optimize()` entry
- **Side effects**: Raises `ValueError` on invalid config

---

### 6. Batch Optimizer (`src/batch_optimizer.py`)

#### `BatchOptimizer.run`
- **Signature**: `run(self, is_cancelled: Callable[[], bool] | None = None, progress_callback: Callable[[dict], None] | None = None, reset_state: bool = True, done_offset: int = 0, total_override: int | None = None, combo_label_prefix: str = "", mark_complete: bool = True) -> list[dict[str, Any]]`
- **Called by**: 
  - `POST /api/batch_optimize` background task (line 1290, 1366)
  - `POST /api/batch_results/auto-validate` → `_run_all_metrics_batch_task` (line 2847)
- **Input params source**: `BatchOptimizerConfig` from request; cancellation/progress from task manager
- **Return type**: `list[dict]` (batch results from DB)
- **Side effects**: 
  - Updates global `_batch_state` (progress, current_combo, done/total)
  - For each combo: calls `_run_single_optuna` → persists via `db.save_batch_result`
- **Error handling**: 
  - Checks `_is_cancelled()` before each combo
  - Catches `TaskCancelledException` and generic `Exception` → updates state with error
  - Individual combo failures logged, continues to next

#### `BatchOptimizer._run_single_optuna`
- **Signature**: `_run_single_optuna(self, symbol: str, timeframe: str, is_cancelled: Callable[[], bool] | None = None, progress_callback: Callable[[float, str], None] | None = None) -> None`
- **Called by**: `run()` per combo
- **Input params source**: Config bounds (EMA, slope, etc.), `DataLoader.load_candles`
- **Return type**: `None` (persists to DB internally)
- **Side effects**: 
  - Creates `OptunaOptimizer`, runs `optimize()`
  - Validates result (min_trades, best_value_valid)
  - `db.save_batch_result` with full metrics
- **Error handling**: Skips combo on insufficient candles or invalid result; logs and continues

---

### 7. Data Loader (`src/data_loader.py`)

#### `DataLoader.load_candles` (classmethod)
- **Signature**: `load_candles(cls, exchange: str = "kucoin", symbol: str = "BTC/USD", timeframe: str = "5m", limit: int = 500, days: int | None = None, use_cache: bool = True) -> pd.DataFrame`
- **Called by**: **Nearly every API endpoint** needing candles (backtest, optimize, WFO, MC, validation, tear-sheet, batch, paper engine, portfolio)
- **Input params source**: Request fields (`exchange`, `symbol`, `timeframe`, `limit`, `days`)
- **Return type**: `pd.DataFrame` (OHLCV + timestamp)
- **Side effects**: 
  - May trigger `fetch_kucoin_historical` / `fetch_gemini_historical` / `generate_synthetic_candles`
  - May trigger `resample_and_upsert` for donor timeframes
  - DB writes via `db.save_candles`
- **Error handling**: Falls back to synthetic on fetch failure; returns empty DataFrame on total failure

#### `DataLoader.fetch_kucoin_historical` (classmethod)
- **Signature**: `fetch_kucoin_historical(cls, symbol: str = "BTC/USDT", timeframe: str = "5m", days: int | None = None) -> pd.DataFrame`
- **Called by**: `load_candles` (kucoin), `fetch_and_update_candles`
- **Input params source**: Same as `load_candles`
- **Return type**: `pd.DataFrame`
- **Side effects**: DB `save_candles`, `resample_and_upsert`
- **Error handling**: Falls back to synthetic on empty result

#### `DataLoader.fetch_gemini_historical` (classmethod)
- **Signature**: `fetch_gemini_historical(cls, symbol: str = "BTC/USD", timeframe: str = "5m", days: int = 180) -> pd.DataFrame`
- **Called by**: `load_candles` (gemini), `fetch_and_update_candles`
- **Input params source**: Same as `load_candles`
- **Return type**: `pd.DataFrame`
- **Side effects**: DB `save_candles`, `resample_and_upsert` (for unsupported TFs)
- **Error handling**: Falls back to synthetic on empty result

#### `DataLoader.fetch_and_update_candles` (classmethod)
- **Signature**: `fetch_and_update_candles(cls, exchange: str = "kucoin", symbol: str = "BTC/USD", timeframe: str = "5m", limit: int = 200) -> pd.DataFrame`
- **Called by**: 
  - `PaperTradingEngine.evaluate_symbol` (line 163) when `fetch_live=True`
  - `PaperTradingEngine.step_all_active_profiles` (line 811)
  - `PaperTradingEngine.reconcile_positions_on_startup` (line 921)
- **Input params source**: Exchange, symbol, timeframe, limit=200
- **Return type**: `pd.DataFrame` (latest candles from DB after upsert)
- **Side effects**: Provider fetch → DB save → resample → DB load
- **Error handling**: Catches all exceptions, falls back to DB cache; returns cached data on failure

---

### 8. Walk-Forward Optimization (`src/wfo.py`)

#### `WalkForwardEngine.run`
- **Signature**: `run(self, df: pd.DataFrame) -> dict[str, Any]`
- **Called by**: 
  - `POST /api/walk_forward` (line 1030)
  - `POST /api/strategy/validate` → `StrategyValidator.validate` (line 286)
- **Input params source**: `WFOConfig` from request, `OptunaConfig`, `base_params`, `n_trials_total`, `bars_per_day`
- **Return type**: `dict` with `window_count`, `walk_forward_efficiency_pct`, `avg_in_sample_return_pct`, `avg_out_of_sample_return_pct`, `oos_total_return_pct`, `param_stability_std`, `window_details`, `combined_oos_trade_count`, `auto_scaled_params`, `effective_num_windows`, `effective_trials_per_window`, `window_regimes`, `regime_change_count`, `regime_change_ratio`, `trigger_reoptimize`
- **Side effects**: 
  - Calls `OptunaOptimizer.optimize` per window (IS)
  - Calls `BacktestEngine.run` per window (OOS with warmup)
- **Error handling**: Raises `ValueError` for insufficient data (< 100 bars) or invalid windows; API → `HTTPException(500)`

#### `WalkForwardEngine.generate_windows`
- **Signature**: `generate_windows(self, df: pd.DataFrame) -> list[dict[str, Any]]`
- **Called by**: `run()` internal
- **Return type**: `list[dict]` with `df_is`, `df_oos`, `df_window`, `warmup_bars`, date ranges
- **Side effects**: None (data slicing)

---

### 9. Monte Carlo (`src/monte_carlo.py`)

#### `MonteCarloSimulator.run`
- **Signature**: `run(self) -> dict[str, Any]`
- **Called by**: 
  - `POST /api/monte_carlo` (line 1064)
  - `POST /api/tear-sheet/*` (lines 209, 315, 412)
  - `POST /api/strategy/validate` → `StrategyValidator.validate` (line 396)
- **Input params source**: `MonteCarloConfig` from request, `initial_capital`, `trades` from backtest
- **Return type**: `dict` with `num_simulations`, `trade_count`, `sample_with_replacement`, `risk_of_ruin_pct`, `final_equity_percentiles`, `net_profit_percentiles`, `max_drawdown_percentiles`, `sharpe_ratio_percentiles`, `equity_curves_percentiles`, `drawdown_histogram`
- **Side effects**: None (pure simulation)
- **Error handling**: Returns minimal dict for insufficient trades (< 2); exceptions propagate

#### `MonteCarloSimulator._run_trade_based_simulation` / `_run_relative_return_path` (private)
- **Called by**: `run()` based on input type (trades vs return_series/equity_curve)

---

### 10. Risk Manager (`src/risk_manager.py`)

#### `RiskManager.calculate_position_size`
- **Signature**: `calculate_position_size(self, equity: float, entry_price: float, stop_loss_price: float, regime: str = "trending") -> tuple[float, float, float]`
- **Returns**: `(units, position_value_usd, risk_amount_usd)`
- **Called by**: 
  - `PaperTradingEngine.evaluate_symbol` (line 569)
  - `BacktestEngine.run` (line 331) via `self.risk_manager`
  - `POST /api/portfolio/size_position` → `CrossAssetRiskBudgeter.calculate_position_size` (which uses this indirectly)
- **Input params source**: Engine-computed equity, entry_price, stop_loss; regime detection
- **Side effects**: None (pure computation)
- **Error handling**: Returns (0,0,0) for invalid inputs

#### `RiskManager.calculate_stop_and_target`
- **Signature**: `calculate_stop_and_target(self, entry_price: float, side: int, vwap: float, atr: float) -> tuple[float, float]`
- **Returns**: `(stop_loss_price, take_profit_price)`
- **Called by**: 
  - `PaperTradingEngine.evaluate_symbol` (lines 562, 323)
  - `BacktestEngine.run` (lines 322, 323)
  - `OptunaOptimizer.optimize` objective (line 322)
- **Input params source**: Strategy params (stop_loss_type, vwap_stop_offset_pct, atr_multiplier, risk_reward_ratio)
- **Side effects**: None (pure computation)

#### `RiskManager.update_trailing_stop`
- **Signature**: `update_trailing_stop(self, side: int, current_price: float, current_stop: float, atr: float, regime: str = "trending") -> float`
- **Returns**: Updated stop price
- **Called by**: 
  - `PaperTradingEngine.evaluate_symbol` (lines 473, 499)
  - `BacktestEngine.run` (line 450)
  - `PaperTradingEngine.reconcile_positions_on_startup` (not directly — uses params)
- **Input params source**: Current market price, existing stop, ATR, regime
- **Side effects**: None (pure computation)

---

### 11. Portfolio Aggregator (`src/portfolio/aggregator.py`)

#### `PortfolioAggregator.get_unified_snapshot`
- **Signature**: `get_unified_snapshot(self, include_synthetic: bool = False) -> UnifiedPortfolioSnapshot`
- **Called by**: 
  - `GET /api/portfolio/snapshot` (line 2296)
  - `GET /api/portfolio/venues` (line 2314)
  - `GET /api/portfolio/positions` (line 2334)
  - `POST /api/portfolio/refresh` (line 2357)
  - `GET /api/portfolio/history` (line 143)
  - `POST /api/portfolio/size_position` (line 2456)
  - `POST /api/portfolio/rebalance/plan` (line 2531)
  - `POST /api/portfolio/rebalance/execute` (line 2555)
  - `POST /api/portfolio/var` (line 2431)
- **Input params source**: Query `include_synthetic`
- **Return type**: `UnifiedPortfolioSnapshot` (dataclass with venues, positions, totals)
- **Side effects**: Calls each connector's `fetch_balances()` (external API calls)
- **Error handling**: Falls back to synthetic if no live connections; exceptions → `HTTPException(500)`

#### `PortfolioAggregator.get_historical_nav`
- **Signature**: `get_historical_nav(self, days: int = 30, include_synthetic: bool = True) -> list[dict[str, Any]]`
- **Called by**: `GET /api/portfolio/history` (line 2376)
- **Input params source**: Query `days`, `include_synthetic`
- **Return type**: `list[dict]` with daily NAV, crypto/stock/cash values
- **Side effects**: Calls `DataLoader.load_candles` for each position symbol (1d timeframe)
- **Error handling**: Logs fetch failures, uses current price as fallback

---

### 12. Portfolio Risk Engine (`src/portfolio/risk_engine.py`)

#### `PortfolioRiskEngine.compute_portfolio_var`
- **Signature**: `compute_portfolio_var(snapshot: UnifiedPortfolioSnapshot, returns_df: pd.DataFrame | None = None, confidence_level: float = 0.95, horizon_days: int = 1) -> dict[str, Any]`
- **Called by**: 
  - `GET /api/portfolio/var` (line 2434)
  - `POST /api/portfolio/size_position` → `CrossAssetRiskBudgeter.calculate_position_size` (line 52)
- **Input params source**: Snapshot from aggregator, query params `confidence_level`, `horizon_days`
- **Return type**: `dict` with `param_var_usd/pct`, `hist_var_usd/pct`, `cvar_usd/pct`, `component_var`, `circuit_breaker_status`, `risk_scale_factor`, `unrealized_drawdown_pct`
- **Side effects**: Generates synthetic returns if none provided
- **Error handling**: Returns zeros for empty portfolio

---

### 13. Portfolio Risk Budget (`src/portfolio/risk_budget.py`)

#### `CrossAssetRiskBudgeter.calculate_position_size`
- **Signature**: `calculate_position_size(symbol: str, entry_price: float, stop_loss_price: float, snapshot: UnifiedPortfolioSnapshot, risk_per_trade_pct: float = 1.0, returns_df: pd.DataFrame | None = None, allow_fractional: bool = True) -> dict[str, Any]`
- **Called by**: `POST /api/portfolio/size_position` (line 2459)
- **Input params source**: Request body `PositionSizingRequest`, snapshot from aggregator
- **Return type**: `dict` with `units`, `position_value_usd`, `effective_risk_usd`, `effective_risk_pct`, `correlation_penalty`, `circuit_breaker_scale`, `circuit_breaker_status`, `avg_correlation_with_portfolio`
- **Side effects**: Calls `PortfolioRiskEngine.compute_portfolio_var` for circuit breaker
- **Error handling**: Returns zero sizing with reason for invalid inputs

---

### 14. Portfolio Risk Budget Engine (`src/portfolio/portfolio_risk_budget.py`)

#### `PortfolioRiskBudgetEngine.compute_risk_budget`
- **Signature**: `compute_risk_budget(symbols: list[str], asset_metrics: dict[str, dict[str, float]] | None = None, returns_df: pd.DataFrame | None = None, config: RiskBudgetConfig | None = None) -> RiskBudgetResult`
- **Called by**: 
  - `POST /api/batch_results/export_to_portfolio` (line 1729) when `weighting_method == "risk_budget"`
  - `POST /api/portfolio/risk_budget` (line 2622)
- **Input params source**: Request `PortfolioRiskBudgetRequest` (symbols, asset_metrics, config params)
- **Return type**: `RiskBudgetResult` (dataclass with `weights`, `method`, `risk_contributions`, `portfolio_volatility`, `diversification_ratio`, `max_drawdown_constraints`, `monte_carlo`)
- **Side effects**: 
  - Generates synthetic returns if needed
  - Runs `compute_portfolio_monte_carlo` (500 sims default)
- **Error handling**: Returns empty result for no symbols; exceptions propagate

---

### 15. Portfolio Rebalancer (`src/portfolio/rebalancer.py`)

#### `PortfolioRebalancingEngine.create_rebalance_plan`
- **Signature**: `create_rebalance_plan(target_weights: dict[str, float], snapshot: UnifiedPortfolioSnapshot | None = None, drift_threshold_pct: float = 2.0, min_trade_usd: float = 25.0) -> RebalancePlan`
- **Called by**: 
  - `POST /api/portfolio/rebalance/plan` (line 2534)
  - `POST /api/portfolio/rebalance/execute` (line 2558) — creates plan then executes
- **Input params source**: Request `RebalancePlanRequest` / `RebalanceExecuteRequest`, snapshot from aggregator
- **Return type**: `RebalancePlan` (dataclass with `items`, `total_turnover_usd`, `estimated_fees_usd`, `requires_rebalancing`)
- **Side effects**: None (read-only planning)
- **Error handling**: None (uses price estimates)

#### `PortfolioRebalancingEngine.execute_plan`
- **Signature**: `execute_plan(plan: RebalancePlan, dry_run: bool = True) -> list[VenueOrder]`
- **Called by**: `POST /api/portfolio/rebalance/execute` (line 2564)
- **Input params source**: Plan from `create_rebalance_plan`, `dry_run` from request
- **Return type**: `list[VenueOrder]`
- **Side effects**: Calls `order_router.route_order` for each item (SELLs first, then BUYs)
- **Error handling**: Individual order failures captured in `VenueOrder.status`; continues

---

### 16. Portfolio Order Router (`src/portfolio/order_router.py`)

#### `order_router.route_order`
- **Signature**: `route_order(symbol: str, side: int, quantity: float, venue: str | None = "auto", order_type: str = "market", limit_price: float | None = None, dry_run: bool = False) -> VenueOrder`
- **Called by**: 
  - `POST /api/portfolio/route_order` (line 2507)
  - `PortfolioRebalancingEngine.execute_plan` (line 187)
- **Input params source**: Request `RouteOrderRequest` or rebalance plan item
- **Return type**: `VenueOrder` (dataclass with `status`, `filled_qty`, `avg_price`, `venue`, `order_id`)
- **Side effects**: 
  - Routes to appropriate connector (Alpaca, Gemini, KuCoin, Paper, etc.)
  - Live API calls when `dry_run=False`
- **Error handling**: Returns `VenueOrder` with `status="rejected"` and error message; no exceptions

---

### 17. Validator (`src/validator.py`)

#### `StrategyValidator.validate`
- **Signature**: `validate(self, df_candles: pd.DataFrame, params: StrategyParams, wfo_config: WFOConfig | None = None, mc_config: MonteCarloConfig | None = None, n_trials: int = 500, target_metric: str = "sharpe_ratio", best_optuna_value: float | None = None, enable_multi_objective: bool = False, multi_objective_metrics: list[str] | None = None) -> ValidationReport`
- **Called by**: 
  - `POST /api/strategy/validate` (line 1118)
  - `POST /api/tear-sheet/*` (lines 183, 289, 386) when no validation_report provided
- **Input params source**: Request `ValidateStrategyRequest` fields, candles from `DataLoader`
- **Return type**: `ValidationReport` (dataclass with `status` (ROBUST/CAUTION/FAIL), `overall_score`, `gates_passed`, `gate_results`, `summary`, `reasons`, `recommended_action`)
- **Side effects**: 
  - Runs Gate 1: `BacktestEngine.run`
  - Runs Gate 2: `WalkForwardEngine.run` (which runs Optuna per window)
  - Runs Gate 3: `MonteCarloSimulator.run`
- **Error handling**: 
  - Gate 1 failure → immediate FAIL return
  - Gate 2/3 exceptions → captured as CAUTION/FAIL in gate_results, continues
  - Overall exceptions propagate to API → `HTTPException(500)`

---

### 18. Strategy (`src/strategy.py`)

#### `EmaVwapStrategy.generate_signals`
- **Signature**: `generate_signals(self, df: pd.DataFrame, precomputed_indicators: pd.DataFrame | None = None) -> pd.DataFrame`
- **Called by**: 
  - `POST /api/backtest` (line 743)
  - `POST /api/tear-sheet/*` (via BacktestEngine)
  - `POST /api/optimize` (via BacktestEngine in Optuna objective)
  - `POST /api/walk_forward` (via BacktestEngine in WFO)
  - `POST /api/strategy/validate` (via BacktestEngine)
  - `PaperTradingEngine.evaluate_symbol` (line 195)
  - `PaperTradingEngine.step_all_active_profiles` (line 833)
  - `PaperTradingEngine.reconcile_positions_on_startup` (not directly — uses params)
- **Input params source**: `StrategyParams` from request/profile, candles from DataLoader
- **Return type**: `pd.DataFrame` with added columns: `signal` (1/-1/0), `raw_signal`, `slope_pass`, `volume_pass`, `regime`, EMAs, VWAP, ATR, vwap_slope, vol_sma
- **Side effects**: None (pure computation)
- **Error handling**: None (pandas operations)

#### `EmaVwapStrategy.evaluate_filters`
- **Signature**: `evaluate_filters(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]`
- **Called by**: `generate_signals` (line 157)
- **Returns**: `(slope_pass: pd.Series, volume_pass: pd.Series)`
- **Side effects**: None

---

## Cross-Cutting Verification

### Parameter Flow Verification
| Flow | Verified |
|------|----------|
| Request → `StrategyParams.from_dict()` → Business function | ✅ All endpoints use `build_strategy_params()` or `StrategyParams.from_dict()` |
| `StrategyParams` → `EmaVwapStrategy` → signals | ✅ `generate_signals` receives params in constructor |
| `StrategyParams` → `BacktestEngine` → metrics | ✅ `BacktestEngine` receives params in constructor |
| `StrategyParams` → `OptunaOptimizer` → trials | ✅ Base params + OptunaConfig bounds |
| `StrategyParams` → `RiskManager` → sizing/stops | ✅ RiskManager receives params in constructor |
| Paper profile params → Engine evaluation | ✅ `PaperProfileRegistry.get_strategy_params()` returns `StrategyParams` |

### Return Value Usage
| Function | Return Used By |
|----------|----------------|
| `BacktestEngine.run()` → `BacktestResult` | API response `metrics`, `chart_data`; Optuna objective; Validator gates; MC simulator |
| `OptunaOptimizer.optimize()` → dict | API response; Batch optimizer persistence; WFO per-window; Validator |
| `WalkForwardEngine.run()` → dict | API response; Validator Gate 2 |
| `MonteCarloSimulator.run()` → dict | API response; Validator Gate 3; Tear-sheet percentiles |
| `DataLoader.load_candles()` → DataFrame | All downstream consumers (backtest, optimize, WFO, MC, paper engine, portfolio) |
| `PaperTradingEngine.evaluate_symbol()` → dict | API response; `step_all_active_profiles` aggregation |
| `PortfolioAggregator.get_unified_snapshot()` → Snapshot | All portfolio endpoints; risk engine; risk budget; rebalancer |
| `StrategyValidator.validate()` → ValidationReport | API response; Tear-sheet validation_report |

### Error Propagation
| Layer | Pattern |
|-------|---------|
| Business logic | Raises `ValueError` for invalid inputs, `optuna.TrialPruned` for invalid trials, returns sentinel values (None, empty dict, zero result) for recoverable conditions |
| API endpoints | `try/except Exception` → `logger.error` → `raise HTTPException(status_code=500, detail="Internal server error")` |
| Specific known errors | `HTTPException(404)` for not found, `HTTPException(400)` for invalid input, `HTTPException(409)` for conflict |
| Background tasks | Errors captured in task result, state updated with error, logged |
| Batch optimizer | Per-combo errors logged, sweep continues; fatal errors update global state |

### Database Side Effects
| Module | Tables Written |
|--------|----------------|
| `PaperLedger` | `ledger_transactions`, `paper_positions`, `paper_trade_history`, `paper_accounts` |
| `PaperProfileRegistry` | `paper_profiles` |
| `BatchOptimizer` | `batch_results` |
| `DataLoader` | `candles` (multi-timeframe) |
| `PaperTradingEngine` | Via ledger + `paper_positions` (MTM updates) |
| Settings | `system_settings` |

---

## Issues Identified

1. **`OptunaOptimizer.optimize`** — `is_cancelled` and `progress_callback` parameters are detected via `inspect.signature` at runtime (lines 410-431). This is fragile; should be explicit in signature.

2. **`PaperTradingEngine.evaluate_symbol`** — Has 6 parameters with complex defaults; called from 3 different contexts (manual step, polling, reconcile) with different parameter combinations. Consider splitting into focused methods.

3. **`DataLoader.load_candles`** — Recursive call for donor timeframe (line 348) could hit recursion limit for deep chains; uses `print()` for logging instead of `logger`.

4. **`BatchOptimizer._run_single_optuna`** — Imports `validate_symbol_format`, `get_optimal_lookback_days`, `get_ema_bounds`, `compute_slope_bounds_from_df`, `logger` inside function (lines 283-239). Should be module-level imports.

5. **`StrategyValidator.validate`** — Gate 2 creates `WalkForwardEngine` with `OptunaConfig` that has `n_trials=n_trials` (UI budget), but WFO auto-scales `trials_per_window`. The `n_trials` passed to `OptunaConfig` should be `trials_per_window`, not total budget. Currently line 274 uses `n_trials` directly.

6. **`PaperTradingEngine.reconcile_positions_on_startup`** — Fetches candles with `limit=300` hardcoded (line 925); should use timeframe-appropriate lookback.

7. **Error handling inconsistency** — Some business functions return error dicts (e.g., `evaluate_symbol` returns `{"status": "error", "error": str(e)}`), others raise exceptions. API layer catches all as `HTTPException(500)` losing distinction.

---

## Recommendations

1. **Standardize error handling**: Business functions should raise typed exceptions (e.g., `InsufficientDataError`, `PositionNotFoundError`) that API layer maps to specific HTTP codes.

2. **Make `OptunaOptimizer.optimize` cancellation/progress explicit**: Add `is_cancelled: Callable[[], bool] | None = None` and `progress_callback: Callable[[float, str], None] | None = None` to signature.

3. **Extract `DataLoader` recursive donor fetch** into a separate method with loop instead of recursion.

4. **Fix `StrategyValidator` Gate 2 OptunaConfig**: Pass `trials_per_window` (auto-scaled) not total `n_trials`.

5. **Add type hints** to all return dicts using `TypedDict` for better IDE support and traceability.

6. **Move inline imports** in `BatchOptimizer._run_single_optuna` to module level.

7. **Add unit tests** for parameter flow: request → `StrategyParams` → each business function → response.