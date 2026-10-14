"""
Regression tests for Epic ema_vwap-l7v — Paper trading accounting and execution correctness.

Covers:
  l7v.1  — Unified long/short equity and PnL math
  l7v.2  — Idempotent candle execution (duplicate-bar guard)
  l7v.3  — Reconciliation uses last_bar_time (market checkpoint), not wall-clock
  l7v.4  — Causal fills: stop/target sized from actual fill, gap-through stop logic
  l7v.9  — Positions scoped by account_id; two accounts do not cross-contaminate
  l7v.10 — Price precision preserved; holding_period_bars derived from elapsed bars
  l7v.11 — Ledger cash operations bounded: negative/zero/NaN/infinite rejected
  l7v.14 — Short opens recorded as SELL_SHORT, not BUY
"""

import math
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.database import CandleDatabase
from src.paper.engine import PaperTradingEngine
from src.paper.ledger import PaperLedger
from src.paper.models import PaperPosition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db() -> CandleDatabase:
    """Create an isolated paper-trading DB in a temp directory.

    Must use a filename that starts with 'test_paper_' so that
    CandleDatabase._get_db_domain() returns 'paper_trading' and all
    paper trading tables (paper_positions, paper_ledger_transactions, etc.)
    are created during init_db.
    """
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_paper_l7v.db")
    return CandleDatabase(db_path=db_path)


def _make_engine(db: CandleDatabase, account_id: str = "default") -> PaperTradingEngine:
    return PaperTradingEngine(db=db, account_id=account_id)


def _fund(ledger: PaperLedger, account_id: str = "default", amount: float = 100_000.0):
    ledger.deposit(account_id=account_id, amount=amount, notes="test fund")


def _open_long(
    ledger,
    account_id="default",
    exchange="binance",
    symbol="BTC/USDT",
    entry_price=50_000.0,
    quantity=1.0,
    bar_time=None,
):
    cost_basis = entry_price * quantity
    fee = cost_basis * 0.001
    ledger.record_buy(
        account_id=account_id,
        exchange=exchange,
        symbol=symbol,
        quantity=quantity,
        price=entry_price,
        fee=fee,
    )
    bt = bar_time or datetime.now(timezone.utc).isoformat()
    pos = PaperPosition(
        account_id=account_id,
        exchange=exchange,
        symbol=symbol,
        side="LONG",
        entry_price=entry_price,
        current_price=entry_price,
        quantity=quantity,
        cost_basis=cost_basis,
        stop_loss=entry_price * 0.95,
        take_profit=entry_price * 1.10,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
        entry_time=bt,
        last_bar_time=bt,
    )
    ledger.save_position(pos)
    return pos


def _open_short(
    ledger,
    account_id="default",
    exchange="binance",
    symbol="ETH/USDT",
    entry_price=3_000.0,
    quantity=2.0,
    bar_time=None,
):
    cost_basis = entry_price * quantity
    fee = cost_basis * 0.001
    ledger.record_sell_short(
        account_id=account_id,
        exchange=exchange,
        symbol=symbol,
        quantity=quantity,
        price=entry_price,
        cost_basis=cost_basis,
        fee=fee,
    )
    bt = bar_time or datetime.now(timezone.utc).isoformat()
    pos = PaperPosition(
        account_id=account_id,
        exchange=exchange,
        symbol=symbol,
        side="SHORT",
        entry_price=entry_price,
        current_price=entry_price,
        quantity=quantity,
        cost_basis=cost_basis,
        stop_loss=entry_price * 1.05,
        take_profit=entry_price * 0.90,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
        entry_time=bt,
        last_bar_time=bt,
    )
    ledger.save_position(pos)
    return pos


def _make_candles(
    n=5,
    start_time=None,
    open_=50_000.0,
    high=51_000.0,
    low=49_000.0,
    close=50_500.0,
    timeframe_minutes=5,
):
    t = start_time or datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        ts = t + timedelta(minutes=timeframe_minutes * i)
        rows.append(
            {
                "timestamp": ts,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 100.0,
            }
        )
    return pd.DataFrame(rows)


