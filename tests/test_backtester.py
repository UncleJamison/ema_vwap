"""
Unit tests for Backtesting and Analytics Engine.
"""

import json
import math

import pandas as pd

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


def test_backtester_drawdown_includes_initial_capital_peak():
    df = pd.DataFrame(
        [
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "open": 100.0,
                "high": 100.5,
                "low": 99.5,
                "close": 100.0,
                "volume": 1000,
                "signal": 1,
                "vwap": 100.0,
                "atr": 1.0,
            },
            {
                "timestamp": "2024-01-01T00:05:00Z",
                "open": 100.0,
                "high": 100.1,
                "low": 99.96,
                "close": 100.0,
                "volume": 1000,
                "signal": 0,
                "vwap": 100.0,
                "atr": 1.0,
            },
            {
                "timestamp": "2024-01-01T00:10:00Z",
                "open": 90.0,
                "high": 92.0,
                "low": 89.0,
                "close": 89.5,
                "volume": 1000,
                "signal": 0,
                "vwap": 100.0,
                "atr": 1.0,
            },
        ]
    )
    params = StrategyParams(
        strategy_mode="crossover",
        initial_capital=100000.0,
        risk_per_trade_pct=0.15,
        stop_loss_type="atr",
        atr_multiplier=0.1,
        vwap_slope_min=0.0,
        volume_filter_enabled=False,
        asset_type="crypto",
    )

    result = BacktestEngine(params).run(df)

    assert len(result.equity_curve) == len(df)
    assert result.max_drawdown_pct >= 9.0
    assert result.final_equity <= params.initial_capital


def test_backtester_risk_metrics_use_initial_capital_and_annualized_return():
    df = pd.DataFrame(
        [
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
                "signal": 1,
                "vwap": 100.0,
                "atr": 10.0,
            },
            {
                "timestamp": "2024-07-01T00:00:00Z",
                "open": 110.0,
                "high": 111.0,
                "low": 109.0,
                "close": 110.0,
                "volume": 1000,
                "signal": 0,
                "vwap": 100.0,
                "atr": 10.0,
            },
        ]
    )
    params = StrategyParams(
        strategy_mode="crossover",
        initial_capital=100000.0,
        risk_per_trade_pct=1.0,
        stop_loss_type="atr",
        atr_multiplier=2.0,
        slippage_pct=0.0,
        vwap_slope_min=0.0,
        volume_filter_enabled=False,
        asset_type="crypto",
    )

    result = BacktestEngine(params).run(df)

    equity = pd.Series(
        [params.initial_capital] + [point["equity"] for point in result.equity_curve],
        dtype=float,
    )
    returns = equity.pct_change().dropna()
    periods_per_year = 365 * 24 * 60 * 60 / (182 * 24 * 60 * 60)
    expected_sharpe = round(
        float(returns.mean() / returns.std() * periods_per_year**0.5), 2
    )
    downside_deviation = float((returns.clip(upper=0).pow(2).mean()) ** 0.5)
    expected_sortino = round(
        float(returns.mean() / downside_deviation * periods_per_year**0.5), 2
    )
    drawdowns = (equity.cummax() - equity) / equity.cummax()
    expected_ulcer = round(float((drawdowns.pow(2).mean() ** 0.5) * 100), 2)
    elapsed_seconds = (
        pd.Timestamp(df["timestamp"].iloc[-1]) - pd.Timestamp(df["timestamp"].iloc[0])
    ).total_seconds()
    annualized_return_pct = (
        (result.final_equity / params.initial_capital)
        ** (365 * 24 * 60 * 60 / elapsed_seconds)
        - 1
    ) * 100
    expected_calmar = round(annualized_return_pct / (drawdowns.max() * 100), 2)
    stock_params = StrategyParams.from_dict({**params.to_dict(), "asset_type": "stock"})
    stock_result = BacktestEngine(stock_params).run(df)
    stock_equity = pd.Series(
        [stock_params.initial_capital]
        + [point["equity"] for point in stock_result.equity_curve],
        dtype=float,
    )
    stock_returns = stock_equity.pct_change().dropna()
    expected_stock_sharpe = round(
        float(
            stock_returns.mean()
            / stock_returns.std()
            * (252 * 24 * 60 * 60 / elapsed_seconds) ** 0.5
        ),
        2,
    )

    assert result.sharpe_ratio == expected_sharpe
    assert result.sortino_ratio == expected_sortino
    assert result.ulcer_index == expected_ulcer
    assert abs(result.calmar_ratio - expected_calmar) <= 0.02
    assert result.equity_curve[-1]["equity"] == result.final_equity
    assert stock_result.sharpe_ratio == expected_stock_sharpe


