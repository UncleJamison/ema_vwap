# Backtest Visualization Design Spec (ema_vwap-77t)

## Data Sources (from BacktestEngine.run)
- `equity_curve`: list of `{timestamp, equity}` dicts (line 374)
- `trades`: list of `Trade` dicts with `entry_time`, `entry_price`, `exit_time`, `exit_price`, `exit_reason`, `pnl`, `side`, `units`, `duration_bars` (line 384)
- `BacktestResult`: aggregate metrics (Sharpe, drawdown, profit factor, etc.)

## Visualization Types
1. Trade annotations on equity curve — entry/exit markers with P&L labels
2. Parameter sensitivity heatmaps — 2D (grid) + 3D (surface) over StrategyParams
3. P&L distribution violin plots — per-trade P&L by exit reason
4. Rolling Sharpe / drawdown windows — rolling window metrics over time
5. Monte Carlo percentile bands — p5/p50/p95 equity curves (from `monte_carlo.py`)

## Export Formats
PNG / SVG / PDF. Use matplotlib (no existing dep — add to requirements).

## Implementation Pattern
- New module: `src/visualization/backtest_plots.py` with functions returning `bytes` (PNG/SVG) or `dict` (JSON)
- New endpoint: `/api/backtest/visualize` in `src/app.py` returning JSON with plot data + PNG bytes
- Frontend: new tab in `src/web/ws_dashboard.js` + `src/web/app.js` rendering the plots

## Integration Points (verify via grep)
1. `src/app.py` — route decorator + calls backtest engine
2. `src/ws_server.py` — no new message type needed (push on-demand via REST)
3. `src/web/app.js` — fetch function + render function
4. `src/web/index.html` — tab placement (after Tear Sheet, consistent with alerting pattern)

## Pitfalls
- `equity_curve` timestamps are strings (not datetime) — convert before plotting
- `trades` list may be empty — guard against empty violin/heatmap inputs
- Monte Carlo percentile bands require `monte_carlo.py` output; don't recompute
- Use `matplotlib.use('Agg')` headless — never `plt.show()` in server context