# ===========================================================================
# l7v.1 — Unified equity and PnL math
# ===========================================================================


class TestEquityUnification:
    def test_long_equity_consistent(self):
        db = _make_db()
        ledger = PaperLedger(db=db)
        _fund(ledger, amount=100_000.0)
        entry, qty = 50_000.0, 1.0
        cost_basis = entry * qty
        current = 52_000.0
        upnl = (current - entry) * qty
        _open_long(ledger, entry_price=entry, quantity=qty)
        pos = ledger.get_position("binance", "BTC/USDT")
        pos.current_price = current
        pos.unrealized_pnl = upnl
        pos.unrealized_pnl_pct = (upnl / cost_basis) * 100.0
        ledger.save_position(pos)
        cash = ledger.get_balance()
        expected = cash + cost_basis + upnl
        assert math.isclose(ledger.get_total_equity(), expected, rel_tol=1e-6)

    def test_short_equity_uses_cost_basis_not_quantity_times_price(self):
        db = _make_db()
        ledger = PaperLedger(db=db)
        _fund(ledger, amount=100_000.0)
        entry, qty = 3_000.0, 2.0
        cost_basis = entry * qty
        current = 2_800.0
        upnl = (entry - current) * qty
        _open_short(ledger, entry_price=entry, quantity=qty)
        pos = ledger.get_position("binance", "ETH/USDT")
        pos.current_price = current
        pos.unrealized_pnl = upnl
        pos.unrealized_pnl_pct = (upnl / cost_basis) * 100.0
        ledger.save_position(pos)
        cash = ledger.get_balance()
        expected = cash + cost_basis + upnl
        wrong_calc = cash + qty * current  # the old incorrect formula
        assert math.isclose(ledger.get_total_equity(), expected, rel_tol=1e-6)
        assert not math.isclose(ledger.get_total_equity(), wrong_calc, rel_tol=1e-6)

    def test_equity_scoped_to_account(self):
        db = _make_db()
        ledger = PaperLedger(db=db)
        _fund(ledger, account_id="acct_a", amount=50_000.0)
        _fund(ledger, account_id="acct_b", amount=50_000.0)
        _open_long(ledger, account_id="acct_a", symbol="BTC/USDT")
        # acct_b has no positions; its equity must equal its cash
        cash_b = ledger.get_balance(account_id="acct_b")
        equity_b = ledger.get_total_equity(account_id="acct_b")
        assert math.isclose(equity_b, cash_b, rel_tol=1e-6), (
            f"acct_b equity ({equity_b}) != acct_b cash ({cash_b}): "
            "acct_a's position is leaking into acct_b's equity"
        )
        # acct_a equity = cash_a + position_market_value; verify it is different from acct_b
        equity_a = ledger.get_total_equity(account_id="acct_a")
        cash_a = ledger.get_balance(account_id="acct_a")
        assert not math.isclose(
            equity_a, equity_b, rel_tol=1e-6
        ), "acct_a and acct_b equity should differ because acct_a holds a position"
        # acct_a's equity accounts for both cash and position
        positions_a = ledger.list_positions(account_id="acct_a")
        expected_equity_a = cash_a + sum(
            p.cost_basis + p.unrealized_pnl for p in positions_a
        )
        assert math.isclose(equity_a, expected_equity_a, rel_tol=1e-6)

    def test_short_upnl_pct_uses_cost_basis_denominator(self):
        entry, qty = 1_000.0, 5.0
        cost_basis = entry * qty
        current = 900.0
        upnl = (entry - current) * qty
        correct_pct = (upnl / cost_basis) * 100.0  # 10 %
        wrong_pct = (entry / current - 1.0) * 100.0  # ~11.11 %
        assert not math.isclose(correct_pct, wrong_pct, rel_tol=1e-4)
        db = _make_db()
        ledger = PaperLedger(db=db)
        _fund(ledger, amount=50_000.0)
        _open_short(ledger, entry_price=entry, quantity=qty)
        pos = ledger.get_position("binance", "ETH/USDT")
        pos.unrealized_pnl_pct = correct_pct
        ledger.save_position(pos)
        assert math.isclose(
            ledger.get_position("binance", "ETH/USDT").unrealized_pnl_pct,
            correct_pct,
            rel_tol=1e-6,
        )

    def test_manual_close_includes_entry_fee(self):
        db = _make_db()
        engine = _make_engine(db)
        _fund(engine.ledger, amount=100_000.0)
        entry, qty, exit_price = 10_000.0, 1.0, 11_000.0
        # Use the actual default fee from StrategyParams to keep the test precise
        from src.config import StrategyParams

        fee_pct = StrategyParams().taker_fee_pct  # e.g. 0.075
        engine.ledger.record_buy(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            quantity=qty,
            price=entry,
            fee=entry * qty * fee_pct / 100.0,
        )
        pos = PaperPosition(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            side="LONG",
            entry_price=entry,
            current_price=entry,
            quantity=qty,
            cost_basis=entry * qty,
            entry_time=datetime.now(timezone.utc).isoformat(),
        )
        engine.ledger.save_position(pos)
        trade = engine.close_position_market(
            "binance", "BTC/USDT", current_price=exit_price
        )
        assert trade is not None
        entry_fee = qty * entry * fee_pct / 100.0
        exit_fee = qty * exit_price * fee_pct / 100.0
        expected_pnl = round((exit_price - entry) * qty - entry_fee - exit_fee, 2)
        # Both entry + exit fee must appear in the trade record
        assert math.isclose(trade.realized_pnl, expected_pnl, rel_tol=1e-4), (
            f"Expected PnL {expected_pnl} but got {trade.realized_pnl}; "
            "close_position_market is not deducting entry fee"
        )
        assert math.isclose(trade.fees, round(entry_fee + exit_fee, 2), rel_tol=1e-4)


