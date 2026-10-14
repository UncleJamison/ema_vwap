"""
Virtual Execution Engine for Paper Trading.
Executes strategy signals in simulated real-time, models fees/slippage, manages SL/TP order triggers,
and tracks mark-to-market balances and positions.
"""

import threading
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from src.config import StrategyParams
from src.data_loader import DataLoader
from src.database import CandleDatabase
from src.database import db as default_db
from src.logger import logger
from src.paper.ledger import PaperLedger
from src.paper.models import PaperPosition, PaperTradeRecord
from src.paper.profiles import PaperProfileRegistry
from src.risk_manager import RiskManager
from src.strategy import EmaVwapStrategy


class PaperTradingEngine:
    """Virtual execution engine managing order execution, live polling, and SL/TP triggers."""

    def __init__(
        self,
        db: CandleDatabase | None = None,
        account_id: str = "default",
    ) -> None:
        self.db = db or default_db
        self.account_id = account_id
        self.registry = PaperProfileRegistry(db=self.db)
        self.ledger = PaperLedger(db=self.db)

        self._running: bool = False
        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._last_evaluations: dict[str, dict[str, Any]] = {}
        self._last_poll_time: str | None = None
        self._consecutive_errors: int = 0
        self._MAX_CONSECUTIVE_ERRORS: int = 10  # Circuit-breaker threshold

    @property
    def is_running(self) -> bool:
        """Check if the live polling runner is active."""
        return self._running

    @staticmethod
    def _timeframe_seconds(timeframe: str) -> float:
        """Convert a timeframe string (e.g. '5m', '1h', '30m') to seconds."""
        _MAP = {
            "1m": 60,
            "3m": 180,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "2h": 7200,
            "4h": 14400,
            "6h": 21600,
            "1d": 86400,
        }
        return float(_MAP.get(timeframe.lower(), 300))

    def evaluate_symbol(
        self,
        exchange: str,
        symbol: str,
        timeframe: str = "5m",
        force_candles: pd.DataFrame | None = None,
        fetch_live: bool = False,
    ) -> dict[str, Any]:
        """
        Evaluate a symbol against its active Optuna parameter profile.
        Checks open positions for SL/TP exit triggers and generates new entries on signals.
        """
        with self._lock:
            # 1. Retrieve configured or fallback strategy parameters
            params = self.registry.get_strategy_params(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
            )

            # 2. Fetch candles (live from provider if polling, or cached/forced)
            if force_candles is not None:
                df_candles = force_candles.copy()
            elif fetch_live:
                df_candles = DataLoader.fetch_and_update_candles(
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=200,
                )
            else:
                df_candles = DataLoader.load_candles(
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=200,
                )

            if df_candles.empty or len(df_candles) < 20:
                result = {
                    "exchange": exchange,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "status": "insufficient_data",
                    "action": "NONE",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._last_evaluations[f"{exchange}:{symbol}:{timeframe}"] = result
                self._last_evaluations[f"{exchange}:{symbol}"] = result
                return result

            # 3. Compute indicators and signals
            strategy = EmaVwapStrategy(params)
            df_signals = strategy.generate_signals(df_candles)

            latest_bar = df_signals.iloc[-1]
            close_price = float(latest_bar["close"])
            high_price = float(latest_bar["high"])
            low_price = float(latest_bar["low"])
            bar_time = str(latest_bar["timestamp"])
            vwap_val = (
                float(latest_bar["vwap"])
                if "vwap" in latest_bar and not pd.isna(latest_bar["vwap"])
                else close_price
            )
            atr_val = (
                float(latest_bar["atr"])
                if "atr" in latest_bar and not pd.isna(latest_bar["atr"])
                else close_price * 0.01
            )
            signal = int(latest_bar["signal"])

            # 4. Check if there is an open position for this asset
            pos = self.ledger.get_position(exchange, symbol)

            if pos is not None:
                # --- Position Open: Update mark-to-market & evaluate exit triggers ---
                if pos.side == "LONG":
                    unrealized_pnl = (close_price - pos.entry_price) * pos.quantity
                    unrealized_pnl_pct = (
                        ((close_price / pos.entry_price) - 1.0) * 100.0
                        if pos.entry_price > 0
                        else 0.0
                    )
                else:
                    unrealized_pnl = (pos.entry_price - close_price) * pos.quantity
                    unrealized_pnl_pct = (
                        ((pos.entry_price / close_price) - 1.0) * 100.0
                        if close_price > 0
                        else 0.0
                    )

                pos.current_price = close_price
                pos.unrealized_pnl = round(unrealized_pnl, 2)
                pos.unrealized_pnl_pct = round(unrealized_pnl_pct, 2)

                exit_triggered = False
                exit_reason = ""
                exit_price = close_price

                # Check Stop-Loss
                if pos.stop_loss is not None:
                    if pos.side == "LONG" and low_price <= pos.stop_loss:
                        exit_triggered = True
                        exit_reason = "STOP_LOSS"
                        # Fill at stop price minus slippage
                        exit_price = pos.stop_loss * (
                            1.0 - (params.slippage_pct / 100.0)
                        )
                    elif pos.side == "SHORT" and high_price >= pos.stop_loss:
                        exit_triggered = True
                        exit_reason = "STOP_LOSS"
                        exit_price = pos.stop_loss * (
                            1.0 + (params.slippage_pct / 100.0)
                        )

                # Check Take-Profit
                if not exit_triggered and pos.take_profit is not None:
                    if (
                        pos.side == "LONG"
                        and high_price >= pos.take_profit
                        or pos.side == "SHORT"
                        and low_price <= pos.take_profit
                    ):
                        exit_triggered = True
                        exit_reason = "TAKE_PROFIT"
                        exit_price = pos.take_profit

                if exit_triggered:
                    # Execute sell/exit order
                    taker_fee = (
                        pos.quantity * exit_price * (params.taker_fee_pct / 100.0)
                    )
                    entry_fee = (
                        pos.quantity * pos.entry_price * (params.taker_fee_pct / 100.0)
                    )
                    if pos.side == "LONG":
                        realized_pnl = (
                            (exit_price - pos.entry_price) * pos.quantity
                            - entry_fee
                            - taker_fee
                        )
                        realized_pnl_pct = (
                            realized_pnl / (pos.cost_basis or 1.0)
                        ) * 100.0
                    else:
                        realized_pnl = (
                            (pos.entry_price - exit_price) * pos.quantity
                            - entry_fee
                            - taker_fee
                        )
                        realized_pnl_pct = (
                            realized_pnl / (pos.cost_basis or 1.0)
                        ) * 100.0

                    self.ledger.record_close_position(
                        account_id=self.account_id,
                        pos=pos,
                        exit_price=exit_price,
                        fee=taker_fee,
                        notes=f"Paper Exit: {exit_reason}",
                    )

                    # Log completed trade in history
                    trade_record = PaperTradeRecord(
                        trade_id=f"tr_{uuid.uuid4().hex[:8]}",
                        exchange=exchange,
                        symbol=symbol,
                        timeframe=timeframe,
                        side=pos.side,
                        entry_price=pos.entry_price,
                        exit_price=exit_price,
                        quantity=pos.quantity,
                        entry_time=pos.entry_time or bar_time,
                        exit_time=bar_time,
                        holding_period_bars=1,
                        realized_pnl=round(realized_pnl, 2),
                        realized_pnl_pct=round(realized_pnl_pct, 2),
                        fees=round(entry_fee + taker_fee, 2),
                        exit_reason=exit_reason,
                        params=params.to_dict(),
                    )
                    self.ledger.record_completed_trade(trade_record)
                    self.ledger.close_position(exchange, symbol)

                    result = {
                        "exchange": exchange,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "action": f"EXIT_{exit_reason}",
                        "exit_price": round(exit_price, 4),
                        "realized_pnl": round(realized_pnl, 2),
                        "realized_pnl_pct": round(realized_pnl_pct, 2),
                        "timestamp": bar_time,
                    }
                    self._last_evaluations[f"{exchange}:{symbol}:{timeframe}"] = result
                    self._last_evaluations[f"{exchange}:{symbol}"] = result
                    return result

                # Check max_holding_bars time-based exit (runs only when no SL/TP triggered)
                elif params.max_holding_bars is not None and pos.entry_time:
                    try:
                        entry_dt = pd.to_datetime(pos.entry_time, utc=True)
                        bar_dt = pd.to_datetime(bar_time, utc=True)
                        bars_held = int(
                            (bar_dt - entry_dt).total_seconds()
                            / self._timeframe_seconds(timeframe)
                        )
                        if bars_held >= params.max_holding_bars:
                            taker_fee = (
                                pos.quantity
                                * close_price
                                * (params.taker_fee_pct / 100.0)
                            )
                            entry_fee = (
                                pos.quantity
                                * pos.entry_price
                                * (params.taker_fee_pct / 100.0)
                            )
                            if pos.side == "LONG":
                                realized_pnl = (
                                    (close_price - pos.entry_price) * pos.quantity
                                    - entry_fee
                                    - taker_fee
                                )
                            else:
                                realized_pnl = (
                                    (pos.entry_price - close_price) * pos.quantity
                                    - entry_fee
                                    - taker_fee
                                )
                            realized_pnl_pct = (
                                realized_pnl / (pos.cost_basis or 1.0)
                            ) * 100.0
                            self.ledger.record_close_position(
                                account_id=self.account_id,
                                pos=pos,
                                exit_price=close_price,
                                fee=taker_fee,
                                notes=f"Paper Exit: TIMEOUT ({bars_held} bars)",
                            )
                            trade_record = PaperTradeRecord(
                                trade_id=f"tr_{uuid.uuid4().hex[:8]}",
                                exchange=exchange,
                                symbol=symbol,
                                timeframe=timeframe,
                                side=pos.side,
                                entry_price=pos.entry_price,
                                exit_price=close_price,
                                quantity=pos.quantity,
                                entry_time=pos.entry_time or bar_time,
                                exit_time=bar_time,
                                holding_period_bars=bars_held,
                                realized_pnl=round(realized_pnl, 2),
                                realized_pnl_pct=round(realized_pnl_pct, 2),
                                fees=round(entry_fee + taker_fee, 2),
                                exit_reason="TIMEOUT",
                                params=params.to_dict(),
                            )
                            self.ledger.record_completed_trade(trade_record)
                            self.ledger.close_position(exchange, symbol)
                            result = {
                                "exchange": exchange,
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "action": "EXIT_TIMEOUT",
                                "exit_price": round(close_price, 4),
                                "holding_period_bars": bars_held,
                                "realized_pnl": round(realized_pnl, 2),
                                "realized_pnl_pct": round(realized_pnl_pct, 2),
                                "timestamp": bar_time,
                            }
                            self._last_evaluations[
                                f"{exchange}:{symbol}:{timeframe}"
                            ] = result
                            self._last_evaluations[f"{exchange}:{symbol}"] = result
                            return result
                    except Exception:
                        pass  # entry_time parse failure: fall through to HOLD

                    # Update trailing stop if ATR stop is configured
                    if params.stop_loss_type == "atr" and pos.stop_loss is not None:
                        rm = RiskManager(params)
                        side_int = 1 if pos.side == "LONG" else -1
                        pos.stop_loss = rm.update_trailing_stop(
                            side_int, close_price, pos.stop_loss, atr_val
                        )

                    self.ledger.save_position(pos)
                    result = {
                        "exchange": exchange,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "action": "HOLD",
                        "current_price": close_price,
                        "unrealized_pnl": pos.unrealized_pnl,
                        "unrealized_pnl_pct": pos.unrealized_pnl_pct,
                        "stop_loss": pos.stop_loss,
                        "take_profit": pos.take_profit,
                        "timestamp": bar_time,
                    }
                    self._last_evaluations[f"{exchange}:{symbol}:{timeframe}"] = result
                    self._last_evaluations[f"{exchange}:{symbol}"] = result
                    return result

                else:
                    # Update trailing stop if ATR stop is configured
                    if params.stop_loss_type == "atr" and pos.stop_loss is not None:
                        rm = RiskManager(params)
                        side_int = 1 if pos.side == "LONG" else -1
                        pos.stop_loss = rm.update_trailing_stop(
                            side_int, close_price, pos.stop_loss, atr_val
                        )

                    self.ledger.save_position(pos)
                    result = {
                        "exchange": exchange,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "action": "HOLD",
                        "current_price": close_price,
                        "unrealized_pnl": pos.unrealized_pnl,
                        "unrealized_pnl_pct": pos.unrealized_pnl_pct,
                        "stop_loss": pos.stop_loss,
                        "take_profit": pos.take_profit,
                        "timestamp": bar_time,
                    }
                    self._last_evaluations[f"{exchange}:{symbol}:{timeframe}"] = result
                    self._last_evaluations[f"{exchange}:{symbol}"] = result
                    return result

            else:
                # --- No Position Open: Evaluate entry triggers ---
                should_enter = False
                side_str = "LONG"
                side_int = 1

                if signal == 1 and params.trade_direction != "short_only":
                    should_enter = True
                    side_str = "LONG"
                    side_int = 1
                elif signal == -1 and params.trade_direction != "long_only":
                    should_enter = True
                    side_str = "SHORT"
                    side_int = -1

                if should_enter:
                    rm = RiskManager(params)
                    stop_loss, take_profit = rm.calculate_stop_and_target(
                        entry_price=close_price,
                        side=side_int,
                        vwap=vwap_val,
                        atr=atr_val,
                    )

                    current_balance = self.ledger.get_balance(self.account_id)
                    units, pos_val, _risk_amount = rm.calculate_position_size(
                        equity=current_balance,
                        entry_price=close_price,
                        stop_loss_price=stop_loss,
                    )

                    # Limit max allocation to 95% of cash to avoid overdraft on fees
                    max_allowed_val = current_balance * 0.95
                    if units <= 0 or pos_val > max_allowed_val:
                        units = max_allowed_val / close_price
                        pos_val = units * close_price

                    if units > 0 and current_balance >= 10.0:
                        # Model slippage on entry
                        slip_mult = 1.0 + (
                            (params.slippage_pct / 100.0)
                            if side_int == 1
                            else -(params.slippage_pct / 100.0)
                        )
                        fill_price = close_price * slip_mult
                        entry_fee = units * fill_price * (params.taker_fee_pct / 100.0)

                        self.ledger.record_buy(
                            account_id=self.account_id,
                            exchange=exchange,
                            symbol=symbol,
                            quantity=units,
                            price=fill_price,
                            fee=entry_fee,
                            notes="Paper Signal Entry",
                        )

                        new_pos = PaperPosition(
                            exchange=exchange,
                            symbol=symbol,
                            side=side_str,
                            entry_price=round(fill_price, 4),
                            current_price=round(fill_price, 4),
                            quantity=units,
                            cost_basis=round(units * fill_price, 2),
                            stop_loss=round(stop_loss, 4),
                            take_profit=round(take_profit, 4),
                            unrealized_pnl=0.0,
                            unrealized_pnl_pct=0.0,
                            entry_time=bar_time,
                            metadata={"params": params.to_dict()},
                        )
                        self.ledger.save_position(new_pos)

                        result = {
                            "exchange": exchange,
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "action": f"ENTER_{side_str}",
                            "entry_price": round(fill_price, 4),
                            "quantity": units,
                            "stop_loss": round(stop_loss, 4),
                            "take_profit": round(take_profit, 4),
                            "timestamp": bar_time,
                        }
                        self._last_evaluations[f"{exchange}:{symbol}:{timeframe}"] = (
                            result
                        )
                        self._last_evaluations[f"{exchange}:{symbol}"] = result
                        return result

                result = {
                    "exchange": exchange,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "action": "NONE",
                    "current_price": close_price,
                    "signal": signal,
                    "timestamp": bar_time,
                }
                self._last_evaluations[f"{exchange}:{symbol}:{timeframe}"] = result
                self._last_evaluations[f"{exchange}:{symbol}"] = result
                return result

    def close_position_market(
        self,
        exchange: str,
        symbol: str,
        current_price: float | None = None,
    ) -> PaperTradeRecord | None:
        """Manually close an open paper position at market price."""
        with self._lock:
            pos = self.ledger.get_position(exchange, symbol)
            if not pos:
                return None

            price = current_price or pos.current_price
            params = self.registry.get_strategy_params(exchange, symbol)
            fee = pos.quantity * price * (params.taker_fee_pct / 100.0)

            if pos.side == "LONG":
                realized_pnl = (price - pos.entry_price) * pos.quantity - fee
            else:
                realized_pnl = (pos.entry_price - price) * pos.quantity - fee

            realized_pnl_pct = (realized_pnl / (pos.cost_basis or 1.0)) * 100.0
            now_iso = datetime.now(timezone.utc).isoformat()

            self.ledger.record_close_position(
                account_id=self.account_id,
                pos=pos,
                exit_price=price,
                fee=fee,
                notes="Manual Market Close",
            )

            trade = PaperTradeRecord(
                trade_id=f"tr_{uuid.uuid4().hex[:8]}",
                exchange=exchange,
                symbol=symbol,
                timeframe="manual",
                side=pos.side,
                entry_price=pos.entry_price,
                exit_price=price,
                quantity=pos.quantity,
                entry_time=pos.entry_time or now_iso,
                exit_time=now_iso,
                holding_period_bars=0,
                realized_pnl=round(realized_pnl, 2),
                realized_pnl_pct=round(realized_pnl_pct, 2),
                fees=round(fee, 2),
                exit_reason="MANUAL",
                params=params.to_dict(),
            )
            self.ledger.record_completed_trade(trade)
            self.ledger.close_position(exchange, symbol)
            return trade

    def step_all_active_profiles(self) -> list[dict[str, Any]]:
        """Evaluate all active paper trading profiles with live candle fetching."""
        profiles = self.registry.list_profiles(active_only=True)
        results = []
        for p in profiles:
            try:
                res = self.evaluate_symbol(
                    exchange=p.exchange,
                    symbol=p.symbol,
                    timeframe=p.timeframe,
                    fetch_live=True,
                )
                results.append(res)
            except Exception as e:
                logger.error(
                    f"[PaperTradingEngine] Error evaluating {p.symbol} ({p.exchange}): {e}"
                )
                results.append(
                    {
                        "exchange": p.exchange,
                        "symbol": p.symbol,
                        "timeframe": p.timeframe,
                        "status": "error",
                        "error": str(e),
                    }
                )

        self._last_poll_time = datetime.now(timezone.utc).isoformat()
        return results

    def reconcile_positions_on_startup(
        self, force_candles_map: dict[str, pd.DataFrame] | None = None
    ) -> list[dict[str, Any]]:
        """
        Audit all open positions against historical candles spanning the downtime gap.
        Evaluates chronologically bar-by-bar if Stop-Loss or Take-Profit levels were breached
        while the server was offline, executing backfilled fills with historical timestamps.
        """
        open_positions = self.ledger.list_positions()
        if not open_positions:
            logger.info("[PaperTradingEngine] No open positions to reconcile.")
            return []

        logger.info(
            f"[PaperTradingEngine] Reconciling {len(open_positions)} open position(s) across downtime window..."
        )
        reconciliation_results: list[dict[str, Any]] = []

        for pos in open_positions:
            exchange = pos.exchange
            symbol = pos.symbol
            key = f"{exchange}:{symbol}"

            # Retrieve profile or reconstruct default params from metadata
            profile = self.registry.get_profile(exchange, symbol)
            timeframe = profile.timeframe if profile else "5m"
            if profile:
                params = profile.get_strategy_params()
            elif pos.metadata and "params" in pos.metadata:
                params = StrategyParams.from_dict(pos.metadata["params"])
            else:
                params = StrategyParams()

            # Obtain historical candles
            if force_candles_map and key in force_candles_map:
                df_candles = force_candles_map[key]
            else:
                try:
                    df_candles = DataLoader.load_candles(
                        exchange=exchange,
                        symbol=symbol,
                        timeframe=timeframe,
                        limit=300,
                    )
                except Exception as e:
                    logger.error(
                        f"[PaperTradingEngine] Reconcile candle fetch failed for {key}: {e}"
                    )
                    continue

            if df_candles.empty or len(df_candles) < 2:
                continue

            # Ensure timestamp column is parsed
            if not pd.api.types.is_datetime64_any_dtype(df_candles["timestamp"]):
                try:
                    df_candles["timestamp"] = pd.to_datetime(
                        df_candles["timestamp"], utc=True, format="ISO8601"
                    )
                except Exception:
                    df_candles["timestamp"] = pd.to_datetime(
                        df_candles["timestamp"], utc=True
                    )

            # Determine checkpoint timestamp (updated_time or entry_time)
            checkpoint_str = pos.updated_time or pos.entry_time
            if checkpoint_str:
                try:
                    checkpoint_dt = pd.to_datetime(checkpoint_str, utc=True)
                    missed_candles = df_candles[
                        df_candles["timestamp"] >= checkpoint_dt
                    ]
                    if missed_candles.empty:
                        missed_candles = df_candles.tail(10)
                except Exception:
                    missed_candles = df_candles.tail(10)
            else:
                missed_candles = df_candles.tail(10)

            position_closed = False
            last_checked_bar_time = ""
            for _, row in missed_candles.iterrows():
                ts_val = row["timestamp"]
                bar_time = (
                    ts_val.isoformat()
                    if hasattr(ts_val, "isoformat")
                    else str(ts_val).replace(" ", "T")
                )
                last_checked_bar_time = bar_time
                high_price = float(row["high"])
                low_price = float(row["low"])
                close_price = float(row["close"])

                exit_triggered = False
                exit_reason = ""
                exit_price = close_price

                # Check Stop-Loss breach
                if pos.stop_loss is not None:
                    if pos.side == "LONG" and low_price <= pos.stop_loss:
                        exit_triggered = True
                        exit_reason = "STOP_LOSS"
                        slip_mult = 1.0 - (params.slippage_pct / 100.0)
                        exit_price = pos.stop_loss * slip_mult
                    elif pos.side == "SHORT" and high_price >= pos.stop_loss:
                        exit_triggered = True
                        exit_reason = "STOP_LOSS"
                        slip_mult = 1.0 + (params.slippage_pct / 100.0)
                        exit_price = pos.stop_loss * slip_mult

                # Check Take-Profit breach
                if not exit_triggered and pos.take_profit is not None:
                    if (
                        pos.side == "LONG"
                        and high_price >= pos.take_profit
                        or pos.side == "SHORT"
                        and low_price <= pos.take_profit
                    ):
                        exit_triggered = True
                        exit_reason = "TAKE_PROFIT"
                        exit_price = pos.take_profit

                # Check max_holding_bars time-based exit during reconcile
                if (
                    not exit_triggered
                    and params.max_holding_bars is not None
                    and pos.entry_time
                ):
                    try:
                        entry_dt = pd.to_datetime(pos.entry_time, utc=True)
                        bar_dt = pd.to_datetime(bar_time, utc=True)
                        bars_held = int(
                            (bar_dt - entry_dt).total_seconds()
                            / self._timeframe_seconds(timeframe)
                        )
                        if bars_held >= params.max_holding_bars:
                            exit_triggered = True
                            exit_reason = "TIMEOUT"
                            exit_price = close_price
                    except Exception:
                        pass

                if exit_triggered:
                    taker_fee = (
                        pos.quantity * exit_price * (params.taker_fee_pct / 100.0)
                    )
                    entry_fee = (
                        pos.quantity * pos.entry_price * (params.taker_fee_pct / 100.0)
                    )
                    if pos.side == "LONG":
                        realized_pnl = (
                            (exit_price - pos.entry_price) * pos.quantity
                            - entry_fee
                            - taker_fee
                        )
                    else:
                        realized_pnl = (
                            (pos.entry_price - exit_price) * pos.quantity
                            - entry_fee
                            - taker_fee
                        )
                    realized_pnl_pct = (realized_pnl / (pos.cost_basis or 1.0)) * 100.0

                    self.ledger.record_close_position(
                        account_id=self.account_id,
                        pos=pos,
                        exit_price=exit_price,
                        fee=taker_fee,
                        notes=f"Paper Reconcile Exit: {exit_reason} (at {bar_time})",
                    )

                    trade_record = PaperTradeRecord(
                        trade_id=f"tr_{uuid.uuid4().hex[:8]}",
                        exchange=exchange,
                        symbol=symbol,
                        timeframe=timeframe,
                        side=pos.side,
                        entry_price=pos.entry_price,
                        exit_price=round(exit_price, 4),
                        quantity=pos.quantity,
                        entry_time=pos.entry_time or bar_time,
                        exit_time=bar_time,
                        holding_period_bars=1,
                        realized_pnl=round(realized_pnl, 2),
                        realized_pnl_pct=round(realized_pnl_pct, 2),
                        fees=round(entry_fee + taker_fee, 2),
                        exit_reason=exit_reason,
                        params=params.to_dict(),
                    )
                    self.ledger.record_completed_trade(trade_record)
                    self.ledger.close_position(exchange, symbol)

                    rec_item = {
                        "exchange": exchange,
                        "symbol": symbol,
                        "action": f"RECONCILE_EXIT_{exit_reason}",
                        "exit_price": round(exit_price, 4),
                        "realized_pnl": round(realized_pnl, 2),
                        "timestamp": bar_time,
                    }
                    reconciliation_results.append(rec_item)
                    logger.info(
                        f"[PaperTradingEngine] Reconciled exit for {key}: {exit_reason} at {bar_time}, PnL: ${realized_pnl:.2f}"
                    )
                    position_closed = True
                    break

            if not position_closed and len(missed_candles) > 0:
                latest_close = float(missed_candles.iloc[-1]["close"])
                if pos.side == "LONG":
                    unrealized_pnl = (latest_close - pos.entry_price) * pos.quantity
                else:
                    unrealized_pnl = (pos.entry_price - latest_close) * pos.quantity
                unrealized_pnl_pct = (unrealized_pnl / (pos.cost_basis or 1.0)) * 100.0

                pos.current_price = round(latest_close, 4)
                pos.unrealized_pnl = round(unrealized_pnl, 2)
                pos.unrealized_pnl_pct = round(unrealized_pnl_pct, 2)
                pos.updated_time = (
                    last_checked_bar_time or datetime.now(timezone.utc).isoformat()
                )
                self.ledger.save_position(pos)

                rec_item = {
                    "exchange": exchange,
                    "symbol": symbol,
                    "action": "RECONCILE_UPDATED_MTM",
                    "current_price": round(latest_close, 4),
                    "unrealized_pnl": round(unrealized_pnl, 2),
                    "timestamp": pos.updated_time,
                }
                reconciliation_results.append(rec_item)

        return reconciliation_results

    def start_polling(self, interval_seconds: int = 15, persist: bool = True) -> bool:
        """Start the background live polling loop, first performing downtime gap reconciliation."""
        if self._running:
            return False

        # Reconcile open positions before launching loop
        try:
            self.reconcile_positions_on_startup()
        except Exception as e:
            logger.error(f"[PaperTradingEngine] Startup reconciliation error: {e}")

        self._running = True
        self._stop_event.clear()

        if persist and self.db is not None:
            self.db.save_setting("paper_runner_enabled", "true", is_secret=False)
            self.db.save_setting(
                "paper_polling_interval", str(interval_seconds), is_secret=False
            )

        def _poll_loop():
            logger.info(
                f"[PaperTradingEngine] Started live polling runner (interval={interval_seconds}s)"
            )
            consecutive_errors = 0
            circuit_open = False
            CIRCUIT_PAUSE_S = 60  # seconds to wait when circuit-breaker trips
            while not self._stop_event.is_set():
                try:
                    self.step_all_active_profiles()
                    consecutive_errors = 0
                    if circuit_open:
                        circuit_open = False
                        logger.info(
                            "[PaperTradingEngine] Circuit-breaker reset — polling resumed."
                        )
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"[PaperTradingEngine] Polling loop error: {e}")
                    if (
                        consecutive_errors >= self._MAX_CONSECUTIVE_ERRORS
                        and not circuit_open
                    ):
                        circuit_open = True
                        logger.critical(
                            f"[PaperTradingEngine] CIRCUIT BREAKER TRIPPED after "
                            f"{consecutive_errors} consecutive errors. "
                            f"Pausing polling for {CIRCUIT_PAUSE_S}s. Last error: {e}"
                        )
                        self._stop_event.wait(CIRCUIT_PAUSE_S)
                        continue
                self._stop_event.wait(interval_seconds)
            logger.info("[PaperTradingEngine] Polling runner stopped.")

        self._poll_thread = threading.Thread(
            target=_poll_loop, name="PaperTradingRunner", daemon=True
        )
        self._poll_thread.start()
        return True

    def stop_polling(self, persist: bool = True) -> bool:
        """Stop the background live polling loop."""
        if not self._running:
            if persist and self.db is not None:
                self.db.save_setting("paper_runner_enabled", "false", is_secret=False)
            return False

        self._running = False
        self._stop_event.set()
        if persist and self.db is not None:
            self.db.save_setting("paper_runner_enabled", "false", is_secret=False)
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)
        self._poll_thread = None
        return True

    def get_status(self) -> dict[str, Any]:
        """Get live runner status and summary."""
        profiles = self.registry.list_profiles(active_only=True)
        positions = self.ledger.list_positions()
        balance = self.ledger.get_balance(self.account_id)
        stats = self.ledger.get_statistics()

        unrealized_total = sum(p.unrealized_pnl for p in positions)
        positions_market_value = sum(p.cost_basis + p.unrealized_pnl for p in positions)
        total_equity = balance + positions_market_value

        return {
            "is_running": self._running,
            "account_id": self.account_id,
            "cash_balance": round(balance, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(unrealized_total, 2),
            "active_profiles_count": len(profiles),
            "open_positions_count": len(positions),
            "last_poll_time": self._last_poll_time,
            "last_evaluations": self._last_evaluations,
            "performance": stats,
        }

    def get_balance_snapshot(self) -> dict[str, Any]:
        """Get current paper trading balance snapshot for portfolio aggregation."""
        self.registry.list_profiles(active_only=True)
        positions = self.ledger.list_positions()
        balance = self.ledger.get_balance(self.account_id)
        self.ledger.get_statistics()

        unrealized_total = sum(p.unrealized_pnl for p in positions)
        positions_market_value = sum(p.cost_basis + p.unrealized_pnl for p in positions)
        total_equity = balance + positions_market_value

        # Convert positions to AssetPosition-like dicts
        position_dicts = []
        for p in positions:
            position_dicts.append(
                {
                    "symbol": p.symbol,
                    "side": p.side,
                    "entry_price": p.entry_price,
                    "quantity": p.quantity,
                    "cost_basis": p.cost_basis,
                    "current_price": p.current_price,
                    "unrealized_pnl_usd": p.unrealized_pnl,
                    "unrealized_pnl_pct": p.unrealized_pnl_pct,
                }
            )

        return {
            "cash_usd": round(balance, 2),
            "stablecoin_usd": 0.0,
            "crypto_usd": sum(
                p.cost_basis + p.unrealized_pnl
                for p in positions
                if p.side in ("LONG", "SHORT")
            ),
            "stock_usd": 0.0,
            "positions": position_dicts,
            "total_nav_usd": round(total_equity, 2),
            "buying_power_usd": round(total_equity * 4.0, 2),  # default margin
            "unrealized_pnl_usd": round(unrealized_total, 2),
        }

    def close_position_manual(
        self, exchange: str, symbol: str, exit_price: float | None = None
    ) -> dict[str, Any]:
        """Manually close an open paper trading position at market/current price."""
        trade = self.close_position_market(exchange, symbol, exit_price)
        if not trade:
            raise ValueError(f"No open position found for {exchange}:{symbol}")
        return {
            "exchange": trade.exchange,
            "symbol": trade.symbol,
            "action": "EXIT_MANUAL_CLOSE",
            "exit_price": trade.exit_price,
            "realized_pnl": trade.realized_pnl,
            "realized_pnl_pct": trade.realized_pnl_pct,
            "timestamp": trade.exit_time,
        }

    def update_position_sl_tp(
        self,
        exchange: str,
        symbol: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> dict[str, Any]:
        """Dynamically update Stop-Loss and/or Take-Profit price levels on an open position."""
        pos = self.ledger.get_position(exchange, symbol)
        if not pos:
            raise ValueError(f"No open position found for {exchange}:{symbol}")

        if stop_loss is not None:
            pos.stop_loss = round(stop_loss, 4) if stop_loss > 0 else None
        if take_profit is not None:
            pos.take_profit = round(take_profit, 4) if take_profit > 0 else None

        self.ledger.save_position(pos)
        return pos.to_dict()


# Global paper trading engine singleton
paper_engine = PaperTradingEngine()
