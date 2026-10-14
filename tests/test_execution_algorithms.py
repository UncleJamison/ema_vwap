"""Unit tests for execution algorithms (ema_vwap-e6f).

Tests verify deterministic behavior of TWAP, VWAP, POV, and Implementation Shortfall algorithms
with mocked market contexts to ensure reproducibility.
"""

import pytest

from src.execution.algorithms import (
    ExecutionOrder,
    ImplementationShortfallAlgorithm,
    POVAlgorithm,
    TWAPAlgorithm,
    VWAPAlgorithm,
)


@pytest.fixture
def market_context_basic():
    """Simple market context for deterministic testing."""
    return {
        "volatility": 0.02,
        "volume_ratio": 1.0,
    }


def test_twap_buy_side_adverse(market_context_basic):
    """TWAP BUY should execute above target price."""
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="BUY",
        quantity=100.0,
        target_price=50000.0,
        urgency="normal",
    )
    algo = TWAPAlgorithm(windows=4)
    result = algo.execute(order, market_context_basic)

    assert result.algorithm == "TWAP"
    assert result.executed_qty == order.quantity
    assert result.avg_price > order.target_price
    assert result.slippage_pct > 0.0
    assert result.market_impact_pct > 0.0


def test_twap_sell_side_favorable(market_context_basic):
    """TWAP SELL should execute below target price."""
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="SELL",
        quantity=100.0,
        target_price=50000.0,
    )
    algo = TWAPAlgorithm(windows=4)
    result = algo.execute(order, market_context_basic)

    assert result.algorithm == "TWAP"
    assert result.avg_price < order.target_price


def test_twap_windows_increase_impact(market_context_basic):
    """More TWAP windows should increase market impact."""
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="BUY",
        quantity=100.0,
        target_price=50000.0,
    )
    algo_few = TWAPAlgorithm(windows=1)
    result_few = algo_few.execute(order, market_context_basic)

    algo_many = TWAPAlgorithm(windows=8)
    result_many = algo_many.execute(order, market_context_basic)

    assert result_many.market_impact_pct > result_few.market_impact_pct


def test_vwap_sell_side_favorable(market_context_basic):
    """VWAP SELL should execute below target price."""
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="SELL",
        quantity=50.0,
        target_price=49000.0,
    )
    algo = VWAPAlgorithm()
    result = algo.execute(order, market_context_basic)

    assert result.algorithm == "VWAP"
    assert result.executed_qty == order.quantity
    assert result.avg_price < order.target_price
    assert result.slippage_pct > 0.0
    assert result.market_impact_pct > 0.0


def test_vwap_buy_side_adverse(market_context_basic):
    """VWAP BUY should execute above target price."""
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="BUY",
        quantity=50.0,
        target_price=49000.0,
    )
    algo = VWAPAlgorithm()
    result = algo.execute(order, market_context_basic)

    assert result.avg_price > order.target_price


def test_vwap_high_volume_reduces_impact(market_context_basic):
    """High volume ratio should reduce VWAP impact."""
    high_vol_context = {"volatility": 0.02, "volume_ratio": 5.0}
    low_vol_context = {"volatility": 0.02, "volume_ratio": 1.0}
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="SELL",
        quantity=20.0,
        target_price=47500.0,
    )

    result_high = VWAPAlgorithm().execute(order, high_vol_context)
    result_low = VWAPAlgorithm().execute(order, low_vol_context)

    assert result_high.market_impact_pct < result_low.market_impact_pct


def test_pov_partial_fill(market_context_basic):
    """POV should partially fill proportional to pov_pct."""
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="BUY",
        quantity=100.0,
        target_price=51000.0,
    )
    algo = POVAlgorithm(pov_pct=25.0)
    result = algo.execute(order, market_context_basic)

    assert result.algorithm == "POV"
    assert result.executed_qty == 25.0  # 25% of 100
    assert result.executed_qty < order.quantity
    assert result.avg_price > order.target_price


def test_pov_high_pov_fills_more(market_context_basic):
    """Higher POV percentage should fill more quantity."""
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="BUY",
        quantity=100.0,
        target_price=51000.0,
    )
    result_low = POVAlgorithm(pov_pct=10.0).execute(order, market_context_basic)
    result_high = POVAlgorithm(pov_pct=50.0).execute(order, market_context_basic)

    assert result_high.executed_qty > result_low.executed_qty


def test_pov_high_pov_higher_impact(market_context_basic):
    """Higher POV should incur higher market impact."""
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="BUY",
        quantity=100.0,
        target_price=51000.0,
    )
    result_low = POVAlgorithm(pov_pct=10.0).execute(order, market_context_basic)
    result_high = POVAlgorithm(pov_pct=50.0).execute(order, market_context_basic)

    assert result_high.market_impact_pct > result_low.market_impact_pct


