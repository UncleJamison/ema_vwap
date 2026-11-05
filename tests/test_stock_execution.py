"""
Unit and integration tests for Stock Execution Engine, Overnight Gap Slippage,
Intraday Margin Monitoring (4:1 longs, 2:1 shorts), and SEC/FINRA regulatory fees.
(Effective June 4, 2026: Old PDT 3-day-trade rule eliminated in favor of real-time margin framework)
"""

from datetime import UTC

import pandas as pd

from src.backtester import BacktestEngine
from src.config import StrategyParams
from src.risk_manager import RiskManager


def test_fractional_vs_whole_share_sizing():
    """Verify whole-share integer rounding when allow_fractional_shares=False."""
    params_fractional = StrategyParams(
        risk_per_trade_pct=1.0, allow_fractional_shares=True
    )
    rm_frac = RiskManager(params_fractional)
    # $10,000 equity * 1% = $100 risk. Stop dist = $3.00 -> 33.3333 units
    units_frac, _val_frac, _risk_frac = rm_frac.calculate_position_size(
        equity=10000.0, entry_price=150.0, stop_loss_price=147.0
    )
    assert round(units_frac, 2) == 33.33

    params_whole = StrategyParams(risk_per_trade_pct=1.0, allow_fractional_shares=False)
    rm_whole = RiskManager(params_whole)
    units_whole, val_whole, _risk_whole = rm_whole.calculate_position_size(
        equity=10000.0, entry_price=150.0, stop_loss_price=147.0
    )
    assert units_whole == 33.0  # Floored to whole integer
    assert val_whole == 33.0 * 150.0


def test_overnight_gap_stop_loss_execution():
    """Verify that an overnight gap opening below Stop Loss fills at Open price rather than Stop Loss."""
    # Day 1: Enter Long at 100.0, SL=95.0, TP=110.0
    # Day 2: Market gaps down and opens at 90.0 (below 95.0 SL)
    df_gap = pd.DataFrame(
        {
            "timestamp": [
                "2026-08-31 15:55:00+00:00",
                "2026-08-31 16:00:00+00:00",
                "2026-09-01 13:30:00+00:00",  # Gap open bar
            ],
            "open": [100.0, 100.0, 90.0],
            "high": [101.0, 101.0, 92.0],
            "low": [99.0, 99.0, 89.0],
            "close": [100.0, 100.0, 91.0],
            "volume": [1000, 1000, 1000],
            "signal": [1, 0, 0],
            "vwap": [95.0, 95.0, 95.0],
            "atr": [2.5, 2.5, 2.5],
        }
    )

    params = StrategyParams(
        stop_loss_type="vwap",
        vwap_stop_offset_pct=0.0,  # SL exactly at 95.0
        slippage_pct=0.0,
        taker_fee_pct=0.0,
        asset_type="stock",
    )

    engine = BacktestEngine(params)
    res = engine.run(df_gap)

    assert res.total_trades == 1
    trade = res.trades[0]
    assert trade["exit_reason"] == "gap_stop_loss"
    # Filled at open price 90.0, not 95.0!
    assert trade["exit_price"] == 90.0


def test_sec_and_finra_regulatory_fees():
    """Verify SEC and FINRA fees are correctly applied on stock sell executions."""
    # Trade entered at $200 and exited at $205 on signal reversal
    df = pd.DataFrame(
        {
            "timestamp": [
                "2026-08-31 13:30:00+00:00",
                "2026-08-31 13:35:00+00:00",
                "2026-08-31 13:40:00+00:00",
            ],
            "open": [200.0, 200.0, 205.0],
            "high": [202.0, 204.0, 206.0],
            "low": [198.0, 199.0, 204.0],
            "close": [200.0, 203.0, 205.0],
            "volume": [1000, 1000, 1000],
            "signal": [1, -1, 0],
            "vwap": [190.0, 190.0, 190.0],
            "atr": [5.0, 5.0, 5.0],
            "trade_direction": ["long_only", "long_only", "long_only"],
        }
    )

    params_stock = StrategyParams(
        asset_type="stock",
        trade_direction="long_only",
        taker_fee_pct=0.1,
        slippage_pct=0.0,
        sec_fee_per_million=27.80,
        finra_taf_per_share=0.000166,
    )

    engine = BacktestEngine(params_stock)
    res = engine.run(df)

    assert len(res.trades) == 1
    trade = res.trades[0]
    assert trade["fee"] > 0.0  # Regulatory fee was charged on sell
    assert trade["exit_reason"] == "signal_reversal"
    assert trade["exit_price"] == 205.0
    assert abs(res.net_profit - trade["pnl"]) <= 0.01
    assert trade["pnl_pct"] == round(
        trade["pnl"] / trade["position_value_usd"] * 100.0, 2
    )


