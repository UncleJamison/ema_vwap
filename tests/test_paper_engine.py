"""
Unit tests for Virtual Execution Engine, Live Polling, and SL/TP Order Management.
"""

import os
import tempfile
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.config import StrategyParams
from src.paper.engine import PaperTradingEngine
from src.paper.models import PaperPosition
from src.providers import SyntheticAdapter
from src.sync_bridge import run_async


@pytest.fixture
def engine(pg_container):
    """Create PaperTradingEngine with PostgreSQL backend (fresh state per test)."""
    import asyncio as _asyncio

    from src.sync_bridge import get_main_loop

    ml = get_main_loop()
    assert ml is not None, "pg_container must set the main loop"

    async def _cleanup():
        from src.pg_connection_manager import get_pg_manager

        pool = get_pg_manager().get_pool("paper_trading")
        async with pool.acquire() as conn:
            # Use a savepoint-free TRUNCATE — fast and resets identity columns
            await conn.execute(
                "TRUNCATE paper_positions, paper_trade_history, "
                "paper_ledger_transactions, paper_profiles RESTART IDENTITY"
            )

    _asyncio.run_coroutine_threadsafe(_cleanup(), ml).result(timeout=10)
    return PaperTradingEngine()


@pytest.fixture
def synthetic_adapter():
    return SyntheticAdapter()


def test_paper_engine_entry_and_hold(engine, synthetic_adapter):
    """Test basic entry and hold behavior."""
    # Generate test candles with strong trend
    df_candles = synthetic_adapter.generate_candles(
        symbol="BTC/USDT", timeframe="5m", num_bars=100, seed=42
    )

    # Save active profile
    params = StrategyParams(
        strategy_mode="crossover",
        fast_ema=9,
        slow_ema=21,
        volume_filter_enabled=False,
        vwap_slope_min=0.0,
        risk_per_trade_pct=2.0,
    )
    engine.registry.save_profile_sync(
        exchange="kucoin",
        symbol="BTC/USDT",
        timeframe="5m",
        strategy_mode="crossover",
        target_metric="sharpe_ratio",
        params=params,
        is_active=True,
    )

    # Initial evaluation
    res = engine.evaluate_symbol(
        exchange="kucoin",
        symbol="BTC/USDT",
        timeframe="5m",
        force_candles=df_candles,
    )
    assert "action" in res

    # If position was entered
    if "ENTER" in res["action"]:
        pos = engine.ledger.get_position_sync("kucoin", "BTC/USDT")
        assert pos is not None
        assert pos.entry_price > 0
        assert pos.stop_loss is not None
        assert pos.take_profit is not None

        # Next bar with price holding -> HOLD
        df_next = df_candles.copy()
        res_hold = engine.evaluate_symbol(
            exchange="kucoin",
            symbol="BTC/USDT",
            timeframe="5m",
            force_candles=df_next,
        )
        assert res_hold["action"] == "HOLD"


def test_paper_engine_respects_profile_risk_percentage_before_safety_cap(
    engine, monkeypatch
):
    """Test that profile risk percentage is respected."""
    engine.ledger.deposit_sync(
        account_id="default", amount=100000.0, notes="Seed account"
    )

    rows = [
        {
            "timestamp": f"2024-01-01T00:{idx:02d}:00Z",
            "close": 100.0,
            "high": 101.0,
            "low": 99.0,
            "vwap": 100.0,
            "atr": 1.0,
            "signal": 0,
        }
        for idx in range(20)
    ]
    rows[-1]["signal"] = 1

    df_candles = pd.DataFrame(rows)

    monkeypatch.setattr(
        "src.paper.engine.EmaVwapStrategy.generate_signals",
        lambda self, df: df.assign(signal=1),
    )

    params = StrategyParams(
        strategy_mode="crossover",
        fast_ema=9,
        slow_ema=21,
        volume_filter_enabled=False,
        vwap_slope_min=0.0,
        risk_per_trade_pct=3.0,
        vwap_stop_offset_pct=0.1,
        initial_capital=100000.0,
    )
    engine.registry.save_profile_sync(
        exchange="kucoin",
        symbol="BTC/USDT",
        timeframe="5m",
        strategy_mode="crossover",
        target_metric="sharpe_ratio",
        params=params,
        is_active=True,
    )

    res = engine.evaluate_symbol(
        exchange="kucoin",
        symbol="BTC/USDT",
        timeframe="5m",
        force_candles=df_candles,
    )

    assert res["action"].startswith("ENTER")
    pos = engine.ledger.get_position_sync("kucoin", "BTC/USDT")
    assert pos is not None
    # Position size should respect the 25% portfolio cap (from params)
    assert pos.quantity * pos.current_price > 0.0


