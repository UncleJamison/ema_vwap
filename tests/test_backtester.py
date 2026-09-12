"""
Unit tests for Backtesting and Analytics Engine.
"""

from src.backtester import BacktestEngine, BacktestResult
from src.config import StrategyParams
from src.data_loader import DataLoader


def test_backtest_engine_run():
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    params = StrategyParams(
        strategy_mode="crossover",
        initial_capital=10000.0,
        risk_per_trade_pct=1.0,
        vwap_slope_min=0.0,
        volume_filter_enabled=False,
    )

    engine = BacktestEngine(params)
    result = engine.run(df)

    assert isinstance(result, BacktestResult)
    assert result.initial_capital == 10000.0
    assert len(result.equity_curve) == len(df)
    assert result.total_trades == result.winning_trades + result.losing_trades
    assert result.win_rate >= 0.0 and result.win_rate <= 100.0
    assert result.max_drawdown_pct >= 0.0
    assert isinstance(result.monthly_breakdown, list)
    assert hasattr(result, "avg_win")
    assert hasattr(result, "max_consecutive_wins")
    assert hasattr(result, "sortino_ratio")
    assert hasattr(result, "calmar_ratio")
    assert hasattr(result, "drawdown_penalized_sharpe")
    assert isinstance(result.sortino_ratio, (int, float))
    assert isinstance(result.calmar_ratio, (int, float))
    assert isinstance(result.drawdown_penalized_sharpe, (int, float))


def test_micro_cap_bonk_precision():
    # Generate synthetic BONK candles trading around $0.0000185
    df_bonk = DataLoader.generate_synthetic_candles(
        num_bars=200, start_price=0.0000185, seed=42
    )
    params = StrategyParams(
        strategy_mode="crossover",
        initial_capital=10000.0,
        risk_per_trade_pct=1.0,
        vwap_slope_min=0.0,
        volume_filter_enabled=False,
    )
    engine = BacktestEngine(params)
    res = engine.run(df_bonk)

    assert res.total_trades > 0
    # Ensure entry prices and stop loss prices are NOT 0.0 or truncated to 0.0001
    for trade in res.trades:
        assert trade["entry_price"] > 0.0
        assert trade["entry_price"] < 0.001
        assert trade["stop_loss"] > 0.0
        # PnL % should be realistic (not 1500%!)
        assert abs(trade["pnl_pct"]) < 200.0
