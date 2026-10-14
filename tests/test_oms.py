"""
Tests for OMS module (src/oms/*): order lifecycle, pre-trade risk checks, TCA.
"""

import pytest

from src.oms.lifecycle import (
    Order,
    OrderManager,
    OrderSide,
    OrderStatus,
)
from src.oms.risk_checks import (
    PreTradeRiskConfig,
    PreTradeRiskManager,
)
from src.oms.tca import (
    ExecutionPlan,
    MarketParameters,
    almgren_chriss_optimal_trajectory,
    compute_implementation_shortfall,
    estimate_adv_from_context,
    estimate_slippage_bps,
    estimate_spread_bps,
    estimate_volatility,
    pre_trade_tca,
)


class TestOrderLifecycle:
    def test_create_order_defaults(self):
        o = Order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=1.0)
        assert o.status == OrderStatus.NEW
        assert o.remaining_quantity == 1.0
        assert o.filled_quantity == 0.0
        assert o.client_order_id.startswith("cli_")
        assert len(o.events) == 1
        assert o.events[0].to_status == OrderStatus.NEW

    def test_order_manager_create_and_get(self):
        mgr = OrderManager()
        o = mgr.create_order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=0.5)
        assert mgr.get_order(o.order_id) is not None
        assert mgr.get_order(o.order_id).symbol == "BTC/USDT"

    def test_submit_order(self):
        mgr = OrderManager()
        o = mgr.create_order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=1.0)
        assert mgr.submit_order(o.order_id, arrival_price=50000.0) is True
        sub = mgr.get_order(o.order_id)
        assert sub.status == OrderStatus.PENDING  # NEW -> PENDING on submit
        assert sub.arrival_price == 50000.0
        assert sub.submitted_at is not None

    def test_process_fill_partial_then_full(self):
        mgr = OrderManager()
        o = mgr.create_order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=2.0)
        mgr.submit_order(o.order_id)
        # Partial fill
        mgr.process_fill(o.order_id, fill_qty=0.5, fill_price=50000.0)
        p = mgr.get_order(o.order_id)
        assert p.filled_quantity == 0.5
        assert p.remaining_quantity == 1.5
        # Full fill
        mgr.process_fill(o.order_id, fill_qty=1.5, fill_price=50100.0)
        f = mgr.get_order(o.order_id)
        assert f.filled_quantity == 2.0
        assert f.remaining_quantity == 0.0
        # VWAP of fills: (0.5*50000 + 1.5*50100) / 2
        assert abs(f.avg_fill_price - (0.5 * 50000 + 1.5 * 50100) / 2.0) < 1e-6

    def test_cancel_order(self):
        mgr = OrderManager()
        o = mgr.create_order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=1.0)
        mgr.submit_order(o.order_id)
        assert mgr.cancel_order(o.order_id) is True
        assert mgr.get_order(o.order_id).status == OrderStatus.PENDING_CANCEL

    def test_reject_order(self):
        mgr = OrderManager()
        o = mgr.create_order(symbol="BTC/USDT", side=OrderSide.BUY, quantity=1.0)
        assert mgr.reject_order(o.order_id, reason="Risk check failed") is True
        r = mgr.get_order(o.order_id)
        assert r.status == OrderStatus.REJECTED
        # Rejection reason lives in the audit-trail event (error_message stays None)
        assert r.events[-1].to_status == OrderStatus.REJECTED
        assert r.events[-1].reason == "Risk check failed"

    def test_is_active(self):
        o = Order(symbol="X", side=OrderSide.BUY, quantity=1.0)
        assert o.is_active() is True
        o.status = OrderStatus.FILLED
        assert o.is_active() is False

    def test_invalid_transition_rejected(self):
        o = Order(symbol="X", side=OrderSide.BUY, quantity=1.0)
        # NEW -> FILLED is not a valid direct transition
        assert o.transition_to(OrderStatus.FILLED) is False
        assert o.status == OrderStatus.NEW

    def test_get_orders_by_status(self):
        mgr = OrderManager()
        a = mgr.create_order(symbol="A", side=OrderSide.BUY, quantity=1)
        b = mgr.create_order(symbol="B", side=OrderSide.SELL, quantity=1)
        mgr.submit_order(a.order_id)
        mgr.reject_order(b.order_id, "test")
        assert len(mgr.get_orders_by_status(OrderStatus.PENDING)) == 1
        assert len(mgr.get_orders_by_status(OrderStatus.REJECTED)) == 1

    def test_client_order_id_lookup(self):
        mgr = OrderManager()
        o = mgr.create_order(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=1.0,
            client_order_id="my-custom-id",
        )
        assert mgr.get_order_by_client_id("my-custom-id").order_id == o.order_id