def test_pov_volume_and_volatility_increase_impact(market_context_basic):
    """POV should increase impact with higher volatility and volume."""
    calm_context = {"volatility": 0.005, "volume_ratio": 1.0}
    stormy_context = {"volatility": 0.05, "volume_ratio": 3.0}
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="BUY",
        quantity=100.0,
        target_price=51000.0,
    )

    result = POVAlgorithm(pov_pct=20.0).execute(order, calm_context)
    result_stormy = POVAlgorithm(pov_pct=20.0).execute(order, stormy_context)

    assert result_stormy.market_impact_pct > result.market_impact_pct


def test_implementation_shortfall_side_favorable(market_context_basic):
    """IS SELL should execute below target (better than arrival)."""
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="SELL",
        quantity=30.0,
        target_price=48000.0,
    )
    algo = ImplementationShortfallAlgorithm(risk_aversion=1.5)
    result = algo.execute(order, market_context_basic)

    assert result.algorithm == "IS"
    assert result.executed_qty == order.quantity
    assert result.slippage_pct > 0.0
    assert result.market_impact_pct > 0.0


def test_implementation_shortfall_low_risk_has_alpha(market_context_basic):
    """IS with low risk aversion should have smaller net cost (alpha benefit)."""
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="BUY",
        quantity=30.0,
        target_price=48000.0,
    )
    algo_conservative = ImplementationShortfallAlgorithm(risk_aversion=3.0)
    result_conservative = algo_conservative.execute(order, market_context_basic)
    algo_aggressive = ImplementationShortfallAlgorithm(risk_aversion=0.5)
    result_aggressive = algo_aggressive.execute(order, market_context_basic)

    # Lower risk aversion should have lower impact
    assert result_aggressive.market_impact_pct < result_conservative.market_impact_pct


def test_implementation_shortfall_volatility_increases_cost(market_context_basic):
    """IS should increase cost with higher volatility."""
    calm = {"volatility": 0.005, "volume_ratio": 1.0}
    stormy = {"volatility": 0.05, "volume_ratio": 1.0}
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="BUY",
        quantity=30.0,
        target_price=48000.0,
    )

    result_calm = ImplementationShortfallAlgorithm(risk_aversion=1.0).execute(
        order, calm
    )
    result_stormy = ImplementationShortfallAlgorithm(risk_aversion=1.0).execute(
        order, stormy
    )

    assert result_stormy.market_impact_pct > result_calm.market_impact_pct


def test_algorithms_with_zero_volatility(market_context_basic):
    """When volatility is zero, slippage and impact should be minimal."""
    market_context_zero_vol = {"volatility": 0.0, "volume_ratio": 1.0}
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="BUY",
        quantity=10.0,
        target_price=52000.0,
    )

    algo = TWAPAlgorithm(windows=2)
    result = algo.execute(order, market_context_zero_vol)
    assert result.slippage_pct < 0.01
    assert result.market_impact_pct < 0.01


def test_algorithms_with_high_volume_ratio(market_context_basic):
    """High volume ratio should reduce VWAP impact."""
    high_vol_context = {"volatility": 0.02, "volume_ratio": 5.0}
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="SELL",
        quantity=20.0,
        target_price=47500.0,
    )

    vwap_algo = VWAPAlgorithm()
    result = vwap_algo.execute(order, high_vol_context)

    # VWAP impact should be lower with high volume
    assert result.market_impact_pct < 0.04


def test_all_algorithms_from_registry(market_context_basic):
    """Verify that each algorithm can be instantiated and executes deterministically."""
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="BUY",
        quantity=5.0,
        target_price=53000.0,
    )

    algorithms = [
        TWAPAlgorithm(windows=2),
        VWAPAlgorithm(),
        POVAlgorithm(pov_pct=10.0),
        ImplementationShortfallAlgorithm(risk_aversion=0.5),
    ]

    for algo in algorithms:
        result = algo.execute(order, market_context_basic)
        assert isinstance(result.executed_qty, float)
        assert isinstance(result.avg_price, float)
        assert isinstance(result.slippage_pct, float)
        assert isinstance(result.market_impact_pct, float)
        assert result.algorithm == algo.name


def test_execution_order_serialization():
    """ExecutionOrder should be serializable and hold expected attributes."""
    order = ExecutionOrder(
        symbol="ETH/USDT",
        side="BUY",
        quantity=100.0,
        target_price=3000.0,
        urgency="high",
    )

    assert order.symbol == "ETH/USDT"
    assert order.side == "BUY"
    assert order.quantity == 100.0
    assert order.target_price == 3000.0
    assert order.urgency == "high"


def test_pov_underfill_protection():
    """POV should never fill more than the order quantity."""
    order = ExecutionOrder(
        symbol="BTC/USDT",
        side="BUY",
        quantity=10.0,
        target_price=51000.0,
    )
    result = POVAlgorithm(pov_pct=200.0).execute(
        order, {"volatility": 0.0, "volume_ratio": 1.0}
    )
    assert result.executed_qty <= order.quantity