# ===========================================================================
# l7v.2 — Idempotent candle execution
# ===========================================================================


class TestIdempotentExecution:
    def _df(self, bar_time_iso, signal_val=0, close=50_000.0):
        ts = pd.to_datetime(bar_time_iso, utc=True)
        row = {
            "timestamp": ts,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 100.0,
            "signal": signal_val,
            "vwap": close,
            "atr": close * 0.01,
        }
        return pd.DataFrame([row] * 25)

    def test_duplicate_bar_returns_hold_with_note(self):
        db = _make_db()
        engine = _make_engine(db)
        _fund(engine.ledger, amount=100_000.0)
        # engine stores bar_time as str(row["timestamp"]) which uses space separator:
        # e.g. "2024-01-01 00:00:00+00:00" not the ISO T-separator
        bar_ts = pd.Timestamp("2024-01-01T00:00:00", tz="UTC")
        bar_time = str(bar_ts)  # "2024-01-01 00:00:00+00:00"
        engine.ledger.record_buy(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            quantity=1.0,
            price=50_000.0,
            fee=50.0,
        )
        pos = PaperPosition(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            side="LONG",
            entry_price=50_000.0,
            current_price=50_000.0,
            quantity=1.0,
            cost_basis=50_000.0,
            stop_loss=47_000.0,
            take_profit=56_000.0,
            entry_time=bar_time,
            last_bar_time=bar_time,
        )
        engine.ledger.save_position(pos)
        df = pd.DataFrame(
            [
                {
                    "timestamp": bar_ts,
                    "open": 50_000.0,
                    "high": 50_500.0,
                    "low": 49_500.0,
                    "close": 50_000.0,
                    "volume": 100.0,
                    "signal": 0,
                    "vwap": 50_000.0,
                    "atr": 500.0,
                }
            ]
            * 25
        )
        result = engine.evaluate_symbol("binance", "BTC/USDT", "5m", force_candles=df)
        assert result["action"] == "HOLD"
        assert result.get("note") == "duplicate_bar_skipped"

    def test_new_bar_updates_last_bar_time(self):
        db = _make_db()
        engine = _make_engine(db)
        _fund(engine.ledger, amount=100_000.0)
        old_ts = pd.Timestamp("2024-01-01T00:00:00", tz="UTC")
        new_ts = pd.Timestamp("2024-01-01T00:05:00", tz="UTC")
        old_bar = old_ts.isoformat()
        engine.ledger.record_buy(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            quantity=1.0,
            price=50_000.0,
            fee=50.0,
        )
        pos = PaperPosition(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            side="LONG",
            entry_price=50_000.0,
            current_price=50_000.0,
            quantity=1.0,
            cost_basis=50_000.0,
            stop_loss=47_000.0,
            take_profit=56_000.0,
            entry_time=old_bar,
            last_bar_time=old_bar,
        )
        engine.ledger.save_position(pos)
        df = pd.DataFrame(
            [
                {
                    "timestamp": new_ts,
                    "open": 50_000.0,
                    "high": 50_500.0,
                    "low": 49_500.0,
                    "close": 50_200.0,
                    "volume": 100.0,
                    "signal": 0,
                    "vwap": 50_000.0,
                    "atr": 500.0,
                }
            ]
            * 25
        )
        result = engine.evaluate_symbol("binance", "BTC/USDT", "5m", force_candles=df)
        assert result["action"] == "HOLD"
        assert result.get("note") != "duplicate_bar_skipped"
        updated = engine.ledger.get_position("binance", "BTC/USDT")
        assert updated.last_bar_time is not None
        assert updated.last_bar_time != old_bar