def test_calmar_is_finite_and_undefined_for_short_backtests():
    for high, low, close in [(105.0, 99.0, 104.0), (101.0, 99.0, 99.5)]:
        df = pd.DataFrame(
            [
                {
                    "timestamp": "2024-01-01T00:00:00Z",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1000,
                    "signal": 1,
                    "vwap": 100.0,
                    "atr": 1.0,
                },
                {
                    "timestamp": "2024-01-01T00:05:00Z",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 99.8,
                    "volume": 1000,
                    "signal": 0,
                    "vwap": 100.0,
                    "atr": 1.0,
                },
                {
                    "timestamp": "2024-01-01T00:10:00Z",
                    "open": 100.0,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": 1000,
                    "signal": 0,
                    "vwap": 100.0,
                    "atr": 1.0,
                },
            ]
        )
        params = StrategyParams(
            initial_capital=10000.0,
            stop_loss_type="atr",
            atr_multiplier=2.0,
            slippage_pct=0.0,
            taker_fee_pct=0.01,
        )

        result = BacktestEngine(params).run(df)

        assert result.total_trades == 1
        assert result.calmar_ratio == 0.0
        assert math.isfinite(result.calmar_ratio)
        json.dumps(result.to_dict(), allow_nan=False)


def test_backtester_empty_input_returns_complete_zero_metrics():
    columns = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "signal",
        "vwap",
        "atr",
    ]
    result = BacktestEngine(StrategyParams()).run(pd.DataFrame(columns=columns))

    assert result.final_equity == result.initial_capital
    assert result.total_return_pct == 0.0
    assert result.calmar_ratio == 0.0
    assert result.drawdown_penalized_sharpe == 0.0
    assert result.drawdown_penalized_sortino == 0.0
    assert result.ulcer_index == 0.0


def test_backtester_drawdown_handles_nonpositive_equity_peaks():
    df = pd.DataFrame(
        [
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
                "signal": 0,
                "vwap": 100.0,
                "atr": 1.0,
            }
        ]
    )
    result = BacktestEngine(StrategyParams(initial_capital=-100.0)).run(df)

    assert result.final_equity == -100.0
    assert result.max_drawdown_pct == 0.0
    assert result.ulcer_index == 0.0


def test_backtester_does_not_fill_signal_from_final_bar():
    df = pd.DataFrame(
        [
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
                "signal": 1,
                "vwap": 100.0,
                "atr": 1.0,
            }
        ]
    )
    params = StrategyParams(
        initial_capital=10000.0,
        stop_loss_type="atr",
        atr_multiplier=2.0,
        slippage_pct=0.0,
        taker_fee_pct=0.0,
    )

    result = BacktestEngine(params).run(df)

    assert result.total_trades == 0
    assert result.final_equity == params.initial_capital


def test_next_open_entry_uses_signal_bar_indicators():
    df = pd.DataFrame(
        [
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
                "signal": 1,
                "vwap": 100.0,
                "atr": 1.0,
            },
            {
                "timestamp": "2024-01-01T00:05:00Z",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
                "signal": 0,
                "vwap": 500.0,
                "atr": 50.0,
            },
        ]
    )
    params = StrategyParams(
        initial_capital=10000.0,
        stop_loss_type="atr",
        atr_multiplier=2.0,
        slippage_pct=0.0,
        taker_fee_pct=0.0,
    )

    result = BacktestEngine(params).run(df)

    assert result.total_trades == 1
    assert result.trades[0]["entry_price"] == 100.0
    assert result.trades[0]["stop_loss"] == 98.0
    assert result.trades[0]["take_profit"] == 104.0


