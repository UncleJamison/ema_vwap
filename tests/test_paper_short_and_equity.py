"""
Unit tests for short trade accounting, total equity calculation, and trade direction configuration in Paper Trading.
"""

from datetime import datetime, timezone

import pytest

from src.config import StrategyParams
from src.database import CandleDatabase
from src.paper.engine import PaperTradingEngine
from src.paper.ledger import PaperLedger, PaperPosition
from src.paper.profiles import PaperProfileRegistry


@pytest.fixture
def test_db(tmp_path):
    db_file = str(tmp_path / "test_short_accounting.db")
    return CandleDatabase(db_path=db_file)


def test_paper_ledger_long_close_position(test_db):
    ledger = PaperLedger(db=test_db)
    account_id = "default"
    ledger.deposit(account_id=account_id, amount=90000.0)  # Total 100,000
    now = datetime.now(timezone.utc)

    # Buy 1 BTC at $50,000, fee $50
    ledger.record_buy(
        account_id=account_id,
        exchange="kucoin",
        symbol="BTC/USDT",
        quantity=1.0,
        price=50000.0,
        fee=50.0,
        notes="Entry Long",
    )
    # Balance after entry: 100,000 - 50,000 - 50 = 49,950
    assert ledger.get_balance(account_id) == 49950.0

    pos = PaperPosition(
        exchange="kucoin",
        symbol="BTC/USDT",
        side="LONG",
        quantity=1.0,
        entry_price=50000.0,
        cost_basis=50000.0,
        current_price=55000.0,
        unrealized_pnl=5000.0,
        entry_time=now.isoformat(),
        stop_loss=48000.0,
        take_profit=56000.0,
    )

    # Close LONG at $55,000, exit fee $55
    # Expected returned: 50,000 (cost basis) + 5,000 (pnl) - 55 (fee) = 54,945
    # Expected final balance: 49,950 + 54,945 = 104,895
    tx = ledger.record_close_position(
        account_id=account_id,
        pos=pos,
        exit_price=55000.0,
        fee=55.0,
        notes="Take Profit",
    )
    assert tx.type == "SELL"
    assert tx.amount == 54945.0
    assert ledger.get_balance(account_id) == 104895.0


def test_paper_ledger_short_close_position_win(test_db):
    ledger = PaperLedger(db=test_db)
    account_id = "default"
    ledger.deposit(account_id=account_id, amount=90000.0)  # Total 100,000
    now = datetime.now(timezone.utc)

    # Short 1 BTC at $50,000, collateral reserved = $50,000, fee $50
    ledger.record_buy(
        account_id=account_id,
        exchange="kucoin",
        symbol="BTC/USDT",
        quantity=1.0,
        price=50000.0,
        fee=50.0,
        notes="Short Entry",
    )
    assert ledger.get_balance(account_id) == 49950.0

    pos = PaperPosition(
        exchange="kucoin",
        symbol="BTC/USDT",
        side="SHORT",
        quantity=1.0,
        entry_price=50000.0,
        cost_basis=50000.0,
        current_price=45000.0,
        unrealized_pnl=5000.0,
        entry_time=now.isoformat(),
        stop_loss=52000.0,
        take_profit=44000.0,
    )

    # Exit winning short at $45,000, exit fee $45
    # Expected returned: 50,000 (collateral) + (50,000 - 45,000)*1 - 45 = 54,955
    # Expected final balance: 49,950 + 54,955 = 104,905
    tx = ledger.record_close_position(
        account_id=account_id,
        pos=pos,
        exit_price=45000.0,
        fee=45.0,
        notes="Take Profit Short",
    )
    assert tx.type == "BUY_TO_COVER"
    assert tx.amount == 54955.0
    assert ledger.get_balance(account_id) == 104905.0


def test_paper_ledger_short_close_position_loss(test_db):
    ledger = PaperLedger(db=test_db)
    account_id = "default"
    ledger.deposit(account_id=account_id, amount=90000.0)  # Total 100,000
    now = datetime.now(timezone.utc)

    # Short 1 BTC at $50,000, collateral reserved = $50,000, fee $50
    ledger.record_buy(
        account_id=account_id,
        exchange="kucoin",
        symbol="BTC/USDT",
        quantity=1.0,
        price=50000.0,
        fee=50.0,
        notes="Short Entry",
    )
    assert ledger.get_balance(account_id) == 49950.0

    pos = PaperPosition(
        exchange="kucoin",
        symbol="BTC/USDT",
        side="SHORT",
        quantity=1.0,
        entry_price=50000.0,
        cost_basis=50000.0,
        current_price=55000.0,
        unrealized_pnl=-5000.0,
        entry_time=now.isoformat(),
        stop_loss=55000.0,
        take_profit=40000.0,
    )

    # Exit losing short at $55,000, exit fee $55
    # Expected returned: 50,000 (collateral) + (50,000 - 55,000)*1 - 55 = 44,945
    # Expected final balance: 49,950 + 44,945 = 94,895
    tx = ledger.record_close_position(
        account_id=account_id,
        pos=pos,
        exit_price=55000.0,
        fee=55.0,
        notes="Stop Loss Short",
    )
    assert tx.type == "BUY_TO_COVER"
    assert tx.amount == 44945.0
    assert ledger.get_balance(account_id) == 94895.0


def test_paper_engine_total_equity_with_open_position(test_db):
    engine = PaperTradingEngine(db=test_db)
    engine.ledger.deposit(account_id="default", amount=90000.0)  # Total 100,000
    now = datetime.now(timezone.utc)

    # Manually add an open position
    pos = PaperPosition(
        exchange="kucoin",
        symbol="BTC/USDT",
        side="LONG",
        quantity=1.0,
        entry_price=50000.0,
        cost_basis=50000.0,
        current_price=50000.0,
        unrealized_pnl=0.0,
        entry_time=now.isoformat(),
    )
    # Deduct cost basis + entry fee from ledger as if recorded entry
    engine.ledger.record_buy(
        account_id="default",
        exchange="kucoin",
        symbol="BTC/USDT",
        quantity=1.0,
        price=50000.0,
        fee=50.0,
        notes="Test Buy",
    )
    engine.ledger.save_position(pos)

    status = engine.get_status()
    # Cash balance is 49,950. With cost_basis 50,000 and unrealized_pnl 0,
    # total_equity MUST be 49,950 + 50,000 + 0 = 99,950 (NOT 49,950!)
    assert status["cash_balance"] == 49950.0
    assert status["total_equity"] == 99950.0


def test_paper_profile_update_trade_direction(test_db):
    registry = PaperProfileRegistry(db=test_db)
    params = StrategyParams(fast_ema=12, slow_ema=26, trade_direction="long_only")

    profile_id = registry.save_profile(
        exchange="gemini",
        symbol="BTC/USD",
        timeframe="15m",
        strategy_mode="crossover",
        target_metric="sharpe_ratio",
        params=params,
        optuna_score=1.8,
        is_active=True,
    )
    assert profile_id is not None

    prof = registry.get_profile(
        exchange="gemini", symbol="BTC/USD", timeframe="15m", strategy_mode="crossover"
    )
    assert prof.params["trade_direction"] == "long_only"

    # Update trade direction to margin (both)
    success = registry.update_trade_direction(profile_id, "both")
    assert success is True

    updated_prof = registry.get_profile(
        exchange="gemini", symbol="BTC/USD", timeframe="15m", strategy_mode="crossover"
    )
    assert updated_prof.params["trade_direction"] == "both"