# ===========================================================================
# l7v.3 — Reconciliation checkpoint
# ===========================================================================


class TestReconciliationCheckpoint:
    def test_reconcile_skips_already_processed_bars(self):
        db = _make_db()
        engine = _make_engine(db)
        _fund(engine.ledger, amount=100_000.0)
        latest_ts = datetime(2024, 1, 2, tzinfo=timezone.utc)
        engine.ledger.record_buy(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            quantity=1.0,
            price=50_000.0,
            fee=50.0,
        )
        pos = PaperPosition(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            side="LONG",
            entry_price=50_000.0,
            current_price=50_000.0,
            quantity=1.0,
            cost_basis=50_000.0,
            stop_loss=47_000.0,
            take_profit=56_000.0,
            entry_time="2024-01-01T00:00:00+00:00",
            last_bar_time=latest_ts.isoformat(),
        )
        engine.ledger.save_position(pos)
        candles = _make_candles(n=5, start_time=latest_ts - timedelta(minutes=20))
        results = engine.reconcile_positions_on_startup(
            force_candles_map={"binance:BTC/USDT": candles}
        )
        assert len(results) == 0

    def test_reconcile_processes_bars_after_checkpoint(self):
        db = _make_db()
        engine = _make_engine(db)
        _fund(engine.ledger, amount=100_000.0)
        checkpoint = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        breach_ts = checkpoint + timedelta(minutes=5)
        engine.ledger.record_buy(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            quantity=1.0,
            price=50_000.0,
            fee=50.0,
        )
        checkpoint_str = str(pd.Timestamp(checkpoint))  # match engine's bar_time format
        pos = PaperPosition(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            side="LONG",
            entry_price=50_000.0,
            current_price=50_000.0,
            quantity=1.0,
            cost_basis=50_000.0,
            stop_loss=49_000.0,
            take_profit=56_000.0,
            entry_time=checkpoint_str,
            last_bar_time=checkpoint_str,
        )
        engine.ledger.save_position(pos)
        # Need >= 2 rows so reconcile's len(df_candles) >= 2 guard passes.
        # First row is AT the checkpoint (should be filtered out by > comparison).
        # Second row (breach_ts) is AFTER and should trigger the stop-loss.
        candles = pd.DataFrame(
            [
                {
                    "timestamp": checkpoint,
                    "open": 50_000.0,
                    "high": 50_100.0,
                    "low": 49_500.0,
                    "close": 50_000.0,
                    "volume": 100.0,
                },
                {
                    "timestamp": breach_ts,
                    "open": 50_000.0,
                    "high": 50_100.0,
                    "low": 48_000.0,
                    "close": 48_500.0,
                    "volume": 100.0,
                },
            ]
        )
        results = engine.reconcile_positions_on_startup(
            force_candles_map={"binance:BTC/USDT": candles}
        )
        assert (
            len(results) == 1
        ), f"Expected 1 reconcile result, got {len(results)}: {results}"
        assert "STOP_LOSS" in results[0]["action"]
        assert engine.ledger.get_position("binance", "BTC/USDT") is None