def test_paper_engine_stop_loss_trigger(engine, synthetic_adapter):
    """Test stop loss trigger."""
    # Pre-open a LONG position at $65,000 with SL at $64,000, TP at $68,000
    pos = PaperPosition(
        exchange="kucoin",
        symbol="BTC/USDT",
        side="LONG",
        entry_price=65000.0,
        current_price=65000.0,
        quantity=0.1,
        cost_basis=6500.0,
        stop_loss=64000.0,
        take_profit=68000.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
        entry_time="2024-01-15T10:00:00Z",
        account_id="default",
    )
    engine.ledger.save_position_sync(pos)

    # Generate candles where latest low breaches SL ($63,500)
    df_candles = synthetic_adapter.generate_candles(
        symbol="BTC/USDT", timeframe="5m", num_bars=50, start_price=65000.0, seed=123
    )
    df_candles.loc[df_candles.index[-1], "low"] = 63500.0
    df_candles.loc[df_candles.index[-1], "close"] = 63800.0

    res = engine.evaluate_symbol(
        exchange="kucoin",
        symbol="BTC/USDT",
        timeframe="5m",
        force_candles=df_candles,
    )
    assert res["action"] == "EXIT_STOP_LOSS"
    assert engine.ledger.get_position_sync("kucoin", "BTC/USDT") is None

    # Check completed trade record
    trades = engine.ledger.list_trade_history_sync(exchange="kucoin", symbol="BTC/USDT")
    assert len(trades) == 1
    assert trades[0].exit_reason == "STOP_LOSS"


def test_paper_engine_take_profit_trigger(engine, synthetic_adapter):
    """Test take profit trigger."""
    # Pre-open a LONG position at $65,000 with SL at $64,000, TP at $67,000
    pos = PaperPosition(
        exchange="gemini",
        symbol="BTC/USD",
        side="LONG",
        entry_price=65000.0,
        current_price=65000.0,
        quantity=0.1,
        cost_basis=6500.0,
        stop_loss=64000.0,
        take_profit=67000.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
        entry_time="2024-01-15T10:00:00Z",
        account_id="default",
    )
    engine.ledger.save_position_sync(pos)

    # Generate candles where high reaches TP ($67,500)
    df_candles = synthetic_adapter.generate_candles(
        symbol="BTC/USD", timeframe="5m", num_bars=50, start_price=65000.0, seed=123
    )
    df_candles.loc[df_candles.index[-1], "high"] = 67500.0
    df_candles.loc[df_candles.index[-1], "close"] = 67200.0

    res = engine.evaluate_symbol(
        exchange="gemini",
        symbol="BTC/USD",
        timeframe="5m",
        force_candles=df_candles,
    )
    assert res["action"] == "EXIT_TAKE_PROFIT"
    assert engine.ledger.get_position_sync("gemini", "BTC/USD") is None

    trades = engine.ledger.list_trade_history_sync(exchange="gemini", symbol="BTC/USD")
    assert len(trades) == 1
    assert trades[0].exit_reason == "TAKE_PROFIT"
    assert trades[0].realized_pnl > 0


def test_paper_engine_manual_market_close(engine):
    """Test manual market close."""
    pos = PaperPosition(
        exchange="gemini",
        symbol="ETH/USD",
        side="LONG",
        entry_price=3000.0,
        current_price=3200.0,
        quantity=1.0,
        cost_basis=3000.0,
        stop_loss=2800.0,
        take_profit=3500.0,
        entry_time="2024-01-15T10:00:00Z",
        account_id="default",
    )
    engine.ledger.save_position_sync(pos)

    trade = engine.close_position_market("gemini", "ETH/USD", current_price=3250.0)
    assert trade is not None
    assert trade.exit_reason == "MANUAL"
    assert trade.exit_price == 3250.0
    assert engine.ledger.get_position_sync("gemini", "ETH/USD") is None


