"""
Virtual Execution Engine for Paper Trading.
Executes strategy signals in simulated real-time, models fees/slippage, manages SL/TP order triggers,
and tracks mark-to-market balances and positions.
"""

import asyncio
import json
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from src.config import StrategyParams
from src.data_loader import DataLoader
from src.database import CandleDatabase
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
        settings_mgr=None,
        max_evaluations: int = 500,
        eval_ttl_seconds: float = 3600.0,
    ) -> None:
        if db is None:
            from src.db_connection_manager import get_database

            db = get_database("paper_trading")
        self.db = db
        self.account_id = account_id
        self.settings_mgr = settings_mgr
        self.registry = PaperProfileRegistry()
        self.ledger = PaperLedger()

        self._running: bool = False
        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._position_locks: dict[str, threading.Lock] = {}
        self._locks_mutex = threading.Lock()
        self._poll_cycle_lock = threading.Lock()
        self._poll_cycle_active: bool = (
            False  # ema_vwap-skxp: fine-grained poll state flag
        )

        # In-memory evaluation cache with bounded capacity and TTL retention
        self._last_evaluations: dict[str, dict[str, Any]] = {}
        self._eval_timestamps: dict[str, float] = {}
        self._eval_lock = threading.Lock()
        self._max_evaluations: int = max_evaluations
        self._eval_ttl_seconds: float = eval_ttl_seconds

        self._last_poll_time: str | None = None
        self._last_poll_metrics: dict[str, Any] = {}
        self._consecutive_errors: int = 0
        self._MAX_CONSECUTIVE_ERRORS: int = 10  # Circuit-breaker threshold

        # Instance caches (ema_vwap-64wn/j24k): strategy & risk manager objects are
        # parameter-keyed; recreating them on every evaluate_symbol call is wasted work.
        self._strategy_cache: dict[tuple, EmaVwapStrategy] = {}
        self._risk_manager_cache: dict[tuple, RiskManager] = {}

    @staticmethod
    def _params_cache_key(params) -> tuple:
        """Build a hashable cache key from StrategyParams (ema_vwap-64wn)."""
        import dataclasses

        try:
            d = dataclasses.asdict(params)
        except TypeError:
            d = vars(params)
        return tuple(sorted((k, repr(v)) for k, v in d.items()))

    def _get_strategy(self, params) -> "EmaVwapStrategy":
        """Return a cached EmaVwapStrategy for the given params (ema_vwap-64wn)."""
        key = self._params_cache_key(params)
        strategy = self._strategy_cache.get(key)
        if strategy is None:
            strategy = EmaVwapStrategy(params)
            if len(self._strategy_cache) >= 32:
                self._strategy_cache.clear()
            self._strategy_cache[key] = strategy
        return strategy

    def _get_risk_manager(self, params) -> "RiskManager":
        """Return a cached RiskManager for the given params (ema_vwap-64wn)."""
        key = self._params_cache_key(params)
        rm = self._risk_manager_cache.get(key)
        if rm is None:
            rm = RiskManager(params)
            if len(self._risk_manager_cache) >= 32:
                self._risk_manager_cache.clear()
            self._risk_manager_cache[key] = rm
        return rm

    @property
    def is_running(self) -> bool:
        """Check if the live polling runner is active."""
        return self._running

    @property
    def last_poll_metrics(self) -> dict[str, Any]:
        """Return metrics captured during the latest polling cycle."""
        return dict(self._last_poll_metrics)

    def _get_position_lock(self, exchange: str, symbol: str) -> threading.Lock:
        """Get or create a per-position serialized settlement lock."""
        key = f"{self.account_id}:{exchange}:{symbol}"
        with self._locks_mutex:
            if key not in self._position_locks:
                self._position_locks[key] = threading.Lock()
            return self._position_locks[key]

    def _record_evaluation(self, key: str, result: dict[str, Any]) -> None:
        """Store evaluation result in _last_evaluations with TTL expiration and bounded capacity."""
        with self._eval_lock:
            now = time.time()
            self._eval_timestamps[key] = now
            self._last_evaluations[key] = result
            self._prune_evaluations_locked(now=now)

    def _prune_evaluations_locked(self, now: float | None = None) -> None:
        """Evict expired entries (> TTL) and oldest entries if capacity exceeds max_evaluations."""
        if now is None:
            now = time.time()

        # 1. Expire entries older than TTL
        expired_keys = [
            k
            for k, ts in self._eval_timestamps.items()
            if (now - ts) > self._eval_ttl_seconds
        ]
        for k in expired_keys:
            self._eval_timestamps.pop(k, None)
            self._last_evaluations.pop(k, None)

        # 2. Bound capacity: if still exceeds max_evaluations, evict oldest
        if len(self._last_evaluations) > self._max_evaluations:
            sorted_keys = sorted(
                self._eval_timestamps.keys(),
                key=lambda k: self._eval_timestamps.get(k, 0.0),
            )
            excess = len(self._last_evaluations) - self._max_evaluations
            for k in sorted_keys[:excess]:
                self._eval_timestamps.pop(k, None)
                self._last_evaluations.pop(k, None)

    def prune_evaluations(self) -> None:
        """Public method to manually trigger evaluation cache pruning."""
        with self._eval_lock:
            self._prune_evaluations_locked()

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
        force_signals: pd.DataFrame | None = None,
    ) -> dict[str, Any]:
        """Evaluate a symbol against its active Optuna parameter profile.

        Checks open positions for SL/TP exit triggers and generates new entries on signals.

        Candle-source precedence (ema_vwap-7d86, documented to avoid ambiguity
        across the three call contexts):
          1. ``force_candles`` — caller-supplied snapshot (step_all_active_profiles
             shares one fetch across profiles on the same asset).
          2. ``fetch_live=True`` — provider fetch + DB update (manual step endpoint).
          3. else — DB cache read (default).

        Signal-source precedence:
          1. ``force_signals`` — caller-supplied precomputed signals.
          2. else — computed from the resolved candles with the profile's params.

        Raises:
            ValueError: if force_signals is supplied without force_candles
                (signals must correspond to the candles being evaluated).
        """
        if force_signals is not None and force_candles is None:
            raise ValueError(
                "force_signals requires force_candles: signals must be derived "
                "from the same candle frame being evaluated."
            )
        # 1. Retrieve configured or fallback strategy parameters (no lock)
        params = self.registry.get_strategy_params_sync(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
        )

        # 2. Fetch candles (live from provider if polling, or cached/forced) (no lock)
        if force_candles is not None:
            df_candles = force_candles.copy()
        elif fetch_live:
            df_candles = DataLoader.fetch_and_update_candles_sync(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                limit=200,
            )
        else:
            df_candles = DataLoader.load_candles_sync(
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
                "timestamp": datetime.now(UTC).isoformat(),
            }
            self._record_evaluation(f"{exchange}:{symbol}:{timeframe}", result)
            self._record_evaluation(f"{exchange}:{symbol}", result)
            return result

        # 3. Compute indicators and signals (no lock)
        if force_signals is not None:
            df_signals = force_signals
        else:
            strategy = self._get_strategy(params)
            df_signals = strategy.generate_signals(df_candles)

        latest_bar = df_signals.iloc[-1]
        close_price = float(latest_bar["close"])
        high_price = float(latest_bar["high"])
        low_price = float(latest_bar["low"])
        # Use isoformat() for consistent timestamp format (T separator)
        # to match what PG round-trip produces via datetime.isoformat()
        bar_time = str(latest_bar["timestamp"])
        if hasattr(latest_bar["timestamp"], "isoformat"):
            bar_time = latest_bar["timestamp"].isoformat()
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
        # Determine regime for risk management (from strategy signals)
        if "regime" in df_signals.columns:
            regime = df_signals["regime"].iloc[-1]
            if regime == "insufficient_data":
                regime = "trending"
        else:
            regime = "trending"  # fallback

        # 4. Serialized settlement per position
        pos_lock = self._get_position_lock(exchange, symbol)
        with pos_lock:
            pos = self.ledger.get_position_sync(
                exchange, symbol, account_id=self.account_id
            )

            if pos is not None:
                # --- Idempotency guard: skip if we already processed this bar ---
                # Normalize timestamps for comparison (DB returns datetime, DataFrame uses ISO string)
                pos_time = None
                if pos.last_bar_time:
                    if isinstance(pos.last_bar_time, str):
                        pos_time = pos.last_bar_time.replace(" ", "T")
                    else:
                        # datetime object - convert to ISO format
                        pos_time = pos.last_bar_time.isoformat()
                if pos_time is not None and bar_time == pos_time:
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
                        "note": "duplicate_bar_skipped",
                    }
                    self._record_evaluation(f"{exchange}:{symbol}:{timeframe}", result)
                    self._record_evaluation(f"{exchange}:{symbol}", result)
                    return result

                # --- Position Open: Update mark-to-market & evaluate exit triggers ---
                if pos.side == "LONG":
                    unrealized_pnl = (close_price - pos.entry_price) * pos.quantity
                    unrealized_pnl_pct = (
                        (unrealized_pnl / (pos.cost_basis or 1.0)) * 100.0
                        if pos.cost_basis > 0
                        else 0.0
                    )
                else:
                    unrealized_pnl = (pos.entry_price - close_price) * pos.quantity
                    unrealized_pnl_pct = (
                        (unrealized_pnl / (pos.cost_basis or 1.0)) * 100.0
                        if pos.cost_basis > 0
                        else 0.0
                    )

                pos.current_price = close_price
                pos.unrealized_pnl = round(unrealized_pnl, 2)
                pos.unrealized_pnl_pct = round(unrealized_pnl_pct, 2)

                exit_triggered = False
                exit_reason = ""
                exit_price = close_price

                # Check Stop-Loss (with adverse-gap fill logic)
                if pos.stop_loss is not None:
                    open_price = (
                        float(latest_bar["open"])
                        if "open" in latest_bar
                        else close_price
                    )
                    if pos.side == "LONG" and low_price <= pos.stop_loss:
                        exit_triggered = True
                        exit_reason = "STOP_LOSS"
                        # Gap-through: if the candle opened below stop, fill at open
                        raw_fill = min(open_price, pos.stop_loss)
                        exit_price = raw_fill * (1.0 - (params.slippage_pct / 100.0))
                    elif pos.side == "SHORT" and high_price >= pos.stop_loss:
                        exit_triggered = True
                        exit_reason = "STOP_LOSS"
                        # Gap-through: if the candle opened above stop, fill at open
                        raw_fill = max(open_price, pos.stop_loss)
                        exit_price = raw_fill * (1.0 + (params.slippage_pct / 100.0))

                # Check Take-Profit
                if (
                    not exit_triggered
                    and pos.take_profit is not None
                    and (
                        pos.side == "LONG"
                        and high_price >= pos.take_profit
                        or pos.side == "SHORT"
                        and low_price <= pos.take_profit
                    )
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

                    self.ledger.record_close_position_sync(
                        account_id=self.account_id,
                        pos=pos,
                        exit_price=exit_price,
                        fee=taker_fee,
                        notes=f"Paper Exit: {exit_reason}",
                    )

                    # Calculate actual bars held for trade history
                    bars_held_exit = 1
                    if pos.entry_time:
                        try:
                            entry_dt = pd.to_datetime(pos.entry_time, utc=True)
                            bar_dt = pd.to_datetime(bar_time, utc=True)
                            bars_held_exit = max(
                                1,
                                int(
                                    (bar_dt - entry_dt).total_seconds()
                                    / self._timeframe_seconds(timeframe)
                                ),
                            )
                        except Exception:
                            bars_held_exit = 1

                    # Log completed trade in history
                    trade_record = PaperTradeRecord(
                        trade_id=f"tr_{uuid.uuid4()}",
                        exchange=exchange,
                        symbol=symbol,
                        timeframe=timeframe,
                        side=pos.side,
                        entry_price=pos.entry_price,
                        exit_price=exit_price,
                        quantity=pos.quantity,
                        # FIX: pos.entry_time must be set on entry; no fallback to bar_time
                        entry_time=pos.entry_time,
                        exit_time=bar_time,
                        holding_period_bars=bars_held_exit,
                        realized_pnl=round(realized_pnl, 2),
                        realized_pnl_pct=round(realized_pnl_pct, 2),
                        fees=round(entry_fee + taker_fee, 2),
                        exit_reason=exit_reason,
                        params=params.to_dict(),
                    )
                    self.ledger.record_completed_trade_sync(trade_record)
                    self.ledger.close_position_sync(
                        exchange, symbol, account_id=self.account_id
                    )

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
                    self._record_evaluation(f"{exchange}:{symbol}:{timeframe}", result)
                    self._record_evaluation(f"{exchange}:{symbol}", result)
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
                            self.ledger.record_close_position_sync(
                                account_id=self.account_id,
                                pos=pos,
                                exit_price=close_price,
                                fee=taker_fee,
                                notes=f"Paper Exit: TIMEOUT ({bars_held} bars)",
                            )
                            trade_record = PaperTradeRecord(
                                trade_id=f"tr_{uuid.uuid4()}",
                                exchange=exchange,
                                symbol=symbol,
                                timeframe=timeframe,
                                side=pos.side,
                                entry_price=pos.entry_price,
                                exit_price=close_price,
                                quantity=pos.quantity,
                                # FIX: pos.entry_time must be set on entry; no fallback to bar_time
                                entry_time=pos.entry_time,
                                exit_time=bar_time,
                                holding_period_bars=bars_held,
                                realized_pnl=round(realized_pnl, 2),
                                realized_pnl_pct=round(realized_pnl_pct, 2),
                                fees=round(entry_fee + taker_fee, 2),
                                exit_reason="TIMEOUT",
                                params=params.to_dict(),
                            )
                            self.ledger.record_completed_trade_sync(trade_record)
                            self.ledger.close_position_sync(
                                exchange, symbol, account_id=self.account_id
                            )
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
                            self._record_evaluation(
                                f"{exchange}:{symbol}:{timeframe}", result
                            )
                            self._record_evaluation(f"{exchange}:{symbol}", result)
                            return result
                    except Exception as e:
                        logger.debug(f"[PaperEngine] Entry time parse failed: {e}")

                    # Trailing stop (Fix #18: activate from entry, not just in profit)
                    if pos.stop_loss is not None and pos.unrealized_pnl_pct >= 0:
                        rm = self._get_risk_manager(params)
                        side_int = 1 if pos.side == "LONG" else -1
                        pos_entry_atr = (
                            pos.metadata.get("entry_atr")
                            if isinstance(pos.metadata, dict)
                            else None
                        )
                        pos.stop_loss = rm.update_trailing_stop(
                            side_int,
                            close_price,
                            pos.stop_loss,
                            atr_val,
                            regime=regime,
                            entry_price=pos.entry_price,  # Fix #18: pass entry_price
                            entry_atr=pos_entry_atr,
                        )

                    pos.last_bar_time = bar_time  # l7v.3: market-bar checkpoint
                    self.ledger.save_position_sync(pos)
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
                    self._record_evaluation(f"{exchange}:{symbol}:{timeframe}", result)
                    self._record_evaluation(f"{exchange}:{symbol}", result)
                    return result

                else:
                    # Trailing stop (Fix #18: activate from entry, not just in profit)
                    if pos.stop_loss is not None and pos.unrealized_pnl_pct >= 0:
                        rm = self._get_risk_manager(params)
                        side_int = 1 if pos.side == "LONG" else -1
                        pos_entry_atr = (
                            pos.metadata.get("entry_atr")
                            if isinstance(pos.metadata, dict)
                            else None
                        )
                        pos.stop_loss = rm.update_trailing_stop(
                            side_int,
                            close_price,
                            pos.stop_loss,
                            atr_val,
                            regime=regime,
                            entry_price=pos.entry_price,  # Fix #18: pass entry_price
                            entry_atr=pos_entry_atr,
                        )

                    pos.last_bar_time = bar_time  # l7v.3: market-bar checkpoint
                    self.ledger.save_position_sync(pos)
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
                    self._record_evaluation(f"{exchange}:{symbol}:{timeframe}", result)
                    self._record_evaluation(f"{exchange}:{symbol}", result)
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
                    # Apply VWAP slope and volume filters before entry
                    strategy = self._get_strategy(params)
                    # Only apply filters if required columns exist in df_signals
                    required_cols = ["vwap_slope", "volume", "vol_sma"]
                    if all(col in df_signals.columns for col in required_cols):
                        slope_pass, volume_pass = strategy.evaluate_filters(df_signals)
                        if not (
                            bool(slope_pass.iloc[-1]) and bool(volume_pass.iloc[-1])
                        ):
                            should_enter = False
                            result = {
                                "exchange": exchange,
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "action": "NONE",
                                "current_price": close_price,
                                "signal": signal,
                                "timestamp": bar_time,
                                "note": "entry_blocked_by_filters",
                            }
                            self._record_evaluation(
                                f"{exchange}:{symbol}:{timeframe}", result
                            )
                            self._record_evaluation(f"{exchange}:{symbol}", result)
                            return result
                    # If required columns missing, skip filter check (default to pass)
                    # Use total equity (cash + position values) for risk-based sizing
                    total_equity = self.ledger.get_total_equity_sync(self.account_id)
                    current_balance = self.ledger.get_balance_sync(self.account_id)

                    # Determine regime from strategy signals (real classifier, not heuristic)
                    # df_signals has "regime" column from strategy.generate_signals()
                    if "regime" in df_signals.columns:
                        regime = df_signals["regime"].iloc[-1]
                        if regime == "insufficient_data":
                            regime = "trending"
                    else:
                        regime = "trending"  # fallback

                    # Risk-based sizing using profile risk_per_trade_pct
                    rm = self._get_risk_manager(params)
                    # l7v.4: Calculate stop/target from actual slipped fill price,
                    # not the raw close. We compute fill_price early here so that
                    # the SL/TP distances reflect the true entry cost.
                    slip_mult_pre = 1.0 + (
                        (params.slippage_pct / 100.0)
                        if side_int == 1
                        else -(params.slippage_pct / 100.0)
                    )
                    fill_price_estimate = close_price * slip_mult_pre
                    stop_loss, take_profit = rm.calculate_stop_and_target(
                        entry_price=fill_price_estimate,
                        side=side_int,
                        vwap=vwap_val,
                        atr=atr_val,
                        regime=regime,
                    )

                    units, pos_val, _risk_amount = rm.calculate_position_size(
                        equity=total_equity,
                        entry_price=close_price,
                        stop_loss_price=stop_loss,
                        regime=regime,
                    )

                    # Respect fixed position_size if set (non-zero). Otherwise, keep the
                    # profile's discovered risk-per-trade sizing as the primary input and
                    # apply portfolio-level cap: sum of all positions <= max_position_pct% of equity
                    if params.position_size and params.position_size > 0:
                        units = float(params.position_size)
                        pos_val = units * close_price
                    else:
                        pos_val = units * close_price

                    # Portfolio-level cap: sum of all positions <= max_position_pct% of equity
                    max_allowed_equity_pct = (
                        getattr(params, "max_position_pct", 80.0) / 100.0
                    )
                    current_position_value = sum(
                        p.cost_basis + p.unrealized_pnl
                        for p in self.ledger.list_positions_sync(
                            account_id=self.account_id
                        )
                    )
                    max_new_position_value = max(
                        0.0,
                        total_equity * max_allowed_equity_pct - current_position_value,
                    )
                    if pos_val > max_new_position_value:
                        units = max_new_position_value / max(close_price, 1e-9)
                        pos_val = units * close_price

                    if current_balance >= 10.0:
                        # Model slippage on entry
                        slip_mult = 1.0 + (
                            (params.slippage_pct / 100.0)
                            if side_int == 1
                            else -(params.slippage_pct / 100.0)
                        )
                        fill_price = close_price * slip_mult
                        entry_fee = units * fill_price * (params.taker_fee_pct / 100.0)

                        # Hard cash guardrail: never exceed 95% of free cash after fees.
                        max_affordable_units = (current_balance * 0.95) / max(
                            fill_price * (1.0 + params.taker_fee_pct / 100.0), 1e-9
                        )
                        if units > max_affordable_units > 0:
                            units = max_affordable_units
                            pos_val = units * fill_price

                        if units <= 0 or pos_val <= 0:
                            return {
                                "exchange": exchange,
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "status": "skipped",
                                "action": "NONE",
                                "reason": "position_size_capped_to_zero",
                                "timestamp": bar_time,
                            }

                        # l7v.14: Route short entries through SELL_SHORT ledger event.
                        # Long entries continue through record_buy.
                        if side_str == "SHORT":
                            cost_basis_val = units * fill_price
                            self.ledger.record_sell_short_sync(
                                account_id=self.account_id,
                                exchange=exchange,
                                symbol=symbol,
                                quantity=units,
                                price=fill_price,
                                cost_basis=cost_basis_val,
                                fee=entry_fee,
                                notes="Paper Signal Short Entry",
                            )
                        else:
                            self.ledger.record_buy_sync(
                                account_id=self.account_id,
                                exchange=exchange,
                                symbol=symbol,
                                quantity=units,
                                price=fill_price,
                                fee=entry_fee,
                                notes="Paper Signal Entry",
                            )

                        new_pos = PaperPosition(
                            account_id=self.account_id,
                            exchange=exchange,
                            symbol=symbol,
                            side=side_str,
                            entry_price=fill_price,  # l7v.10: no forced 4-decimal rounding
                            current_price=fill_price,
                            quantity=units,
                            cost_basis=round(units * fill_price, 8),
                            stop_loss=stop_loss,  # l7v.10: no forced rounding
                            take_profit=take_profit,
                            unrealized_pnl=0.0,
                            unrealized_pnl_pct=0.0,
                            entry_time=bar_time,
                            last_bar_time=bar_time,  # l7v.3: set checkpoint at entry
                            metadata={
                                "params": params.to_dict(),
                                "entry_atr": atr_val,
                            },
                        )
                        # Fix #18: Initialize trailing stop from entry (allows trailing from entry)
                        rm = self._get_risk_manager(params)
                        side_int = 1 if side_str == "LONG" else -1
                        new_pos.stop_loss = rm.update_trailing_stop(
                            side_int,
                            fill_price,
                            new_pos.stop_loss,
                            atr_val,
                            regime=regime,
                            entry_price=fill_price,  # Fix #18: pass entry_price for initial trail
                            entry_atr=atr_val,
                        )
                        self.ledger.save_position_sync(new_pos)

                        result = {
                            "exchange": exchange,
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "action": f"ENTER_{side_str}",
                            "entry_price": round(fill_price, 4),
                            "quantity": units,
                            "stop_loss": round(new_pos.stop_loss, 4),
                            "take_profit": round(take_profit, 4),
                            "timestamp": bar_time,
                        }
                        self._record_evaluation(
                            f"{exchange}:{symbol}:{timeframe}", result
                        )
                        self._record_evaluation(f"{exchange}:{symbol}", result)
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
                self._record_evaluation(f"{exchange}:{symbol}:{timeframe}", result)
                self._record_evaluation(f"{exchange}:{symbol}", result)
                return result

    def close_position_market(
        self,
        exchange: str,
        symbol: str,
        current_price: float | None = None,
    ) -> PaperTradeRecord | None:
        """Manually close an open paper position at market price."""
        pos_lock = self._get_position_lock(exchange, symbol)
        with pos_lock, self._lock:
            pos = self.ledger.get_position_sync(
                exchange, symbol, account_id=self.account_id
            )
            if not pos:
                return None

            price = current_price
            if price is None or price <= 0:
                try:
                    df_live = DataLoader.load_candles_sync(
                        exchange, symbol, timeframe="1m", limit=2
                    )
                    if not df_live.empty and "close" in df_live:
                        price = float(df_live.iloc[-1]["close"])
                except Exception:
                    price = None
            if price is None or price <= 0:
                price = pos.current_price or pos.entry_price

            params = self.registry.get_strategy_params_sync(exchange, symbol)
            exit_fee = pos.quantity * price * (params.taker_fee_pct / 100.0)
            # l7v.1: include entry fee for consistent lifecycle PnL accounting
            entry_fee = pos.quantity * pos.entry_price * (params.taker_fee_pct / 100.0)

            if pos.side == "LONG":
                realized_pnl = (
                    (price - pos.entry_price) * pos.quantity - entry_fee - exit_fee
                )
            else:
                realized_pnl = (
                    (pos.entry_price - price) * pos.quantity - entry_fee - exit_fee
                )

            realized_pnl_pct = (realized_pnl / (pos.cost_basis or 1.0)) * 100.0
            now_iso = datetime.now(UTC).isoformat()

            # l7v.10: derive actual bars held from entry_time
            bars_held = 0
            if pos.entry_time:
                try:
                    profile = self.registry.get_profile_sync(exchange, symbol)
                    tf = profile.timeframe if profile else "5m"
                    entry_dt = pd.to_datetime(pos.entry_time, utc=True)
                    now_dt = pd.to_datetime(now_iso, utc=True)
                    bars_held = max(
                        0,
                        int(
                            (now_dt - entry_dt).total_seconds()
                            / self._timeframe_seconds(tf)
                        ),
                    )
                except Exception:
                    bars_held = 0

            self.ledger.record_close_position_sync(
                account_id=self.account_id,
                pos=pos,
                exit_price=price,
                fee=exit_fee,
                notes="Manual Market Close",
            )

            trade = PaperTradeRecord(
                trade_id=f"tr_{uuid.uuid4()}",
                exchange=exchange,
                symbol=symbol,
                timeframe="manual",
                side=pos.side,
                entry_price=pos.entry_price,
                exit_price=price,
                quantity=pos.quantity,
                entry_time=pos.entry_time or now_iso,
                exit_time=now_iso,
                holding_period_bars=bars_held,
                realized_pnl=round(realized_pnl, 2),
                realized_pnl_pct=round(realized_pnl_pct, 2),
                fees=round(entry_fee + exit_fee, 2),
                exit_reason="MANUAL",
                params=params.to_dict(),
            )
            self.ledger.record_completed_trade_sync(trade)
            self.ledger.close_position_sync(
                exchange, symbol, account_id=self.account_id
            )
            return trade

    def step_all_active_profiles(self) -> list[dict[str, Any]]:
        """Evaluate all active paper trading profiles with live candle fetching and snapshot reuse.

        Lock granularity (ema_vwap-skxp): hold _poll_cycle_lock only for state checks,
        not during the full profile evaluation loop.
        """
        # Quick state check with lock
        with self._poll_cycle_lock:
            if self._poll_cycle_active:
                logger.warning(
                    "[PaperTradingEngine] Polling cycle already in progress, skipping overlapping run."
                )
                return []
            self._poll_cycle_active = True

        start_perf = time.perf_counter()
        try:
            profiles = self.registry.list_profiles_sync(active_only=True)
            results = []

            candle_snapshots: dict[tuple[str, str, str], pd.DataFrame] = {}
            signal_snapshots: dict[tuple[str, str, str, str], pd.DataFrame | None] = {}

            provider_fetches = 0
            indicator_computations = 0
            cached_candle_hits = 0
            cached_signal_hits = 0

            for p in profiles:
                try:
                    candle_key = (p.exchange, p.symbol, p.timeframe)
                    if candle_key in candle_snapshots:
                        df_candles = candle_snapshots[candle_key]
                        cached_candle_hits += 1
                    else:
                        df_candles = DataLoader.fetch_and_update_candles_sync(
                            exchange=p.exchange,
                            symbol=p.symbol,
                            timeframe=p.timeframe,
                            limit=200,
                        )
                        candle_snapshots[candle_key] = df_candles
                        provider_fetches += 1

                    params = self.registry.get_strategy_params_sync(
                        exchange=p.exchange,
                        symbol=p.symbol,
                        timeframe=p.timeframe,
                    )
                    params_key = json.dumps(params.to_dict(), sort_keys=True)
                    signal_key = (p.exchange, p.symbol, p.timeframe, params_key)

                    if signal_key in signal_snapshots:
                        df_signals = signal_snapshots[signal_key]
                        cached_signal_hits += 1
                    else:
                        if not df_candles.empty and len(df_candles) >= 20:
                            strategy = self._get_strategy(params)
                            df_signals = strategy.generate_signals(df_candles)
                        else:
                            df_signals = None
                        signal_snapshots[signal_key] = df_signals
                        indicator_computations += 1

                    res = self.evaluate_symbol(
                        exchange=p.exchange,
                        symbol=p.symbol,
                        timeframe=p.timeframe,
                        force_candles=df_candles,
                        force_signals=df_signals,
                        fetch_live=False,
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

            latency_ms = (time.perf_counter() - start_perf) * 1000.0
            self._last_poll_time = datetime.now(UTC).isoformat()
            self._last_poll_metrics = {
                "poll_latency_ms": round(latency_ms, 2),
                "active_profiles_count": len(profiles),
                "provider_fetches": provider_fetches,
                "indicator_computations": indicator_computations,
                "cached_candle_hits": cached_candle_hits,
                "cached_signal_hits": cached_signal_hits,
                "timestamp": self._last_poll_time,
            }
            logger.debug(
                f"[PaperTradingEngine] Poll finished in {latency_ms:.1f}ms for {len(profiles)} profiles "
                f"({provider_fetches} provider fetches, {indicator_computations} indicator runs, "
                f"{cached_candle_hits} candle hits, {cached_signal_hits} signal hits)"
            )
            return results
        finally:
            with self._poll_cycle_lock:
                self._poll_cycle_active = False

    def reconcile_positions_on_startup(
        self, force_candles_map: dict[str, pd.DataFrame] | None = None
    ) -> list[dict[str, Any]]:
        """
        Audit all open positions against historical candles spanning the downtime gap.
        Evaluates chronologically bar-by-bar if Stop-Loss or Take-Profit levels were breached
        while the server was offline, executing backfilled fills with historical timestamps.
        """
        open_positions = self.ledger.list_positions_sync(account_id=self.account_id)
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
            profile = self.registry.get_profile_sync(exchange, symbol)
            timeframe = profile.timeframe if profile else "5m"
            if profile:
                params = profile.get_strategy_params()
            elif pos.metadata and "params" in pos.metadata:
                params = StrategyParams.from_dict(pos.metadata["params"])
            else:
                params = StrategyParams()

            # Obtain historical candles.
            # Fetch limit (ema_vwap-u94s): derive from downtime span so long gaps
            # are fully covered — previously hardcoded limit=300 could miss SL/TP
            # breaches for positions open across extended downtime.
            if force_candles_map and key in force_candles_map:
                df_candles = force_candles_map[key]
            else:
                try:
                    from src.config import get_bars_per_day

                    try:
                        opened_at = pd.to_datetime(
                            pos.entry_time or pos.last_bar_time, utc=True
                        )
                    except Exception:
                        opened_at = None
                    if opened_at is not None and not pd.isna(opened_at):
                        elapsed_days = max(
                            0.0,
                            (pd.Timestamp.now(tz="UTC") - opened_at).total_seconds()
                            / 86400.0,
                        )
                        bars_per_day = get_bars_per_day(timeframe)
                        # +1 day buffer, floor at 300 (previous behavior), cap 50k
                        reconcile_limit = int(
                            min(50000, max(300, (elapsed_days + 1.0) * bars_per_day))
                        )
                    else:
                        reconcile_limit = 300

                    df_candles = DataLoader.load_candles_sync(
                        exchange=exchange,
                        symbol=symbol,
                        timeframe=timeframe,
                        limit=reconcile_limit,
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

            # Ensure indicators and point-in-time regime are computed for trailing stop replay
            try:
                strategy = self._get_strategy(params)
                df_signals = strategy.generate_signals(df_candles)
            except Exception as e:
                logger.debug(
                    f"[PaperTradingEngine] Indicator generation failed during reconcile: {e}"
                )
                df_signals = df_candles

            # l7v.3: Use last_bar_time (durable market checkpoint) not wall-clock updated_time.
            # Falls back to entry_time if no bar has been processed yet.
            checkpoint_str = pos.last_bar_time or pos.entry_time
            if checkpoint_str:
                try:
                    checkpoint_dt = pd.to_datetime(checkpoint_str, utc=True)
                    # Strictly AFTER the checkpoint — don't replay the last processed bar
                    missed_candles = df_signals[df_signals["timestamp"] > checkpoint_dt]
                    if missed_candles.empty:
                        # No new bars since checkpoint — nothing to reconcile
                        logger.info(
                            f"[PaperTradingEngine] {key}: no new candles since checkpoint {checkpoint_str}, skipping."
                        )
                        continue
                except Exception:
                    missed_candles = df_signals.tail(10)
            else:
                missed_candles = df_signals.tail(10)

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

                # Check Stop-Loss breach (with gap-through fill logic)
                if pos.stop_loss is not None:
                    open_price = float(row["open"]) if "open" in row else close_price
                    if pos.side == "LONG" and low_price <= pos.stop_loss:
                        exit_triggered = True
                        exit_reason = "STOP_LOSS"
                        # Gap-through: fill at open when open gapped below stop
                        raw_fill = min(open_price, pos.stop_loss)
                        slip_mult = 1.0 - (params.slippage_pct / 100.0)
                        exit_price = raw_fill * slip_mult
                    elif pos.side == "SHORT" and high_price >= pos.stop_loss:
                        exit_triggered = True
                        exit_reason = "STOP_LOSS"
                        # Gap-through: fill at open when open gapped above stop
                        raw_fill = max(open_price, pos.stop_loss)
                        slip_mult = 1.0 + (params.slippage_pct / 100.0)
                        exit_price = raw_fill * slip_mult

                # Check Take-Profit breach
                if (
                    not exit_triggered
                    and pos.take_profit is not None
                    and (
                        pos.side == "LONG"
                        and high_price >= pos.take_profit
                        or pos.side == "SHORT"
                        and low_price <= pos.take_profit
                    )
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
                        bars_held_rec = int(
                            (bar_dt - entry_dt).total_seconds()
                            / self._timeframe_seconds(timeframe)
                        )
                        if bars_held_rec >= params.max_holding_bars:
                            exit_triggered = True
                            exit_reason = "TIMEOUT"
                            exit_price = close_price
                    except Exception as e:
                        logger.debug(f"[PaperEngine] Timeout exit parse failed: {e}")

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

                    self.ledger.record_close_position_sync(
                        account_id=self.account_id,
                        pos=pos,
                        exit_price=exit_price,
                        fee=taker_fee,
                        notes=f"Paper Reconcile Exit: {exit_reason} (at {bar_time})",
                    )

                    # l7v.10: compute actual bars held from entry time
                    bars_held_rec = 1
                    if pos.entry_time:
                        try:
                            entry_dt = pd.to_datetime(pos.entry_time, utc=True)
                            bar_dt = pd.to_datetime(bar_time, utc=True)
                            bars_held_rec = max(
                                1,
                                int(
                                    (bar_dt - entry_dt).total_seconds()
                                    / self._timeframe_seconds(timeframe)
                                ),
                            )
                        except Exception:
                            bars_held_rec = 1

                    trade_record = PaperTradeRecord(
                        trade_id=f"tr_{uuid.uuid4()}",
                        exchange=exchange,
                        symbol=symbol,
                        timeframe=timeframe,
                        side=pos.side,
                        entry_price=pos.entry_price,
                        exit_price=exit_price,
                        quantity=pos.quantity,
                        # FIX: pos.entry_time must be set on entry; no fallback to bar_time
                        entry_time=pos.entry_time,
                        exit_time=bar_time,
                        holding_period_bars=bars_held_rec,
                        realized_pnl=round(realized_pnl, 2),
                        realized_pnl_pct=round(realized_pnl_pct, 2),
                        fees=round(entry_fee + taker_fee, 2),
                        exit_reason=exit_reason,
                        params=params.to_dict(),
                    )
                    self.ledger.record_completed_trade_sync(trade_record)
                    self.ledger.close_position_sync(
                        exchange, symbol, account_id=self.account_id
                    )

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

                # Trailing stop replay across missed bar
                if pos.side == "LONG":
                    cur_unrealized_pnl = (close_price - pos.entry_price) * pos.quantity
                else:
                    cur_unrealized_pnl = (pos.entry_price - close_price) * pos.quantity
                cur_unrealized_pct = (
                    cur_unrealized_pnl / (pos.cost_basis or 1.0)
                ) * 100.0

                if pos.stop_loss is not None and cur_unrealized_pct >= 0:
                    bar_atr = (
                        float(row["atr"])
                        if "atr" in row and pd.notna(row["atr"])
                        else 0.0
                    )
                    bar_regime = (
                        str(row["regime"])
                        if "regime" in row and pd.notna(row["regime"])
                        else "trending"
                    )
                    if bar_regime == "insufficient_data":
                        bar_regime = "trending"

                    if bar_atr > 0:
                        rm = self._get_risk_manager(params)
                        side_int = 1 if pos.side == "LONG" else -1
                        pos_entry_atr = (
                            pos.metadata.get("entry_atr")
                            if isinstance(pos.metadata, dict)
                            else None
                        )
                        pos.stop_loss = rm.update_trailing_stop(
                            side=side_int,
                            current_price=close_price,
                            current_stop=pos.stop_loss,
                            atr=bar_atr,
                            regime=bar_regime,
                            entry_price=pos.entry_price,
                            entry_atr=pos_entry_atr,
                        )

            if not position_closed and len(missed_candles) > 0:
                latest_close = float(missed_candles.iloc[-1]["close"])
                if pos.side == "LONG":
                    unrealized_pnl = (latest_close - pos.entry_price) * pos.quantity
                else:
                    unrealized_pnl = (pos.entry_price - latest_close) * pos.quantity
                unrealized_pnl_pct = (unrealized_pnl / (pos.cost_basis or 1.0)) * 100.0

                pos.current_price = latest_close
                pos.unrealized_pnl = round(unrealized_pnl, 2)
                pos.unrealized_pnl_pct = round(unrealized_pnl_pct, 2)
                # l7v.3: store the last bar's market time as the durable checkpoint
                pos.last_bar_time = last_checked_bar_time or None
                self.ledger.save_position_sync(pos)

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

        if persist and self.settings_mgr is not None:
            self.settings_mgr.set("paper_runner_enabled", "true", is_secret=False)
            self.settings_mgr.set(
                "paper_polling_interval", str(interval_seconds), is_secret=False
            )

        def _poll_loop():
            thread_name = threading.current_thread().name
            logger.info(
                f"[PaperTradingEngine] Started live polling runner (interval={interval_seconds}s) on thread {thread_name}"
            )
            consecutive_errors = 0
            circuit_open = False
            CIRCUIT_PAUSE_S = 60  # seconds to wait when circuit-breaker trips
            last_health_log = time.time()
            HEALTH_LOG_INTERVAL = 30  # seconds
            while not self._stop_event.is_set():
                try:
                    # Offload CPU-heavy polling work to thread pool to avoid
                    # blocking the main event loop (which must stay responsive
                    # for API requests and WebSocket messages).
                    # Create a new event loop for this thread to run the async function.
                    loop = asyncio.new_event_loop()
                    try:
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(
                            asyncio.to_thread(self.step_all_active_profiles)
                        )
                    finally:
                        loop.close()
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
                except BaseException as e:
                    # Catch BaseException (KeyboardInterrupt, SystemExit, GeneratorExit)
                    # to prevent silent thread death. Log and re-raise so the thread
                    # exits cleanly and _running is corrected.
                    logger.critical(
                        f"[PaperTradingEngine] Polling thread {thread_name} received {type(e).__name__}: {e}"
                    )
                    self._running = False
                    raise
                # Periodic health log so we can confirm thread is alive in prod logs
                now = time.time()
                if now - last_health_log >= HEALTH_LOG_INTERVAL:
                    logger.debug(
                        f"[PaperTradingEngine] Polling heartbeat — running={self._running}, "
                        f"consecutive_errors={consecutive_errors}, circuit_open={circuit_open}"
                    )
                    last_health_log = now
                self._stop_event.wait(interval_seconds)
            logger.info(
                f"[PaperTradingEngine] Polling runner stopped on thread {thread_name}."
            )

        self._poll_thread = threading.Thread(
            target=_poll_loop, name="PaperTradingRunner", daemon=True
        )
        self._poll_thread.start()
        return True

    def stop_polling(self, persist: bool = True) -> bool:
        """Stop the background live polling loop."""
        if not self._running:
            if persist and self.settings_mgr is not None:
                self.settings_mgr.set("paper_runner_enabled", "false", is_secret=False)
            return False

        self._running = False
        self._stop_event.set()
        if persist and self.settings_mgr is not None:
            self.settings_mgr.set("paper_runner_enabled", "false", is_secret=False)
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)
        self._poll_thread = None
        return True

    async def get_status(self) -> dict[str, Any]:
        """Get live runner status and summary (async, safe from main event loop)."""
        profiles = await self.registry.list_profiles(active_only=True)
        positions = await self.ledger.list_positions(account_id=self.account_id)
        balance = await self.ledger.get_balance(account_id=self.account_id)
        stats = await self.ledger.get_statistics()

        unrealized_total = sum(p.unrealized_pnl for p in positions)
        positions_market_value = sum(p.cost_basis + p.unrealized_pnl for p in positions)
        total_equity = balance + positions_market_value

        with self._eval_lock:
            self._prune_evaluations_locked()
            evaluations_copy = dict(self._last_evaluations)

        return {
            "is_running": self._running,
            "account_id": self.account_id,
            "cash_balance": round(balance, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(unrealized_total, 2),
            "active_profiles_count": len(profiles),
            "open_positions_count": len(positions),
            "last_poll_time": self._last_poll_time,
            "last_evaluations": evaluations_copy,
            "last_poll_metrics": dict(self._last_poll_metrics),
            "performance": stats,
        }

    def get_balance_snapshot(self) -> dict[str, Any]:
        """Get current paper trading balance snapshot for portfolio aggregation."""
        self.registry.list_profiles_sync(active_only=True)
        positions = self.ledger.list_positions_sync(account_id=self.account_id)
        balance = self.ledger.get_balance_sync(self.account_id)
        self.ledger.get_statistics_sync()

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
        pos = self.ledger.get_position_sync(
            exchange, symbol, account_id=self.account_id
        )
        if not pos:
            raise ValueError(f"No open position found for {exchange}:{symbol}")

        if stop_loss is not None:
            pos.stop_loss = round(stop_loss, 4) if stop_loss > 0 else None
        if take_profit is not None:
            pos.take_profit = round(take_profit, 4) if take_profit > 0 else None

        self.ledger.save_position_sync(pos)
        return pos.to_dict()

    def reset_state(self, account_id: str = "default") -> None:
        """Reset in-memory engine evaluations, position locks, and polling metrics for an account."""
        with self._lock, self._locks_mutex:
            with self._eval_lock:
                self._last_evaluations.clear()
            self._position_locks.clear()
            self._last_poll_time = None
            self._consecutive_errors = 0
            self._last_poll_metrics = {
                "active_profiles": 0,
                "cached_candle_hits": 0,
                "cached_signal_hits": 0,
                "provider_fetches": 0,
                "indicator_computations": 0,
                "duration_seconds": 0.0,
            }
        logger.info(
            f"[PaperTradingEngine] In-memory engine state reset for account '{account_id}'."
        )


# Global paper trading engine singleton
paper_engine = PaperTradingEngine()
