"""
Tests for Options Module (src/options/*)
Covers: Black-Scholes pricing, Greeks, implied vol, strategies, risk
"""

import math

import pytest

from src.options.greeks import (
    OptionType,
    black_scholes_greeks,
    black_scholes_price,
    implied_volatility,
)
from src.options.strategies import (
    ButterflyStrategy,
    IronCondorStrategy,
    OptionLeg,
    StraddleStrategy,
    StrangleStrategy,
    StrategyType,
    VerticalSpreadStrategy,
    create_strategy,
)


class TestBlackScholes:
    S, K, T, r, sigma = 100.0, 100.0, 0.25, 0.05, 0.2

    def test_atm_call_price(self):
        p = black_scholes_price(
            self.S, self.K, self.T, self.r, self.sigma, OptionType.CALL
        )
        # ATM call ~ 4.6 for these params (Bjerksund benchmark approx)
        assert 3.5 < p < 6.0
        assert p > 0

    def test_atm_put_price(self):
        p = black_scholes_price(
            self.S, self.K, self.T, self.r, self.sigma, OptionType.PUT
        )
        assert 3.0 < p < 6.0

    def test_put_call_parity(self):
        c = black_scholes_price(
            self.S, self.K, self.T, self.r, self.sigma, OptionType.CALL
        )
        p = black_scholes_price(
            self.S, self.K, self.T, self.r, self.sigma, OptionType.PUT
        )
        # C - P = S*e^{-qT} - K*e^{-rT} with q=0
        parity = self.S - self.K * math.exp(-self.r * self.T)
        assert abs((c - p) - parity) < 1e-8

    def test_expired_intrinsic(self):
        # ITM call at expiry
        assert black_scholes_price(110, 100, 0, 0.05, 0.2, OptionType.CALL) == 10.0
        # OTM call at expiry
        assert black_scholes_price(90, 100, 0, 0.05, 0.2, OptionType.CALL) == 0.0
        # ITM put at expiry
        assert black_scholes_price(90, 100, 0, 0.05, 0.2, OptionType.PUT) == 10.0

    def test_dividend_yield_lowers_call(self):
        no_div = black_scholes_price(
            self.S, self.K, self.T, self.r, self.sigma, OptionType.CALL, q=0.0
        )
        with_div = black_scholes_price(
            self.S, self.K, self.T, self.r, self.sigma, OptionType.CALL, q=0.03
        )
        assert with_div < no_div


class TestGreeks:
    S, K, T, r, sigma = 100.0, 100.0, 0.25, 0.05, 0.2

    def test_delta_call_range(self):
        g = black_scholes_greeks(
            self.S, self.K, self.T, self.r, self.sigma, OptionType.CALL
        )
        assert 0 < g.delta < 1
        assert abs(g.delta - 0.5) < 0.1  # ATM call delta ~ 0.5

    def test_delta_put_range(self):
        g = black_scholes_greeks(
            self.S, self.K, self.T, self.r, self.sigma, OptionType.PUT
        )
        assert -1 < g.delta < 0
        assert abs(g.delta + 0.5) < 0.1  # ATM put delta ~ -0.5

    def test_gamma_positive(self):
        g = black_scholes_greeks(
            self.S, self.K, self.T, self.r, self.sigma, OptionType.CALL
        )
        assert g.gamma > 0

    def test_theta_negative(self):
        g = black_scholes_greeks(
            self.S, self.K, self.T, self.r, self.sigma, OptionType.CALL
        )
        assert g.theta < 0

    def test_vega_positive(self):
        g = black_scholes_greeks(
            self.S, self.K, self.T, self.r, self.sigma, OptionType.CALL
        )
        assert g.vega > 0

    def test_call_put_delta_sum(self):
        gc = black_scholes_greeks(
            self.S, self.K, self.T, self.r, self.sigma, OptionType.CALL
        )
        gp = black_scholes_greeks(
            self.S, self.K, self.T, self.r, self.sigma, OptionType.PUT
        )
        # Call delta - put delta = 1 (no dividends)
        assert abs((gc.delta - gp.delta) - 1.0) < 1e-6