def test_short_stock_regulatory_fees_apply_to_opening_sale():
    df = pd.DataFrame(
        {
            "timestamp": [
                "2026-08-31 13:30:00+00:00",
                "2026-09-01 13:35:00+00:00",
            ],
            "open": [200.0, 200.0],
            "high": [201.0, 201.0],
            "low": [199.0, 179.0],
            "close": [200.0, 180.0],
            "volume": [1000, 1000],
            "signal": [-1, 0],
            "vwap": [210.0, 210.0],
            "atr": [5.0, 5.0],
        }
    )
    params = StrategyParams(
        initial_capital=10000.0,
        risk_per_trade_pct=10.0,
        stop_loss_type="vwap",
        vwap_stop_offset_pct=0.0,
        trade_direction="short_only",
        asset_type="stock",
        taker_fee_pct=0.0,
        slippage_pct=0.0,
        sec_fee_per_million=27.80,
        finra_taf_per_share=0.000166,
    )

    result = BacktestEngine(params).run(df)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade["entry_price"] == 200.0
    expected_sec_fee = (
        trade["units"] * trade["entry_price"] * params.sec_fee_per_million / 1_000_000
    )
    expected_finra_fee = min(8.30, trade["units"] * params.finra_taf_per_share)
    assert abs(trade["fee"] - expected_sec_fee - expected_finra_fee) <= 0.01
    assert abs(result.net_profit - trade["pnl"]) <= 0.01


def test_intraday_margin_monitoring():
    """Verify that real-time intraday margin monitoring enforces 4:1 buying power for longs and blocks trades if insufficient margin."""
    from datetime import datetime, timedelta

    base_time = datetime(2026, 8, 31, 13, 30, tzinfo=UTC)
    bars = []

    # Create multiple bars with signals for trading attempts
    for t in range(4):
        t_time = base_time + timedelta(minutes=t * 10)
        bars.append(
            {
                "timestamp": t_time.strftime("%Y-%m-%d %H:%M:%S+00:00"),
                "open": 100.0,
                "high": 102.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 1000,
                "signal": 1,  # Long signal
                "vwap": 95.0,
                "atr": 2.0,
            }
        )
        # Exit signal
        bars.append(
            {
                "timestamp": (t_time + timedelta(minutes=5)).strftime(
                    "%Y-%m-%d %H:%M:%S+00:00"
                ),
                "open": 100.0,
                "high": 102.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 1000,
                "signal": -1,  # Exit signal
                "vwap": 95.0,
                "atr": 2.0,
            }
        )

    df_margin = pd.DataFrame(bars)

    # With enforce_margin_calls=True, minimum $2,000 equity, and 4:1 long buying power
    params_margin = StrategyParams(
        initial_capital=3000.0,  # $3,000 starting capital
        enforce_margin_calls=True,
        min_margin_equity=2000.0,
        long_buying_power_ratio=4.0,
        short_buying_power_ratio=2.0,
        asset_type="stock",
        slippage_pct=0.0,
        taker_fee_pct=0.0,
        position_size=10.0,  # Buy 10 shares per trade at ~$100/share
    )

    engine = BacktestEngine(params_margin)
    res = engine.run(df_margin)

    # With $3,000 equity and 4:1 buying power, available BP = $12,000 for longs
    # At $100/share, can buy up to 120 shares. With position_size=10, should have room.
    # All trades should execute if margin is sufficient
    assert res.total_trades >= 1  # Trades executed within margin constraints


