"""
Backtesting and Analytics Engine.
Simulates trading setups, order execution with fees and slippage, stop loss/take profit triggers, and computes comprehensive quantitative performance metrics.
"""

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.config import StrategyParams
from src.risk_manager import RiskManager
from src.strategy import EmaVwapStrategy


def dynamic_round_price(price: float) -> float:
    """Preserve exact float precision up to 10 decimal places for micro-cap tokens (BONK, SHIB, PEPE)."""
    if price == 0:
        return 0.0
    return round(float(price), 10)


def dynamic_round_units(units: float) -> float:
    """Dynamically round units based on size."""
    if units >= 1.0:
        return round(units, 4)
    return round(units, 8)


@dataclass
class Trade:
    trade_id: int
    side: int  # 1 = Long, -1 = Short
    entry_time: str
    entry_price: float
    units: float
    position_value_usd: float
    stop_loss: float
    take_profit: float
    exit_time: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    fee: float = 0.0
    duration_bars: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestResult:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    initial_capital: float
    final_equity: float
    net_profit: float
    total_return_pct: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    drawdown_penalized_sharpe: float
    drawdown_penalized_sortino: float
    ulcer_index: float

    # Advanced Metrics
    avg_trade_pnl: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    payoff_ratio: float
    expectancy: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    avg_duration_bars: float
    monthly_breakdown: list[dict[str, Any]]

    equity_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BacktestEngine:
    """Executes event-driven backtesting loop over DataFrame candles."""

    def __init__(self, params: StrategyParams):
        self.params = params
        self.strategy = EmaVwapStrategy(params)
        self.risk_manager = RiskManager(params)

    def run(self, df: pd.DataFrame, warmup_bars: int = 0) -> BacktestResult:
        """Run backtest on provided OHLCV candles DataFrame with optional warmup bars."""
        if "signal" in df.columns and "vwap" in df.columns and "atr" in df.columns:
            df_signals = df.copy()
        else:
            df_signals = self.strategy.generate_signals(df)
        if warmup_bars > 0 and len(df_signals) > warmup_bars:
            df_signals = df_signals.iloc[warmup_bars:].reset_index(drop=True)

        capital = self.params.initial_capital
        equity = capital
        equity_curve = []
        trades: list[Trade] = []
        active_trade: Trade | None = None
        trade_counter = 0
        entry_bar_index = 0

        taker_fee = self.params.taker_fee_pct / 100.0
        slippage = self.params.slippage_pct / 100.0

        n_bars = len(df_signals)
        if n_bars == 0:
            return BacktestResult(
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                initial_capital=capital,
                final_equity=capital,
                net_profit=0.0,
                total_return_pct=0.0,
                profit_factor=1.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                avg_trade_pnl=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                largest_win=0.0,
                largest_loss=0.0,
                payoff_ratio=0.0,
                expectancy=0.0,
                max_consecutive_wins=0,
                max_consecutive_losses=0,
                avg_duration_bars=0.0,
                monthly_breakdown=[],
                equity_curve=[],
                trades=[],
            )

        # Pre-extract fast NumPy arrays for orders of magnitude faster execution
        timestamps = df_signals["timestamp"].astype(str).to_numpy()
        opens = df_signals["open"].to_numpy(dtype=float)
        highs = df_signals["high"].to_numpy(dtype=float)
        lows = df_signals["low"].to_numpy(dtype=float)
        closes = df_signals["close"].to_numpy(dtype=float)
        signals = df_signals["signal"].to_numpy(dtype=int)
        vwaps = df_signals["vwap"].to_numpy(dtype=float)
        atrs = df_signals["atr"].to_numpy(dtype=float)

        is_stock = getattr(self.params, "asset_type", "crypto") == "stock"
        enforce_margin_calls = getattr(self.params, "enforce_margin_calls", False)
        min_margin_equity = getattr(self.params, "min_margin_equity", 2000.0)
        long_bp_ratio = getattr(self.params, "long_buying_power_ratio", 4.0)
        short_bp_ratio = getattr(self.params, "short_buying_power_ratio", 2.0)

        for i in range(n_bars):
            timestamp_str = timestamps[i]
            open_p = opens[i]
            high = highs[i]
            low = lows[i]
            close = closes[i]
            signal = signals[i]
            vwap = vwaps[i]
            atr = atrs[i]

            # Process open trade
            if active_trade is not None:
                side = active_trade.side
                units = active_trade.units
                stop_loss = active_trade.stop_loss
                take_profit = active_trade.take_profit
                entry_price = active_trade.entry_price

                exited = False
                exit_price = close
                exit_reason = ""

                # Check Overnight Gaps and Stop Loss / Take Profit triggers
                if side == 1:  # Long position
                    if open_p <= stop_loss:
                        # Overnight gap down below stop loss fills at open price
                        exited = True
                        exit_price = open_p * (1.0 - slippage)
                        exit_reason = "gap_stop_loss"
                    elif open_p >= take_profit:
                        # Overnight gap up above take profit fills at open price
                        exited = True
                        exit_price = open_p * (1.0 - slippage)
                        exit_reason = "gap_take_profit"
                    elif low <= stop_loss:
                        exited = True
                        exit_price = stop_loss * (1.0 - slippage)
                        exit_reason = "stop_loss"
                    elif high >= take_profit:
                        exited = True
                        exit_price = take_profit * (1.0 - slippage)
                        exit_reason = "take_profit"
                    elif signal == -1:  # Signal reversal
                        exited = True
                        exit_price = close * (1.0 - slippage)
                        exit_reason = "signal_reversal"
                elif side == -1:  # Short position
                    if open_p >= stop_loss:
                        # Overnight gap up above stop loss fills at open price
                        exited = True
                        exit_price = open_p * (1.0 + slippage)
                        exit_reason = "gap_stop_loss"
                    elif open_p <= take_profit:
                        # Overnight gap down below take profit fills at open price
                        exited = True
                        exit_price = open_p * (1.0 + slippage)
                        exit_reason = "gap_take_profit"
                    elif high >= stop_loss:
                        exited = True
                        exit_price = stop_loss * (1.0 + slippage)
                        exit_reason = "stop_loss"
                    elif low <= take_profit:
                        exited = True
                        exit_price = take_profit * (1.0 + slippage)
                        exit_reason = "take_profit"
                    elif signal == 1:  # Signal reversal
                        exited = True
                        exit_price = close * (1.0 + slippage)
                        exit_reason = "signal_reversal"

                # Max holding bars time-based exit (checked after SL/TP/reversal)
                if not exited and self.params.max_holding_bars is not None:
                    if (i - entry_bar_index) >= self.params.max_holding_bars:
                        exited = True
                        exit_price = close
                        exit_reason = "timeout"

                if exited:
                    if side == 1:
                        raw_pnl = (exit_price - entry_price) * units
                    else:
                        raw_pnl = (entry_price - exit_price) * units

                    # Calculate commission and regulatory fees
                    base_exit_fee = (units * exit_price) * taker_fee
                    regulatory_fee = 0.0
                    if is_stock:
                        # SEC Section 31 fee on sells ($27.80 per million)
                        sec_fee = (units * exit_price) * (
                            getattr(self.params, "sec_fee_per_million", 27.80)
                            / 1_000_000.0
                        )
                        # FINRA TAF fee ($0.000166 per share, max $8.30)
                        finra_fee = min(
                            8.30,
                            units
                            * getattr(self.params, "finra_taf_per_share", 0.000166),
                        )
                        regulatory_fee = sec_fee + finra_fee

                    exit_fee = base_exit_fee + regulatory_fee
                    total_trade_fee = active_trade.fee + exit_fee
                    net_pnl = raw_pnl - exit_fee
                    pnl_pct = (
                        (net_pnl / active_trade.position_value_usd) * 100.0
                        if active_trade.position_value_usd > 0
                        else 0.0
                    )

                    active_trade.exit_time = timestamp_str
                    active_trade.exit_price = dynamic_round_price(exit_price)
                    active_trade.exit_reason = exit_reason
                    active_trade.pnl = round(net_pnl, 2)
                    active_trade.pnl_pct = round(pnl_pct, 2)
                    active_trade.fee = round(total_trade_fee, 2)
                    active_trade.duration_bars = i - entry_bar_index

                    equity += net_pnl
                    trades.append(active_trade)
                    active_trade = None

                # A trailing stop derived from this candle's close can only
                # protect subsequent candles; checking it before this candle's
                # high/low would use future intrabar information.
                if active_trade is not None and self.params.stop_loss_type == "atr":
                    active_trade.stop_loss = self.risk_manager.update_trailing_stop(
                        side=side, current_price=close, current_stop=stop_loss, atr=atr
                    )

            # Open new trade if no position is active and signal exists
            if active_trade is None and signal in (1, -1):
                can_enter = True
                side = int(signal)

                # Check trade direction constraint
                if (
                    self.params.trade_direction == "long_only"
                    and side == -1
                    or self.params.trade_direction == "short_only"
                    and side == 1
                ):
                    can_enter = False
                # "both" and "long_short" allow both long and short entries

                # Intraday Margin Monitoring (4:1 longs, 2:1 shorts based on maintenance margin excess)
                if can_enter and enforce_margin_calls and is_stock:
                    # Check minimum equity requirement and real-time buying power constraints
                    if equity < min_margin_equity:
                        can_enter = False  # Auto-liquidation triggered
                    else:
                        # Calculate available buying power based on side
                        bp_ratio = long_bp_ratio if side == 1 else short_bp_ratio
                        available_bp = equity * bp_ratio
                        # Rough position check (more refined margin monitoring would include maintenance requirements)
                        position_cost = abs(close * self.params.position_size)
                        if position_cost > available_bp:
                            can_enter = False  # Insufficient margin

                if can_enter:
                    side = int(signal)
                    if side == 1:
                        entry_price = close * (1.0 + slippage)
                    else:
                        entry_price = close * (1.0 - slippage)

                    stop_loss, take_profit = (
                        self.risk_manager.calculate_stop_and_target(
                            entry_price=entry_price, side=side, vwap=vwap, atr=atr
                        )
                    )

                    units, _, _ = self.risk_manager.calculate_position_size(
                        equity=equity,
                        entry_price=entry_price,
                        stop_loss_price=stop_loss,
                    )

                    entry_price = dynamic_round_price(entry_price)
                    units = dynamic_round_units(units)
                    val_usd = units * entry_price

                    if units > 0 and val_usd <= equity * 3.0:  # Cap max leverage at 3x
                        trade_counter += 1
                        entry_bar_index = i
                        entry_fee = val_usd * taker_fee
                        equity -= entry_fee

                        active_trade = Trade(
                            trade_id=trade_counter,
                            side=side,
                            entry_time=timestamp_str,
                            entry_price=entry_price,
                            units=units,
                            position_value_usd=round(val_usd, 2),
                            stop_loss=dynamic_round_price(stop_loss),
                            take_profit=dynamic_round_price(take_profit),
                            fee=round(entry_fee, 2),
                        )

            # Record mark-to-market equity
            mtm_pnl = 0.0
            if active_trade is not None:
                if active_trade.side == 1:
                    mtm_pnl = (close - active_trade.entry_price) * active_trade.units
                else:
                    mtm_pnl = (active_trade.entry_price - close) * active_trade.units

            current_equity = equity + mtm_pnl
            equity_curve.append(
                {"timestamp": timestamp_str, "equity": round(current_equity, 2)}
            )

        # Close any remaining position at end of backtest data
        if active_trade is not None:
            last_close = closes[-1]
            if active_trade.side == 1:
                exit_price = last_close * (1.0 - slippage)
            else:
                exit_price = last_close * (1.0 + slippage)
            if active_trade.side == 1:
                raw_pnl = (exit_price - active_trade.entry_price) * active_trade.units
            else:
                raw_pnl = (active_trade.entry_price - exit_price) * active_trade.units

            exit_fee = (active_trade.units * exit_price) * taker_fee
            net_pnl = raw_pnl - exit_fee
            active_trade.exit_time = timestamps[-1]
            active_trade.exit_price = dynamic_round_price(exit_price)
            active_trade.exit_reason = "end_of_data"
            active_trade.pnl = round(net_pnl, 2)
            active_trade.fee = round(active_trade.fee + exit_fee, 2)
            active_trade.duration_bars = n_bars - 1 - entry_bar_index
            equity += net_pnl
            trades.append(active_trade)
            # Replace the last mark-to-market point with realized end-of-data
            # equity so analytics and final_equity describe the same curve.
            equity_curve[-1] = {
                "timestamp": timestamps[-1],
                "equity": round(equity, 2),
            }

        # Basic Performance Metrics
        total_trades = len(trades)
        winning_trades_list = [t for t in trades if t.pnl > 0]
        losing_trades_list = [t for t in trades if t.pnl <= 0]

        winning_trades = len(winning_trades_list)
        losing_trades = len(losing_trades_list)
        win_rate = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0

        net_profit = equity - capital
        total_return_pct = (net_profit / capital) * 100.0

        gains = sum(t.pnl for t in winning_trades_list)
        losses = abs(sum(t.pnl for t in losing_trades_list))
        profit_factor = (
            (gains / losses) if losses > 0 else (gains if gains > 0 else 1.0)
        )

        # Advanced Performance Metrics
        avg_trade_pnl = (net_profit / total_trades) if total_trades > 0 else 0.0
        avg_win = (gains / winning_trades) if winning_trades > 0 else 0.0
        avg_loss = (losses / losing_trades) if losing_trades > 0 else 0.0

        largest_win = max([t.pnl for t in trades], default=0.0)
        largest_loss = min([t.pnl for t in trades], default=0.0)
        payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

        win_prob = win_rate / 100.0
        expectancy = (win_prob * avg_win) - ((1.0 - win_prob) * avg_loss)

        # Consecutive Wins / Losses
        max_consec_wins = 0
        max_consec_losses = 0
        curr_consec_wins = 0
        curr_consec_losses = 0

        for t in trades:
            if t.pnl > 0:
                curr_consec_wins += 1
                curr_consec_losses = 0
                max_consec_wins = max(max_consec_wins, curr_consec_wins)
            else:
                curr_consec_losses += 1
                curr_consec_wins = 0
                max_consec_losses = max(max_consec_losses, curr_consec_losses)

        avg_duration = (
            float(np.mean([t.duration_bars for t in trades]))
            if total_trades > 0
            else 0.0
        )

        # Monthly Breakdown
        monthly_map: dict[str, float] = {}
        for t in trades:
            if t.exit_time:
                month_key = str(t.exit_time)[:7]  # YYYY-MM
                monthly_map[month_key] = monthly_map.get(month_key, 0.0) + t.pnl

        monthly_breakdown = [
            {
                "month": k,
                "net_pnl": round(v, 2),
                "return_pct": round((v / capital) * 100.0, 2),
            }
            for k, v in sorted(monthly_map.items())
        ]

        # Max Drawdown
        eq_series = pd.Series([point["equity"] for point in equity_curve])
        if len(eq_series) > 0:
            peak = eq_series.cummax()
            drawdown = (peak - eq_series) / peak
            max_drawdown_pct = float(drawdown.max() * 100.0)
        else:
            max_drawdown_pct = 0.0

        # Sharpe Ratio
        if len(eq_series) > 1:
            returns = eq_series.pct_change().dropna()
            std_ret = returns.std()
            try:
                timestamp_series = pd.to_datetime(
                    df_signals["timestamp"], utc=True, format="ISO8601"
                )
            except Exception:
                timestamp_series = pd.to_datetime(df_signals["timestamp"], utc=True)
            median_interval = timestamp_series.diff().dropna().median()
            if pd.notna(median_interval) and median_interval.total_seconds() > 0:
                periods_per_year = pd.Timedelta(days=365) / median_interval
            else:
                periods_per_year = 288 * 365
            sharpe_ratio = (
                float((returns.mean() / std_ret) * np.sqrt(periods_per_year))
                if std_ret > 0
                else 0.0
            )
            # Sortino Ratio (Downside deviation risk)
            downside_returns = returns[returns < 0]
            downside_std = downside_returns.std()
            if len(downside_returns) > 0 and downside_std > 0:
                sortino_ratio = float(
                    (returns.mean() / downside_std) * np.sqrt(periods_per_year)
                )
            else:
                sortino_ratio = sharpe_ratio if sharpe_ratio > 0 else 0.0
        else:
            sharpe_ratio = 0.0
            sortino_ratio = 0.0

        # Calmar Ratio (Total Return / Max Drawdown)
        if max_drawdown_pct > 0.05:
            calmar_ratio = float(total_return_pct / max_drawdown_pct)
        else:
            calmar_ratio = (
                float(total_return_pct / 0.1) if total_return_pct > 0 else 0.0
            )

        # Drawdown-Penalized Sharpe (Smooth returns with heavy tail-risk penalty)
        if max_drawdown_pct > 0:
            penalty = max(0.0, 1.0 - (max_drawdown_pct / 50.0))
            if sharpe_ratio > 0:
                drawdown_penalized_sharpe = float(sharpe_ratio * penalty)
            else:
                drawdown_penalized_sharpe = float(
                    sharpe_ratio - (max_drawdown_pct * 0.05)
                )
        else:
            drawdown_penalized_sharpe = float(sharpe_ratio)

        # Drawdown-Penalized Sortino (Sortino ratio adjusted for drawdown severity)
        if len(eq_series) > 1 and max_drawdown_pct > 0:
            dd_penalty = max(0.0, 1.0 - (max_drawdown_pct / 50.0))
            drawdown_penalized_sortino = float(sortino_ratio * dd_penalty)
        else:
            drawdown_penalized_sortino = float(sortino_ratio)

        # Ulcer Index: sqrt(mean of (drawdown_pct)^2 at each peak)
        # Measures depth and duration of peak-to-trough declines
        if len(eq_series) > 1:
            peak = eq_series.cummax()
            drawdown_pct = ((peak - eq_series) / peak) * 100.0
            ulcer_index = float(np.sqrt(np.mean(drawdown_pct**2)))
        else:
            ulcer_index = 0.0

        return BacktestResult(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=round(win_rate, 2),
            initial_capital=capital,
            final_equity=round(equity, 2),
            net_profit=round(net_profit, 2),
            total_return_pct=round(total_return_pct, 2),
            profit_factor=round(profit_factor, 2),
            max_drawdown_pct=round(max_drawdown_pct, 2),
            sharpe_ratio=round(sharpe_ratio, 2),
            sortino_ratio=round(sortino_ratio, 2),
            calmar_ratio=round(calmar_ratio, 2),
            drawdown_penalized_sharpe=round(drawdown_penalized_sharpe, 2),
            drawdown_penalized_sortino=round(drawdown_penalized_sortino, 2),
            ulcer_index=round(ulcer_index, 2),
            avg_trade_pnl=round(avg_trade_pnl, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            largest_win=round(largest_win, 2),
            largest_loss=round(largest_loss, 2),
            payoff_ratio=round(payoff_ratio, 2),
            expectancy=round(expectancy, 2),
            max_consecutive_wins=max_consec_wins,
            max_consecutive_losses=max_consec_losses,
            avg_duration_bars=round(avg_duration, 1),
            monthly_breakdown=monthly_breakdown,
            equity_curve=equity_curve,
            trades=[t.to_dict() for t in trades],
        )