def test_entry_bar_stop_precedes_take_profit_when_both_touch():
    df = pd.DataFrame(
        [
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
                "signal": 1,
                "vwap": 100.0,
                "atr": 1.0,
            },
            {
                "timestamp": "2024-01-01T00:05:00Z",
                "open": 100.0,
                "high": 105.0,
                "low": 95.0,
                "close": 100.0,
                "volume": 1000,
                "signal": 0,
                "vwap": 100.0,
                "atr": 1.0,
            },
        ]
    )
    params = StrategyParams(
        initial_capital=10000.0,
        stop_loss_type="atr",
        atr_multiplier=2.0,
        slippage_pct=0.0,
        taker_fee_pct=0.0,
    )

    result = BacktestEngine(params).run(df)

    assert result.total_trades == 1
    assert result.trades[0]["exit_reason"] == "stop_loss"
    assert result.trades[0]["exit_price"] == 98.0


def test_signal_reversal_closes_and_reopens_at_next_bar_open():
    df = pd.DataFrame(
        [
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
                "signal": 1,
                "vwap": 100.0,
                "atr": 1.0,
            },
            {
                "timestamp": "2024-01-01T00:05:00Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
                "signal": -1,
                "vwap": 100.0,
                "atr": 1.0,
            },
            {
                "timestamp": "2024-01-01T00:10:00Z",
                "open": 105.0,
                "high": 106.0,
                "low": 104.0,
                "close": 105.0,
                "volume": 1000,
                "signal": 0,
                "vwap": 100.0,
                "atr": 1.0,
            },
        ]
    )
    params = StrategyParams(
        initial_capital=10000.0,
        trade_direction="both",
        stop_loss_type="atr",
        atr_multiplier=3.0,
        slippage_pct=0.0,
        taker_fee_pct=0.0,
    )

    result = BacktestEngine(params).run(df)

    assert result.total_trades == 2
    assert result.trades[0]["exit_reason"] == "signal_reversal"
    assert result.trades[0]["exit_price"] == 105.0
    assert result.trades[1]["side"] == -1
    assert result.trades[1]["entry_price"] == 105.0


def test_backtester_end_of_data_stock_exit_applies_regulatory_fees():
    df = pd.DataFrame(
        [
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "open": 100.0,
                "high": 100.2,
                "low": 99.8,
                "close": 100.0,
                "volume": 1000,
                "signal": 1,
                "vwap": 100.0,
                "atr": 1.0,
            },
            {
                "timestamp": "2024-01-01T00:05:00Z",
                "open": 100.3,
                "high": 100.6,
                "low": 99.9,
                "close": 100.4,
                "volume": 1000,
                "signal": 0,
                "vwap": 100.0,
                "atr": 1.0,
            },
        ]
    )
    params = StrategyParams(
        strategy_mode="crossover",
        initial_capital=10000.0,
        risk_per_trade_pct=0.05,
        stop_loss_type="atr",
        atr_multiplier=0.5,
        vwap_slope_min=0.0,
        volume_filter_enabled=False,
        asset_type="stock",
    )

    result = BacktestEngine(params).run(df)

    assert result.total_trades == 1
    trade = result.trades[0]
    assert trade["exit_reason"] == "end_of_data"

    entry_fee = trade["units"] * trade["entry_price"] * (params.taker_fee_pct / 100.0)
    exit_price = trade["exit_price"]
    base_exit_fee = trade["units"] * exit_price * (params.taker_fee_pct / 100.0)
    sec_fee = (trade["units"] * exit_price) * (params.sec_fee_per_million / 1_000_000.0)
    finra_fee = min(8.30, trade["units"] * params.finra_taf_per_share)
    expected_total_fee = entry_fee + base_exit_fee + sec_fee + finra_fee

    assert abs(trade["fee"] - expected_total_fee) < 0.05
    raw_pnl = (exit_price - trade["entry_price"]) * trade["units"]
    expected_trade_pnl = raw_pnl - expected_total_fee
    assert abs(trade["pnl"] - expected_trade_pnl) < 0.05
    assert abs(result.net_profit - trade["pnl"]) < 0.05
    assert (
        abs(result.final_equity - (result.initial_capital + result.net_profit)) < 0.05
    )