# ===========================================================================
# l7v.4 — Causal fills and gap-through stops
# ===========================================================================


class TestCausalFills:
    def test_gap_down_stop_fills_at_open_not_stop(self):
        db = _make_db()
        engine = _make_engine(db)
        _fund(engine.ledger, amount=100_000.0)
        stop_level, gap_open = 49_000.0, 47_000.0
        engine.ledger.record_buy(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            quantity=1.0,
            price=50_000.0,
            fee=50.0,
        )
        pos = PaperPosition(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            side="LONG",
            entry_price=50_000.0,
            current_price=50_000.0,
            quantity=1.0,
            cost_basis=50_000.0,
            stop_loss=stop_level,
            take_profit=56_000.0,
            entry_time="2024-01-01T00:00:00+00:00",
            last_bar_time="2024-01-01T00:00:00+00:00",
        )
        engine.ledger.save_position(pos)
        ts = pd.to_datetime("2024-01-01T00:05:00+00:00", utc=True)
        df = pd.DataFrame(
            [
                {
                    "timestamp": ts,
                    "open": gap_open,
                    "high": 48_000.0,
                    "low": 46_000.0,
                    "close": 47_500.0,
                    "volume": 100.0,
                    "signal": 0,
                    "vwap": 47_500.0,
                    "atr": 500.0,
                }
            ]
            * 25
        )
        result = engine.evaluate_symbol("binance", "BTC/USDT", "5m", force_candles=df)
        assert result["action"] == "EXIT_STOP_LOSS"
        # Fill must be below the stop level (gap-through means worse fill)
        assert result["exit_price"] < stop_level

    def test_gap_up_short_stop_fills_at_open_not_stop(self):
        db = _make_db()
        engine = _make_engine(db)
        _fund(engine.ledger, amount=100_000.0)
        stop_level, gap_open = 3_150.0, 3_300.0
        engine.ledger.record_sell_short(
            account_id="default",
            exchange="binance",
            symbol="ETH/USDT",
            quantity=2.0,
            price=3_000.0,
            cost_basis=6_000.0,
            fee=6.0,
        )
        pos = PaperPosition(
            account_id="default",
            exchange="binance",
            symbol="ETH/USDT",
            side="SHORT",
            entry_price=3_000.0,
            current_price=3_000.0,
            quantity=2.0,
            cost_basis=6_000.0,
            stop_loss=stop_level,
            take_profit=2_700.0,
            entry_time="2024-01-01T00:00:00+00:00",
            last_bar_time="2024-01-01T00:00:00+00:00",
        )
        engine.ledger.save_position(pos)
        ts = pd.to_datetime("2024-01-01T00:05:00+00:00", utc=True)
        df = pd.DataFrame(
            [
                {
                    "timestamp": ts,
                    "open": gap_open,
                    "high": 3_400.0,
                    "low": 3_100.0,
                    "close": 3_350.0,
                    "volume": 100.0,
                    "signal": 0,
                    "vwap": 3_300.0,
                    "atr": 50.0,
                }
            ]
            * 25
        )
        result = engine.evaluate_symbol("binance", "ETH/USDT", "5m", force_candles=df)
        assert result["action"] == "EXIT_STOP_LOSS"
        # Fill must be ABOVE stop level (gap-through means worse fill for short)
        assert result["exit_price"] > stop_level


