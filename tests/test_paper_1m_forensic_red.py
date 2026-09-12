"""
RED test — verifies a 1m paper strategy must trigger an entry position
when data, profile, and signal conditions are satisfied.

This test should FAIL (RED) until the system enters a position.
"""

import pytest

from src.config import StrategyParams
from src.database import CandleDatabase
from src.paper.engine import PaperTradingEngine
from src.providers import SyntheticAdapter


@pytest.fixture
def test_db_1m(tmp_path):
    db_file = str(tmp_path / "test_1m_paper.db")
    return CandleDatabase(db_path=db_file)


def test_1m_strategy_must_enter_position(test_db_1m):
    """RED: A 1m strategy with a profile and synthetic candles must enter a trade."""
    engine = PaperTradingEngine(db=test_db_1m)
    synth = SyntheticAdapter()

    # Register an active 1m profile with permissive settings
    params = StrategyParams(
        strategy_mode="crossover",
        fast_ema=9,
        slow_ema=21,
        volume_filter_enabled=False,
        vwap_slope_min=0.0,
        trade_direction="both",
        risk_per_trade_pct=2.0,
    )
    engine.registry.save_profile(
        exchange="synthetic",
        symbol="BTC/USDT",
        timeframe="1m",
        strategy_mode="crossover",
        target_metric="sharpe_ratio",
        params=params,
        is_active=True,
    )

    # Force candles with a clear crossover signal (fast EMA above VWAP, close above VWAP)
    df_candles = synth.generate_candles(
        symbol="BTC/USDT", timeframe="1m", num_bars=50, seed=77
    )
    # Push price to create a clear long entry on the last bar
    last_idx = df_candles.index[-1]
    df_candles.loc[last_idx, "close"] = 50000.0
    df_candles.loc[last_idx, "high"] = 50500.0

    # Evaluate — this is the exact call the polling loop makes
    result = engine.evaluate_symbol(
        exchange="synthetic",
        symbol="BTC/USDT",
        timeframe="1m",
        force_candles=df_candles,
        fetch_live=False,
    )

    # RED ASSERTION: the system must have entered a position
    assert result.get("action") is not None, f"No action returned: {result}"
    assert "ENTER" in str(result.get("action")), (
        f"Expected entry action (ENTER_LONG/ENTER_SHORT), got: {result.get('action')}. "
        f"Result details: {result}. Check: profile active={engine.registry.get_profile('synthetic','BTC/USDT','1m','crossover') is not None}, signal conditions, and entry trigger logic."
    )

    # If position entered, verify it exists in the ledger
    pos = engine.ledger.get_position("synthetic", "BTC/USDT")
    assert pos is not None, "Position should exist after entry signal"
    assert pos.side in (
        "LONG",
        "SHORT",
    ), f"Position side should be LONG or SHORT, got: {pos.side if pos else 'NONE'}"