class TestImpliedVol:
    def test_roundtrip_iv(self):
        S, K, T, r = 100.0, 100.0, 0.25, 0.05
        true_iv = 0.25
        price = black_scholes_price(S, K, T, r, true_iv, OptionType.CALL)
        recovered = implied_volatility(price, S, K, T, r, OptionType.CALL)
        assert abs(recovered - true_iv) < 1e-3

    def test_iv_out_of_money_put(self):
        S, K, T, r = 100.0, 90.0, 0.25, 0.05
        true_iv = 0.35
        price = black_scholes_price(S, K, T, r, true_iv, OptionType.PUT)
        recovered = implied_volatility(price, S, K, T, r, OptionType.PUT)
        assert abs(recovered - true_iv) < 1e-3


class TestStrategies:
    def test_straddle(self):
        s = StraddleStrategy(symbol="BTC", spot=100.0, strike=100.0, expiry=0.25)
        legs = s.build_legs()
        assert len(legs) == 2
        assert sum(1 for leg in legs if leg.option_type == OptionType.CALL) == 1
        assert sum(1 for leg in legs if leg.option_type == OptionType.PUT) == 1
        # Both legs long, same strike
        assert all(leg.quantity == 1 for leg in legs)
        assert legs[0].strike == legs[1].strike

    def test_strangle(self):
        s = StrangleStrategy(
            symbol="BTC", spot=100.0, call_strike=110.0, put_strike=90.0, expiry=0.25
        )
        legs = s.build_legs()
        assert len(legs) == 2
        call_legs = [l for l in legs if l.option_type == OptionType.CALL]
        put_legs = [l for l in legs if l.option_type == OptionType.PUT]
        assert len(call_legs) == 1 and len(put_legs) == 1
        assert call_legs[0].strike > put_legs[0].strike

    def test_iron_condor(self):
        s = IronCondorStrategy(
            symbol="BTC",
            spot=100.0,
            call_short_strike=105.0,
            call_long_strike=110.0,
            put_short_strike=95.0,
            put_long_strike=90.0,
            expiry=0.25,
        )
        legs = s.build_legs()
        assert len(legs) == 4
        # 2 calls (short + long), 2 puts (short + long)
        calls = [l for l in legs if l.option_type == OptionType.CALL]
        puts = [l for l in legs if l.option_type == OptionType.PUT]
        assert len(calls) == 2 and len(puts) == 2
        assert sum(l.quantity for l in calls) == 0  # -1 + 1
        assert sum(l.quantity for l in puts) == 0  # -1 + 1

    def test_vertical_spread_call(self):
        s = VerticalSpreadStrategy(
            symbol="BTC",
            spot=100.0,
            long_strike=95.0,
            short_strike=100.0,
            option_type=OptionType.CALL,
            expiry=0.25,
        )
        legs = s.build_legs()
        assert len(legs) == 2
        assert sum(l.quantity for l in legs) == 0  # +1 -1

    def test_butterfly(self):
        s = ButterflyStrategy(
            symbol="BTC",
            spot=100.0,
            lower_strike=90.0,
            middle_strike=100.0,
            upper_strike=110.0,
            expiry=0.25,
        )
        legs = s.build_legs()
        assert len(legs) == 3
        assert sum(l.quantity for l in legs) == 0  # +1 -2 +1

    def test_create_strategy_dispatch(self):
        s = create_strategy(
            StrategyType.STRADDLE, "BTC", 100.0, strike=100.0, expiry=0.25
        )
        assert isinstance(s, StraddleStrategy)
        s2 = create_strategy(StrategyType.IRON_CONDOR, "BTC", 100.0)
        assert isinstance(s2, IronCondorStrategy)

    def test_leg_price(self):
        leg = OptionLeg(OptionType.CALL, strike=100.0, quantity=1, expiry=0.25)
        price = leg.price(S=100.0, r=0.05, sigma=0.2)
        assert price > 0

    def test_leg_greeks_scaled_by_quantity(self):
        leg = OptionLeg(OptionType.CALL, strike=100.0, quantity=-1, expiry=0.25)
        g = leg.leg_greeks(S=100.0, r=0.05, sigma=0.2)
        assert g.delta < 0  # short call -> negative delta


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