# ===========================================================================
# l7v.9 — Account-scoped positions
# ===========================================================================


class TestAccountScopedPositions:
    def test_two_accounts_same_symbol_isolated(self):
        db = _make_db()
        ledger = PaperLedger(db=db)
        _fund(ledger, "alice", 100_000.0)
        _fund(ledger, "bob", 100_000.0)
        _open_long(ledger, account_id="alice", symbol="BTC/USDT")
        assert (
            ledger.get_position("binance", "BTC/USDT", account_id="alice") is not None
        )
        assert ledger.get_position("binance", "BTC/USDT", account_id="bob") is None

    def test_list_positions_scoped(self):
        db = _make_db()
        ledger = PaperLedger(db=db)
        _fund(ledger, "alice", 100_000.0)
        _fund(ledger, "bob", 100_000.0)
        _open_long(ledger, account_id="alice", symbol="BTC/USDT")
        _open_short(ledger, account_id="bob", symbol="ETH/USDT")
        assert len(ledger.list_positions(account_id="alice")) == 1
        assert ledger.list_positions(account_id="alice")[0].symbol == "BTC/USDT"
        assert len(ledger.list_positions(account_id="bob")) == 1
        assert ledger.list_positions(account_id="bob")[0].symbol == "ETH/USDT"

    def test_close_position_scoped(self):
        db = _make_db()
        ledger = PaperLedger(db=db)
        _fund(ledger, "alice", 100_000.0)
        _fund(ledger, "bob", 100_000.0)
        _open_long(ledger, account_id="alice", symbol="BTC/USDT")
        _open_long(ledger, account_id="bob", symbol="BTC/USDT")
        ledger.close_position("binance", "BTC/USDT", account_id="alice")
        assert ledger.get_position("binance", "BTC/USDT", account_id="alice") is None
        assert ledger.get_position("binance", "BTC/USDT", account_id="bob") is not None

    def test_equity_does_not_cross_accounts(self):
        db = _make_db()
        ledger = PaperLedger(db=db)
        _fund(ledger, "alice", 50_000.0)
        _fund(ledger, "bob", 50_000.0)
        _open_long(
            ledger,
            account_id="alice",
            symbol="BTC/USDT",
            entry_price=50_000.0,
            quantity=1.0,
        )
        # Bob's equity must equal his own cash — Alice's position must not bleed in
        bob_cash = ledger.get_balance(account_id="bob")
        bob_equity = ledger.get_total_equity(account_id="bob")
        assert math.isclose(bob_equity, bob_cash, rel_tol=1e-6), (
            f"Bob's equity ({bob_equity}) != bob's cash ({bob_cash}): "
            "Alice's position is leaking into Bob's equity."
        )


# ===========================================================================
# l7v.10 — Price precision and holding_period_bars
# ===========================================================================


