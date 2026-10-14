"""
Tests for Factor Research Module (src/factors/*)
Covers: factor functions, IC analysis, factor optimizer
"""

import numpy as np
import pandas as pd
import pytest

from src.factors.factors import (
    compute_all_factors,
    get_factor_categories,
    list_factors,
    mean_reversion_factor,
    momentum_factor,
    realized_volatility,
    volatility_factor,
    volume_factor,
)
from src.factors.ic_analysis import (
    compute_ic,
    compute_ic_decay,
    compute_ic_series,
    ic_summary,
)
from src.factors.optimizer import (
    CombinationMethod,
    FactorOptimizerConfig,
    combine_factors,
    optimize_factor_portfolio,
)


@pytest.fixture
def sample_data():
    np.random.seed(42)
    n = 252
    dates = pd.date_range("2023-01-01", periods=n, freq="D")
    rets = np.random.normal(0.0005, 0.015, n)
    prices = (1 + pd.Series(rets)).cumprod() * 100
    df = pd.DataFrame(
        {
            "close": prices.values,
            "volume": np.random.lognormal(10, 0.5, n),
            "high": (prices * 1.01).values,
            "low": (prices * 0.99).values,
            "open": prices.shift(1).fillna(prices.iloc[0]).values,
        },
        index=dates,
    )
    return df


class TestFactors:
    def test_momentum_factor(self, sample_data):
        factor = momentum_factor(sample_data, lookback=20)
        assert isinstance(factor, pd.Series)
        assert factor.name == "momentum"
        assert len(factor) == len(sample_data)
        # Skip-first guard: first lookback+skip values are NaN (lookahead guard)
        assert factor.iloc[:21].isna().all()
        assert not np.isnan(factor.iloc[-1])

    def test_mean_reversion_factor(self, sample_data):
        factor = mean_reversion_factor(sample_data, lookback=20)
        assert isinstance(factor, pd.Series)
        assert len(factor) == len(sample_data)

    def test_volume_factor(self, sample_data):
        factor = volume_factor(sample_data, lookback=20)
        assert isinstance(factor, pd.Series)
        assert len(factor) == len(sample_data)

    def test_volatility_factor(self, sample_data):
        factor = volatility_factor(sample_data, lookback=20)
        assert isinstance(factor, pd.Series)
        assert len(factor) == len(sample_data)

    def test_realized_volatility(self, sample_data):
        rv = realized_volatility(sample_data, lookback=20)
        assert isinstance(rv, pd.Series)
        assert (rv.dropna() >= 0).all()

    def test_compute_all_factors(self, sample_data):
        factors = compute_all_factors(sample_data)
        assert isinstance(factors, pd.DataFrame)
        assert len(factors) == len(sample_data)
        assert factors.shape[1] > 0

    def test_list_factors(self):
        names = list_factors()
        assert isinstance(names, list)
        assert any("momentum" in n for n in names)

    def test_get_factor_categories(self):
        cats = get_factor_categories()
        assert isinstance(cats, list)
        assert len(cats) > 0


