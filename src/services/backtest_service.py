"""
Backtest Execution Service (ema_vwap-klm5).

Extracted business logic from API endpoints to a dedicated service layer.
Endpoints handle HTTP concerns; services handle business logic.
"""

from typing import Any

import pandas as pd

from src.backtester import BacktestEngine
from src.config import StrategyParams, get_optimal_lookback_days
from src.data_loader import DataLoader
from src.strategy import EmaVwapStrategy


def execute_backtest(
    exchange: str,
    symbol: str,
    timeframe: str,
    params: StrategyParams,
    limit: int | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """
    Execute a full backtest and return structured results.

    This is the core business logic extracted from the /api/backtest endpoint.
    """
    # "Auto (Optimal Lookback)" mode
    backtest_days = days
    backtest_limit = limit
    if backtest_days is None and limit == 500:
        # Auto mode: use optimal lookback days for the timeframe
        backtest_days = get_optimal_lookback_days(timeframe)
        backtest_limit = None  # let DataLoader compute from days

    # Load Candles from Database Cache or Exchange API
    df_candles = DataLoader.load_candles(
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        limit=backtest_limit,
        days=backtest_days,
    )

    # Compute indicators and strategy signals
    strategy = EmaVwapStrategy(params)
    df_signals = strategy.generate_signals(df_candles)

    # Run Backtest
    engine = BacktestEngine(params)
    result = engine.run(df_candles, df_signals=df_signals)

    # Prepare chart candles response
    chart_data = []
    for _, row in df_signals.iterrows():
        chart_data.append(
            {
                "timestamp": str(row["timestamp"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "vwap": float(row["vwap"]) if not pd.isna(row["vwap"]) else None,
                "ema_fast": (
                    float(row[f"ema_{params.fast_ema}"])
                    if f"ema_{params.fast_ema}" in row
                    and not pd.isna(row[f"ema_{params.fast_ema}"])
                    else None
                ),
                "ema_slow": (
                    float(row[f"ema_{params.slow_ema}"])
                    if f"ema_{params.slow_ema}" in row
                    and not pd.isna(row[f"ema_{params.slow_ema}"])
                    else None
                ),
                "ema_trend": (
                    float(row[f"ema_{params.trend_ema}"])
                    if f"ema_{params.trend_ema}" in row
                    and not pd.isna(row[f"ema_{params.trend_ema}"])
                    else None
                ),
                "signal": int(row["signal"]) if "signal" in row else 0,
            }
        )

    # Build equity curve
    equity_curve = [
        {"timestamp": ts, "equity": round(eq, 2)} for ts, eq in result.equity_curve
    ]

    # Build trade list
    trades = []
    for trade in result.trades:
        trades.append(
            {
                "entry_time": str(trade.entry_time),
                "exit_time": str(trade.exit_time),
                "side": trade.side,
                "entry_price": round(trade.entry_price, 6),
                "exit_price": round(trade.exit_price, 6),
                "pnl": round(trade.pnl, 2),
                "duration_bars": trade.duration_bars,
                "exit_reason": trade.exit_reason,
            }
        )

    return {
        "strategy_params": params.to_dict(),
        "metrics": result.to_dict(),
        "chart_data": chart_data,
        "equity_curve": equity_curve,
        "trades": trades,
    }