class TestPreTradeRisk:
    def _manager(self, **config_overrides):
        cfg = PreTradeRiskConfig(**config_overrides)
        return PreTradeRiskManager(cfg)

    def test_small_order_passes_all(self):
        rm = self._manager()
        ok, results = rm.check_order(
            symbol="BTC/USDT",
            side="buy",
            quantity=1.0,
            price=100.0,
            portfolio_nav=1_000_000.0,
        )
        assert ok is True
        assert all(r.passed for r in results)

    def test_oversized_order_rejected(self):
        rm = self._manager(max_order_size_pct_nav=5.0)
        # 20% of NAV
        ok, results = rm.check_order(
            symbol="BTC/USDT",
            side="buy",
            quantity=2000.0,
            price=100.0,
            portfolio_nav=1_000_000.0,
        )
        assert ok is False
        failed = [r for r in results if not r.passed]
        assert any(r.check_name == "max_order_size" for r in failed)

    def test_participation_rate_limit(self):
        rm = self._manager(max_participation_pct_adv=10.0)
        # quantity / adv = 50%
        ok, results = rm.check_order(
            symbol="BTC/USDT",
            side="buy",
            quantity=500.0,
            price=100.0,
            portfolio_nav=1_000_000.0,
            adv=1000.0,
        )
        assert ok is False
        assert any(
            r.check_name == "participation_rate" and not r.passed for r in results
        )

    def test_position_limit(self):
        rm = self._manager(max_position_pct_nav=10.0)
        # Existing 99k position + 20k new = 119k > 10% of 1M
        ok, _results = rm.check_order(
            symbol="BTC/USDT",
            side="buy",
            quantity=200.0,
            price=100.0,
            portfolio_nav=1_000_000.0,
            current_positions={"BTC/USDT": 990.0},
        )
        assert ok is False

    def test_daily_loss_limit(self):
        rm = self._manager(max_daily_loss_pct=5.0)
        rm.set_starting_nav(1_000_000.0)
        rm.update_daily_pnl(-60_000.0)  # -6% of starting NAV
        ok, results = rm.check_order(
            symbol="BTC/USDT",
            side="buy",
            quantity=1.0,
            price=100.0,
            portfolio_nav=940_000.0,
        )
        assert ok is False
        assert any("daily_loss" in r.check_name and not r.passed for r in results)


class TestTCA:
    def test_almgren_chriss_plan(self):
        params = MarketParameters(adv=1_000_000, daily_volatility=0.25)
        plan = almgren_chriss_optimal_trajectory(
            total_quantity=10_000,
            market_params=params,
            n_slices=10,
        )
        assert isinstance(plan, ExecutionPlan)
        assert plan.n_slices == 10
        assert len(plan.slice_quantities) == 10
        assert abs(sum(plan.slice_quantities) - 10_000) < 1e-6
        assert all(q > 0 for q in plan.slice_quantities)

    def test_almgren_chriss_high_urgency_front_loaded(self):
        params = MarketParameters(adv=1_000_000)
        plan_hi = almgren_chriss_optimal_trajectory(
            total_quantity=10_000,
            market_params=params,
            n_slices=10,
            urgency="high",
        )
        plan_lo = almgren_chriss_optimal_trajectory(
            total_quantity=10_000,
            market_params=params,
            n_slices=10,
            urgency="low",
        )
        # High urgency should put more volume in the first slice than low urgency
        assert plan_hi.slice_quantities[0] >= plan_lo.slice_quantities[0]

    def test_slippage_increases_with_size(self):
        small = estimate_slippage_bps("BTC/USDT", quantity=100, side="buy")
        large = estimate_slippage_bps("BTC/USDT", quantity=100_000, side="buy")
        assert large[0] >= small[0]  # slippage_bps is first element of tuple

    def test_implementation_shortfall(self):
        # Buy filled above arrival => positive (bad) shortfall
        is_bad = compute_implementation_shortfall(
            symbol="BTC/USDT",
            quantity=10.0,
            side="buy",
            arrival_price=100.0,
            avg_fill_price=101.0,
        )
        assert is_bad > 0
        # Buy filled below arrival => negative (good)
        is_good = compute_implementation_shortfall(
            symbol="BTC/USDT",
            quantity=10.0,
            side="buy",
            arrival_price=100.0,
            avg_fill_price=99.0,
        )
        assert is_good < 0

    def test_pre_trade_tca_plan(self):
        plan = pre_trade_tca(
            symbol="BTC/USDT",
            quantity=5000,
            side="buy",
            arrival_price=100.0,
            urgency="normal",
        )
        assert isinstance(plan, dict)
        assert "slippage_bps" in plan or "total_cost_bps" in plan

    def test_spread_and_vol_estimators(self):
        spread = estimate_spread_bps("BTC/USDT")
        assert spread > 0
        vol = estimate_volatility("BTC/USDT")
        assert vol > 0
        adv = estimate_adv_from_context("BTC/USDT")
        assert adv > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
