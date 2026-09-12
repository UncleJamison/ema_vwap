"""
Unit tests for Paper Trading Profile Registry, SQLite Ledger, and Accounting Module.
"""

import pytest

from src.config import StrategyParams
from src.database import CandleDatabase
from src.paper import (
    PaperLedger,
    PaperPosition,
    PaperProfileRegistry,
    PaperTradeRecord,
)


@pytest.fixture
def test_db(tmp_path):
    db_file = str(tmp_path / "test_paper_candles.db")
    return CandleDatabase(db_path=db_file)


def test_paper_trading_tables_created(test_db):
    with test_db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

    assert "paper_profiles" in tables
    assert "paper_positions" in tables
    assert "paper_ledger_transactions" in tables
    assert "paper_trade_history" in tables


def test_paper_profile_registry_crud(test_db):
    registry = PaperProfileRegistry(db=test_db)

    params = StrategyParams(fast_ema=12, slow_ema=26, risk_per_trade_pct=3.0)
    profile_id = registry.save_profile(
        exchange="kucoin",
        symbol="BTC/USDT",
        timeframe="5m",
        strategy_mode="crossover",
        target_metric="sharpe_ratio",
        params=params,
        optuna_score=2.45,
        is_active=True,
    )
    assert profile_id is not None

    # Retrieve profile
    prof = registry.get_profile(
        exchange="kucoin", symbol="BTC/USDT", timeframe="5m", strategy_mode="crossover"
    )
    assert prof is not None
    assert prof.symbol == "BTC/USDT"
    assert prof.exchange == "kucoin"
    assert prof.optuna_score == 2.45
    assert prof.params["fast_ema"] == 12
    assert prof.params["slow_ema"] == 26

    # Convert to StrategyParams
    strat_params = registry.get_strategy_params(
        exchange="kucoin", symbol="BTC/USDT", timeframe="5m", strategy_mode="crossover"
    )
    assert strat_params.fast_ema == 12
    assert strat_params.slow_ema == 26
    assert strat_params.risk_per_trade_pct == 3.0

    # Fallback to default params for unknown symbol
    default_p = registry.get_strategy_params(exchange="gemini", symbol="ETH/USD")
    assert default_p.fast_ema == 9  # default from StrategyParams

    # List profiles
    all_profs = registry.list_profiles()
    assert len(all_profs) == 1

    # Toggle active
    registry.set_active(prof.profile_id, is_active=False)
    prof_inactive = registry.get_profile(exchange="kucoin", symbol="BTC/USDT")
    assert prof_inactive.is_active is False

    # Delete profile
    deleted = registry.delete_profile(prof.profile_id)
    assert deleted is True
    assert registry.get_profile(exchange="kucoin", symbol="BTC/USDT") is None


def test_paper_ledger_balances_and_transactions(test_db):
    ledger = PaperLedger(db=test_db)
    account_id = "test_acc"

    # Default initial balance
    assert ledger.get_balance(account_id) == 10000.0

    # Deposit
    tx_dep = ledger.deposit(account_id=account_id, amount=5000.0, notes="Top-up")
    assert tx_dep.amount == 5000.0
    assert tx_dep.balance_after == 15000.0
    assert ledger.get_balance(account_id) == 15000.0

    # Buy trade execution
    # Buy 0.1 BTC at $60,000 = $6,000 + $6 fee
    tx_buy = ledger.record_buy(
        account_id=account_id,
        exchange="kucoin",
        symbol="BTC/USDT",
        quantity=0.1,
        price=60000.0,
        fee=6.0,
        notes="Signal Entry",
    )
    assert tx_buy.type == "BUY"
    assert tx_buy.amount == -6006.0
    assert tx_buy.balance_after == 15000.0 - 6006.0
    assert ledger.get_balance(account_id) == 8994.0

    # Overdraft rejection
    with pytest.raises(ValueError, match="Insufficient funds"):
        ledger.record_buy(
            account_id=account_id,
            exchange="kucoin",
            symbol="BTC/USDT",
            quantity=1.0,
            price=60000.0,
        )

    # Sell trade execution
    # Sell 0.1 BTC at $65,000 = $6,500 - $6.50 fee = $6,493.50 net proceeds
    tx_sell = ledger.record_sell(
        account_id=account_id,
        exchange="kucoin",
        symbol="BTC/USDT",
        quantity=0.1,
        price=65000.0,
        fee=6.5,
        notes="Target Hit",
    )
    assert tx_sell.type == "SELL"
    assert tx_sell.amount == 6493.5
    assert tx_sell.balance_after == 8994.0 + 6493.5
    assert ledger.get_balance(account_id) == 15487.5

    # Check transaction audit trail
    txs = ledger.list_transactions(account_id=account_id)
    assert len(txs) == 3
    assert txs[0].type == "SELL"  # Ordered by id DESC
    assert txs[1].type == "BUY"
    assert txs[2].type == "DEPOSIT"


def test_paper_positions_and_trade_history(test_db):
    ledger = PaperLedger(db=test_db)

    # Open position
    pos = PaperPosition(
        exchange="gemini",
        symbol="ETH/USD",
        side="LONG",
        entry_price=3000.0,
        current_price=3150.0,
        quantity=2.0,
        cost_basis=6000.0,
        stop_loss=2900.0,
        take_profit=3300.0,
        unrealized_pnl=300.0,
        unrealized_pnl_pct=5.0,
        metadata={"signal": "crossover"},
    )
    ledger.save_position(pos)

    # Query open position
    saved_pos = ledger.get_position(exchange="gemini", symbol="ETH/USD")
    assert saved_pos is not None
    assert saved_pos.entry_price == 3000.0
    assert saved_pos.unrealized_pnl == 300.0
    assert saved_pos.metadata.get("signal") == "crossover"

    positions = ledger.list_positions()
    assert len(positions) == 1

    # Record completed trade
    trade = PaperTradeRecord(
        trade_id="tr_001",
        exchange="gemini",
        symbol="ETH/USD",
        timeframe="5m",
        side="LONG",
        entry_price=3000.0,
        exit_price=3300.0,
        quantity=2.0,
        entry_time="2024-01-15T10:00:00Z",
        exit_time="2024-01-15T14:30:00Z",
        holding_period_bars=54,
        realized_pnl=600.0,
        realized_pnl_pct=10.0,
        fees=6.0,
        exit_reason="TAKE_PROFIT",
    )
    ledger.record_completed_trade(trade)

    # Close position
    closed = ledger.close_position(exchange="gemini", symbol="ETH/USD")
    assert closed is True
    assert ledger.get_position(exchange="gemini", symbol="ETH/USD") is None

    # Check trade history & statistics
    history = ledger.list_trade_history()
    assert len(history) == 1
    assert history[0].trade_id == "tr_001"
    assert history[0].exit_reason == "TAKE_PROFIT"

    stats = ledger.get_statistics(exchange="gemini")
    assert stats["total_trades"] == 1
    assert stats["win_rate"] == 100.0
    assert stats["total_realized_pnl"] == 600.0
    assert stats["winning_trades"] == 1