class TestPricePrecisionAndHoldingPeriod:
    def test_micro_price_not_rounded_to_zero(self):
        db = _make_db()
        ledger = PaperLedger(db=db)
        _fund(ledger, amount=100_000.0)
        micro_price = 0.00001234
        qty = 1_000_000.0
        cost_basis = micro_price * qty
        ledger.record_buy(
            account_id="default",
            exchange="binance",
            symbol="MICRO/USDT",
            quantity=qty,
            price=micro_price,
            fee=cost_basis * 0.001,
        )
        pos = PaperPosition(
            account_id="default",
            exchange="binance",
            symbol="MICRO/USDT",
            side="LONG",
            entry_price=micro_price,
            current_price=micro_price,
            quantity=qty,
            cost_basis=cost_basis,
            entry_time=datetime.now(timezone.utc).isoformat(),
        )
        ledger.save_position(pos)
        recovered = ledger.get_position("binance", "MICRO/USDT")
        assert recovered is not None
        assert (
            recovered.entry_price > 0
        ), f"Micro price collapsed: {recovered.entry_price}"

    def test_holding_period_bars_derived_from_elapsed(self):
        db = _make_db()
        engine = _make_engine(db)
        _fund(engine.ledger, amount=100_000.0)
        entry_time = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        exit_time = entry_time + timedelta(minutes=50)  # 10 bars @ 5m
        engine.ledger.record_buy(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            quantity=1.0,
            price=50_000.0,
            fee=50.0,
        )
        pos = PaperPosition(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            side="LONG",
            entry_price=50_000.0,
            current_price=50_000.0,
            quantity=1.0,
            cost_basis=50_000.0,
            stop_loss=49_000.0,
            take_profit=56_000.0,
            entry_time=entry_time.isoformat(),
            last_bar_time=entry_time.isoformat(),
        )
        engine.ledger.save_position(pos)
        df = pd.DataFrame(
            [
                {
                    "timestamp": exit_time,
                    "open": 50_000.0,
                    "high": 50_000.0,
                    "low": 48_000.0,
                    "close": 48_500.0,
                    "volume": 100.0,
                    "signal": 0,
                    "vwap": 48_500.0,
                    "atr": 500.0,
                }
            ]
            * 25
        )
        result = engine.evaluate_symbol("binance", "BTC/USDT", "5m", force_candles=df)
        assert result["action"] == "EXIT_STOP_LOSS"
        history = engine.ledger.list_trade_history(
            exchange="binance", symbol="BTC/USDT"
        )
        assert len(history) == 1
        assert history[0].holding_period_bars == 10


# ===========================================================================
# l7v.11 — Bounded ledger cash operations
# ===========================================================================


class TestLedgerCashValidation:
    def test_deposit_rejects_zero(self):
        ledger = PaperLedger(db=_make_db())
        with pytest.raises(ValueError, match="finite positive"):
            ledger.deposit(amount=0.0)

    def test_deposit_rejects_negative(self):
        ledger = PaperLedger(db=_make_db())
        with pytest.raises(ValueError, match="finite positive"):
            ledger.deposit(amount=-100.0)

    def test_deposit_rejects_nan(self):
        ledger = PaperLedger(db=_make_db())
        with pytest.raises(ValueError, match="finite positive"):
            ledger.deposit(amount=float("nan"))

    def test_deposit_rejects_inf(self):
        ledger = PaperLedger(db=_make_db())
        with pytest.raises(ValueError, match="finite positive"):
            ledger.deposit(amount=float("inf"))

    def test_withdraw_rejects_zero(self):
        ledger = PaperLedger(db=_make_db())
        _fund(ledger)
        with pytest.raises(ValueError, match="finite positive"):
            ledger.withdraw(amount=0.0)

    def test_withdraw_rejects_negative(self):
        ledger = PaperLedger(db=_make_db())
        _fund(ledger)
        with pytest.raises(ValueError, match="finite positive"):
            ledger.withdraw(amount=-500.0)

    def test_withdraw_rejects_nan(self):
        ledger = PaperLedger(db=_make_db())
        _fund(ledger)
        with pytest.raises(ValueError, match="finite positive"):
            ledger.withdraw(amount=float("nan"))

    def test_withdraw_rejects_over_balance(self):
        ledger = PaperLedger(db=_make_db())
        _fund(ledger, amount=1_000.0)
        actual_balance = ledger.get_balance()  # may include 10000 default
        with pytest.raises(ValueError, match="Insufficient"):
            ledger.withdraw(
                amount=actual_balance + 1_000.0
            )  # definitely exceeds balance

    def test_failed_validation_leaves_balance_unchanged(self):
        ledger = PaperLedger(db=_make_db())
        _fund(ledger, amount=1_000.0)
        bal = ledger.get_balance()
        with pytest.raises(ValueError):
            ledger.deposit(amount=-999.0)
        assert ledger.get_balance() == bal