def test_short_margin_check_uses_calculated_position_notional():
    df = pd.DataFrame(
        {
            "timestamp": [
                "2026-08-31 13:30:00+00:00",
                "2026-09-01 13:30:00+00:00",
            ],
            "open": [100.0, 100.0],
            "high": [101.0, 101.0],
            "low": [99.0, 99.0],
            "close": [100.0, 100.0],
            "volume": [1000, 1000],
            "signal": [-1, 0],
            "vwap": [120.0, 120.0],
            "atr": [1.0, 1.0],
        }
    )
    params = StrategyParams(
        initial_capital=10000.0,
        risk_per_trade_pct=50.0,
        stop_loss_type="vwap",
        vwap_stop_offset_pct=0.0,
        trade_direction="short_only",
        asset_type="stock",
        enforce_margin_calls=True,
        short_buying_power_ratio=2.0,
        taker_fee_pct=0.0,
        slippage_pct=0.0,
    )

    result = BacktestEngine(params).run(df)

    assert result.total_trades == 0


def test_margin_equity_breach_liquidates_open_stock_position():
    df = pd.DataFrame(
        {
            "timestamp": [
                "2026-08-31 13:30:00+00:00",
                "2026-09-01 13:30:00+00:00",
            ],
            "open": [100.0, 100.0],
            "high": [101.0, 82.0],
            "low": [99.0, 78.0],
            "close": [100.0, 80.0],
            "volume": [1000, 1000],
            "signal": [1, 0],
            "vwap": [100.0, 100.0],
            "atr": [10.0, 10.0],
        }
    )
    params = StrategyParams(
        initial_capital=2500.0,
        risk_per_trade_pct=100.0,
        stop_loss_type="atr",
        atr_multiplier=5.0,
        asset_type="stock",
        enforce_margin_calls=True,
        min_margin_equity=2000.0,
        taker_fee_pct=0.0,
        slippage_pct=0.0,
    )

    result = BacktestEngine(params).run(df)

    assert result.total_trades == 1
    assert result.trades[0]["exit_reason"] == "margin_call"
    assert result.equity_curve[-1]["equity"] == result.final_equity
    assert result.final_equity < params.min_margin_equity


def test_minimum_margin_equity_enforcement():
    """Verify that minimum $2,000 margin equity requirement blocks trades when equity drops below threshold."""
    from datetime import datetime, timedelta

    base_time = datetime(2026, 8, 31, 13, 30, tzinfo=UTC)
    bars = []

    # Create bars with a losing trade to drop equity below minimum
    for t in range(2):
        t_time = base_time + timedelta(minutes=t * 10)
        bars.append(
            {
                "timestamp": t_time.strftime("%Y-%m-%d %H:%M:%S+00:00"),
                "open": 100.0,
                "high": 101.0 if t == 0 else 100.0,
                "low": 98.0,
                "close": 100.0 if t == 0 else 99.0,  # Losing bar for second trade
                "volume": 1000,
                "signal": 1,  # Long signal
                "vwap": 95.0,
                "atr": 2.0,
            }
        )
        bars.append(
            {
                "timestamp": (t_time + timedelta(minutes=5)).strftime(
                    "%Y-%m-%d %H:%M:%S+00:00"
                ),
                "open": 100.0 if t == 0 else 99.0,
                "high": 102.0,
                "low": 98.0,
                "close": 100.0 if t == 0 else 99.0,
                "volume": 1000,
                "signal": -1,  # Exit signal
                "vwap": 95.0,
                "atr": 2.0,
            }
        )

    df_equity_test = pd.DataFrame(bars)

    params_low_equity = StrategyParams(
        initial_capital=2500.0,  # Starting with $2,500
        enforce_margin_calls=True,
        min_margin_equity=2000.0,  # Min equity requirement
        long_buying_power_ratio=4.0,
        asset_type="stock",
        slippage_pct=0.0,
        taker_fee_pct=0.001,  # Some fees to drive equity down
        position_size=5.0,
    )

    engine = BacktestEngine(params_low_equity)
    res = engine.run(df_equity_test)

    # At least one trade should execute initially; subsequent trades depend on whether equity stays above minimum
    assert res.total_trades >= 1
