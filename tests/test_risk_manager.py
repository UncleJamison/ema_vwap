"""
Unit tests for Risk Management and Position Sizing Engine.
"""

from src.config import StrategyParams
from src.risk_manager import RiskManager


def test_position_sizing():
    params = StrategyParams(risk_per_trade_pct=1.0)  # 1% risk of $10,000 = $100 risk
    rm = RiskManager(params)

    equity = 10000.0
    entry_price = 60000.0
    stop_loss = 59000.0  # $1000 stop distance

    units, val_usd, risk_usd = rm.calculate_position_size(
        equity, entry_price, stop_loss
    )

    assert risk_usd == 100.0
    assert abs(units - (100.0 / 1000.0)) < 1e-6  # 0.1 BTC
    assert abs(val_usd - 6000.0) < 1e-4  # $6,000 position value


def test_stop_and_target_vwap():
    params = StrategyParams(
        stop_loss_type="vwap", vwap_stop_offset_pct=0.1, risk_reward_ratio=2.0
    )
    rm = RiskManager(params)

    entry_price = 65000.0
    vwap = 64800.0
    atr = 300.0

    stop, target = rm.calculate_stop_and_target(entry_price, side=1, vwap=vwap, atr=atr)

    assert stop < entry_price
    assert target > entry_price
    # Target distance should be 2.0x risk distance
    risk_dist = entry_price - stop
    target_dist = target - entry_price
    assert abs(target_dist - 2.0 * risk_dist) < 1e-4


def test_trailing_stop_update():
    params = StrategyParams(atr_multiplier=2.0)
    rm = RiskManager(params)

    atr = 100.0
    # Long trade
    current_stop = 50000.0
    # Price rises to 50500 -> new trailing stop = 50500 - (2 * 100) = 50300
    updated_stop = rm.update_trailing_stop(
        side=1, current_price=50500.0, current_stop=current_stop, atr=atr
    )
    assert updated_stop == 50300.0

    # Price drops to 50200 -> trailing stop should NOT ratchet down
    no_drop_stop = rm.update_trailing_stop(
        side=1, current_price=50200.0, current_stop=updated_stop, atr=atr
    )
    assert no_drop_stop == 50300.0