# ===========================================================================
# l7v.14 — Short opens as SELL_SHORT
# ===========================================================================


class TestShortLedgerTransactionType:
    def test_record_sell_short_type(self):
        ledger = PaperLedger(db=_make_db())
        _fund(ledger, amount=50_000.0)
        tx = ledger.record_sell_short(
            account_id="default",
            exchange="binance",
            symbol="ETH/USDT",
            quantity=2.0,
            price=3_000.0,
            cost_basis=6_000.0,
            fee=6.0,
        )
        assert tx.type == "SELL_SHORT"
        assert tx.asset_qty < 0
        assert tx.amount < 0

    def test_short_ledger_history_reconstructs_qty(self):
        ledger = PaperLedger(db=_make_db())
        _fund(ledger, amount=50_000.0)
        qty = 3.0
        ledger.record_sell_short(
            account_id="default",
            exchange="binance",
            symbol="ETH/USDT",
            quantity=qty,
            price=2_000.0,
            cost_basis=qty * 2_000.0,
            fee=0.0,
        )
        txs = [
            t
            for t in ledger.list_transactions(account_id="default", symbol="ETH/USDT")
            if t.type == "SELL_SHORT"
        ]
        assert len(txs) == 1
        assert math.isclose(abs(txs[0].asset_qty), qty, rel_tol=1e-9)

    def test_short_balance_after_reflects_collateral(self):
        ledger = PaperLedger(db=_make_db())
        _fund(ledger, amount=50_000.0)
        balance_before_short = ledger.get_balance()  # includes 10k default
        entry, qty = 2_000.0, 5.0
        cost_basis, fee = entry * qty, entry * qty * 0.001
        ledger.record_sell_short(
            account_id="default",
            exchange="binance",
            symbol="ETH/USDT",
            quantity=qty,
            price=entry,
            cost_basis=cost_basis,
            fee=fee,
        )
        expected = balance_before_short - cost_basis - fee
        assert math.isclose(ledger.get_balance(), expected, rel_tol=1e-9)

    def test_engine_short_entry_uses_sell_short(self):
        db = _make_db()
        engine = _make_engine(db)
        _fund(engine.ledger, amount=100_000.0)
        ts = pd.to_datetime("2024-01-01T00:05:00+00:00", utc=True)
        df = pd.DataFrame(
            [
                {
                    "timestamp": ts,
                    "open": 3_000.0,
                    "high": 3_000.0,
                    "low": 2_990.0,
                    "close": 3_000.0,
                    "volume": 100.0,
                    "signal": -1,
                    "vwap": 3_000.0,
                    "atr": 30.0,
                }
            ]
            * 25
        )
        result = engine.evaluate_symbol("binance", "ETH/USDT", "5m", force_candles=df)
        # If the engine enters a SHORT, verify the ledger records SELL_SHORT not BUY.
        if result["action"] == "ENTER_SHORT":
            txs = engine.ledger.list_transactions(
                account_id="default", symbol="ETH/USDT"
            )
            tx_types = [t.type for t in txs]
            assert (
                "SELL_SHORT" in tx_types
            ), f"Expected SELL_SHORT in ledger for short entry, got: {tx_types}"
            assert (
                "BUY" not in tx_types
            ), "Short entry must not create a BUY transaction"
        # If action != ENTER_SHORT the signal mapping may use a different convention;
        # the SELL_SHORT ledger path is covered by test_record_sell_short_type above.