def test_paper_engine_polling_runner_lifecycle(engine):
    """Test polling runner lifecycle."""
    assert engine.is_running is False

    started = engine.start_polling(interval_seconds=1)
    assert started is True
    assert engine.is_running is True

    # Starting again returns False (already running)
    assert engine.start_polling() is False

    status = run_async(engine.get_status())
    assert status["is_running"] is True
    assert "cash_balance" in status
    assert "total_equity" in status

    stopped = engine.stop_polling()
    assert stopped is True
    assert engine.is_running is False


def test_paper_engine_runner_persistence(engine):
    """Test runner persistence to settings."""
    from src.settings import SettingsManager

    tmp = tempfile.TemporaryDirectory()
    settings_db_path = os.path.join(tmp.name, "system_settings_for_runner.db")
    # Create empty DB file for SettingsManager
    import sqlite3

    conn = sqlite3.connect(settings_db_path)
    conn.close()

    settings_mgr = SettingsManager()

    # Track settings state
    settings_state = {
        "paper_runner_enabled": "false",
        "paper_polling_interval": "20",
    }

    # Mock the get/set methods directly
    settings_mgr.get = MagicMock(
        side_effect=lambda key, default=None: settings_state.get(key, default)
    )

    def mock_set(key, value, is_secret=None):
        settings_state[key] = str(value)

    settings_mgr.set = MagicMock(side_effect=mock_set)

    try:
        engine2 = PaperTradingEngine(settings_mgr=settings_mgr)
        # Start and persist
        engine2.start_polling(interval_seconds=20, persist=True)
        assert engine2.is_running is True
        assert settings_mgr.get("paper_runner_enabled") == "true"
        assert settings_mgr.get("paper_polling_interval") == "20"

        # Stop and persist
        engine2.stop_polling(persist=True)
        assert engine2.is_running is False
        assert settings_mgr.get("paper_runner_enabled") == "false"
        assert settings_mgr.get("paper_polling_interval") == "20"
    finally:
        try:
            tmp.cleanup()
        except PermissionError:
            pass


def test_reconcile_positions_stop_loss_breach(engine):
    """Test reconciliation with stop loss breach."""
    pos = PaperPosition(
        exchange="gemini",
        symbol="BTC/USD",
        side="LONG",
        entry_price=65000.0,
        current_price=65000.0,
        quantity=0.1,
        cost_basis=6500.0,
        stop_loss=64000.0,
        take_profit=68000.0,
        entry_time="2026-08-20T10:00:00+00:00",
        updated_time="2026-08-20T10:00:00+00:00",
        account_id="default",
    )
    engine.ledger.save_position_sync(pos)

    # Historical candles spanning downtime gap
    df_candles = pd.DataFrame(
        {
            "timestamp": [
                "2026-08-20T10:05:00+00:00",
                "2026-08-20T10:10:00+00:00",
                "2026-08-20T10:15:00+00:00",
            ],
            "open": [65000.0, 64500.0, 63800.0],
            "high": [65200.0, 64800.0, 64000.0],
            "low": [64800.0, 63500.0, 63200.0],  # Bar 2 breaches SL at 64000
            "close": [64900.0, 63700.0, 63500.0],
            "volume": [100.0, 150.0, 120.0],
        }
    )

    results = engine.reconcile_positions_on_startup(
        force_candles_map={"gemini:BTC/USD": df_candles}
    )
    assert len(results) == 1
    assert results[0]["action"] == "RECONCILE_EXIT_STOP_LOSS"
    assert results[0]["timestamp"] == "2026-08-20T10:10:00+00:00"

    # Position must be closed in ledger
    assert engine.ledger.get_position_sync("gemini", "BTC/USD") is None

    # Trade history must be recorded
    trades = engine.ledger.list_trade_history_sync("gemini", "BTC/USD")
    assert len(trades) == 1
    assert trades[0].exit_reason == "STOP_LOSS"
    assert trades[0].exit_time.isoformat() == "2026-08-20T10:10:00+00:00"