class TestICAnalysis:
    @pytest.fixture
    def cross_sectional(self):
        """Cross-sectional data: dates x assets."""
        np.random.seed(42)
        n_periods = 120
        n_assets = 10
        dates = pd.date_range("2023-01-01", periods=n_periods, freq="D")
        factor_df = pd.DataFrame(
            np.random.normal(0, 1, (n_periods, n_assets)),
            index=dates,
        )
        # Returns correlated with factor
        returns_df = pd.DataFrame(
            0.1 * factor_df.values + np.random.normal(0, 0.01, (n_periods, n_assets)),
            index=dates,
        )
        return factor_df, returns_df

    def test_compute_ic(self):
        np.random.seed(42)
        factor = pd.Series(np.random.normal(0, 1, 100))
        returns = pd.Series(0.5 * factor + np.random.normal(0, 0.01, 100))
        ic, p = compute_ic(factor, returns, method="spearman")
        assert isinstance(ic, float)
        assert -1 <= ic <= 1
        assert 0 <= p <= 1
        assert ic > 0.8  # strong positive correlation by construction

    def test_compute_ic_short_series(self):
        # < 3 obs -> returns (0.0, 1.0) sentinel
        ic, p = compute_ic(pd.Series([1.0, 2.0]), pd.Series([1.0, 2.0]))
        assert ic == 0.0
        assert p == 1.0

    def test_compute_ic_series(self, cross_sectional):
        factor_df, returns_df = cross_sectional
        ic_series = compute_ic_series(factor_df, returns_df, horizon=1, min_periods=20)
        assert isinstance(ic_series, pd.Series)
        assert len(ic_series) > 0

    def test_compute_ic_decay(self, cross_sectional):
        factor_df, returns_df = cross_sectional
        decay = compute_ic_decay(
            factor_df, returns_df, horizons=[1, 5, 10], min_periods=20
        )
        assert isinstance(decay, pd.DataFrame)
        assert "horizon" in decay.columns
        assert "mean_ic" in decay.columns
        assert len(decay) == 3

    def test_ic_summary(self, cross_sectional):
        factor_df, returns_df = cross_sectional
        ic_series = compute_ic_series(factor_df, returns_df, horizon=1, min_periods=20)
        summary = ic_summary(ic_series, factor_name="mom", horizon=1)
        # ic_summary returns an ICSeriesResult dataclass
        assert hasattr(summary, "mean_ic")
        assert hasattr(summary, "std_ic")
        assert hasattr(summary, "ir")
        assert hasattr(summary, "hit_rate")


class TestFactorOptimizer:
    @pytest.fixture
    def factor_data(self):
        """Cross-sectional factor data: dates x factors."""
        np.random.seed(42)
        n_periods = 252
        dates = pd.date_range("2023-01-01", periods=n_periods, freq="D")
        n_factors = 3
        factor_df = pd.DataFrame(
            np.random.normal(0, 1, (n_periods, n_factors)),
            index=dates,
            columns=["mom", "mr", "vol"],
        )
        factor_returns_df = pd.DataFrame(
            np.random.normal(0, 0.01, (n_periods, n_factors)),
            index=dates,
            columns=["mom", "mr", "vol"],
        )
        return factor_df, factor_returns_df

    def test_combine_factors_equal_weight(self, factor_data):
        factor_df, _factor_returns_df = factor_data
        config = FactorOptimizerConfig()  # default IC_WEIGHTED
        weights = combine_factors(factor_df, config=config)
        assert isinstance(weights, np.ndarray)
        assert len(weights) == factor_df.shape[1]

    def test_combine_factors_mean_variance(self, factor_data):
        factor_df, factor_returns_df = factor_data
        config = FactorOptimizerConfig(method=CombinationMethod.MEAN_VARIANCE)
        weights = combine_factors(
            factor_df, config=config, factor_returns=factor_returns_df
        )
        assert isinstance(weights, np.ndarray)
        assert len(weights) == factor_df.shape[1]

    def test_combine_factors_risk_parity(self, factor_data):
        factor_df, factor_returns_df = factor_data
        config = FactorOptimizerConfig(method=CombinationMethod.RISK_PARITY)
        weights = combine_factors(
            factor_df, config=config, factor_returns=factor_returns_df
        )
        assert isinstance(weights, np.ndarray)
        assert len(weights) == factor_df.shape[1]

    def test_combine_factors_hrp(self, factor_data):
        factor_df, factor_returns_df = factor_data
        config = FactorOptimizerConfig(method=CombinationMethod.HRP)
        weights = combine_factors(
            factor_df, config=config, factor_returns=factor_returns_df
        )
        assert isinstance(weights, np.ndarray)
        assert len(weights) == factor_df.shape[1]

    def test_optimize_factor_portfolio(self, factor_data):
        factor_df, factor_returns_df = factor_data
        # forward_returns is a Series (date -> return), not a DataFrame
        forward_returns = factor_returns_df.mean(axis=1)
        config = FactorOptimizerConfig()
        result = optimize_factor_portfolio(factor_df, forward_returns, config)
        assert isinstance(result, dict)
        assert "weights" in result
        assert "ic_values" in result
        assert "sharpe" in result
        assert len(result["weights"]) == factor_df.shape[1]