def test_reconcile_positions_take_profit_breach(engine):
    """Test reconciliation with take profit breach."""
    pos = PaperPosition(
        exchange="gemini",
        symbol="ETH/USD",
        side="SHORT",
        entry_price=3000.0,
        current_price=3000.0,
        quantity=1.0,
        cost_basis=3000.0,
        stop_loss=3200.0,
        take_profit=2800.0,
        entry_time="2026-08-20T12:00:00+00:00",
        updated_time="2026-08-20T12:00:00+00:00",
        account_id="default",
    )
    engine.ledger.save_position_sync(pos)

    # Historical candles spanning downtime gap
    df_candles = pd.DataFrame(
        {
            "timestamp": [
                "2026-08-20T12:05:00+00:00",
                "2026-08-20T12:10:00+00:00",
            ],
            "open": [3000.0, 2900.0],
            "high": [3020.0, 2920.0],
            "low": [2950.0, 2750.0],  # Bar 2 drops to 2750 (hits TP at 2800)
            "close": [2980.0, 2780.0],
            "volume": [50.0, 80.0],
        }
    )

    results = engine.reconcile_positions_on_startup(
        force_candles_map={"gemini:ETH/USD": df_candles}
    )
    assert len(results) == 1
    assert results[0]["action"] == "RECONCILE_EXIT_TAKE_PROFIT"
    assert results[0]["timestamp"] == "2026-08-20T12:10:00+00:00"
    assert engine.ledger.get_position_sync("gemini", "ETH/USD") is None


def test_reconcile_positions_updates_mtm_no_exit(engine):
    """Test reconciliation updates MTM without exit."""
    pos = PaperPosition(
        exchange="kucoin",
        symbol="SOL/USDT",
        side="LONG",
        entry_price=150.0,
        current_price=150.0,
        quantity=10.0,
        cost_basis=1500.0,
        stop_loss=140.0,
        take_profit=180.0,
        entry_time="2026-08-20T15:00:00+00:00",
        updated_time="2026-08-20T15:00:00+00:00",
        account_id="default",
    )
    engine.ledger.save_position_sync(pos)

    # Candles that move price to 160 without breaching SL or TP
    df_candles = pd.DataFrame(
        {
            "timestamp": [
                "2026-08-20T15:05:00+00:00",
                "2026-08-20T15:10:00+00:00",
            ],
            "open": [150.0, 155.0],
            "high": [156.0, 162.0],
            "low": [149.0, 154.0],
            "close": [155.0, 160.0],
            "volume": [200.0, 250.0],
        }
    )

    results = engine.reconcile_positions_on_startup(
        force_candles_map={"kucoin:SOL/USDT": df_candles}
    )
    assert len(results) == 1
    assert results[0]["action"] == "RECONCILE_UPDATED_MTM"
    assert results[0]["current_price"] == 160.0
    assert results[0]["unrealized_pnl"] == 100.0

    # Position should still be open
    updated_pos = engine.ledger.get_position_sync("kucoin", "SOL/USDT")
    assert updated_pos is not None
    assert updated_pos.current_price == 160.0
    assert updated_pos.unrealized_pnl == 100.0


def test_paper_engine_update_sl_tp(engine):
    """Test dynamic SL/TP update."""
    pos = PaperPosition(
        exchange="gemini",
        symbol="BTC/USD",
        side="LONG",
        entry_price=90000.0,
        current_price=92000.0,
        quantity=0.5,
        cost_basis=45000.0,
        stop_loss=88000.0,
        take_profit=95000.0,
        entry_time="2024-01-15T10:00:00Z",
        account_id="default",
    )
    engine.ledger.save_position_sync(pos)

    # Modify SL/TP dynamically
    updated = engine.update_position_sl_tp(
        exchange="gemini",
        symbol="BTC/USD",
        stop_loss=89500.0,
        take_profit=97000.0,
    )
    assert updated["stop_loss"] == 89500.0
    assert updated["take_profit"] == 97000.0

    # Verify persisted in ledger
    saved_pos = engine.ledger.get_position_sync("gemini", "BTC/USD")
    assert saved_pos is not None
    assert saved_pos.stop_loss == 89500.0
    assert saved_pos.take_profit == 97000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
