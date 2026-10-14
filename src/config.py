"""
Configuration module for the EMA + VWAP Crypto Trading System.
Defines parameter dataclasses, exchange configurations, and default settings.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

MAX_OPTUNA_WORKERS: int = 4


def get_optimal_lookback_days(timeframe: str, category: str = "crypto") -> int:
    """Calculate optimal lookback days based on timeframe and asset category.
    Returns the default_lookback_days from TimeframeSpec (tf_lookbacks).
    """
    # Use the same defaults as get_timeframe_spec tf_lookbacks
    tf = timeframe.lower()
    crypto_lookbacks = {
        "1m": 30,
        "3m": 60,
        "5m": 90,
        "15m": 180,
        "30m": 250,
        "1h": 900,
        "2h": 540,
        "4h": 350,
        "6h": 540,
        "8h": 540,
        "12h": 350,
        "1d": 1800,
        "1w": 10950,
    }
    return crypto_lookbacks.get(tf, 90)


@dataclass
class TimeframeSpec:
    """Timeframe specification with lookback windows and bars per day."""

    bars_per_day_crypto: int = 60
    bars_per_day_stock: int = 30
    bars_per_day_synthetic: int = 45
    min_lookback_days: int = 1
    max_lookback_days: int = 3650
    default_lookback_days: int = 30


def get_timeframe_spec(timeframe: str, category: str = "crypto") -> TimeframeSpec:
    """Return timeframe specification with bars per day and lookback windows."""
    # Per-timeframe bars per day (how many candles in a full trading day)
    tf_bars = {
        "1m": 1440 if category == "crypto" else 78,
        "3m": 480 if category == "crypto" else 26,
        "5m": 288 if category == "crypto" else 78,
        "15m": 96 if category == "crypto" else 39,
        "30m": 48 if category == "crypto" else 20,
        "1h": 24 if category == "crypto" else 6.5,
        "2h": 12 if category == "crypto" else 3.25,
        "4h": 6 if category == "crypto" else 3.25,
        "6h": 4 if category == "crypto" else 1.0,
        "8h": 3 if category == "crypto" else 1.0,
        "12h": 2 if category == "crypto" else 1.0,
        "1d": 1.0,
    }
    # Lookback windows (in days)
    tf_lookbacks = {
        "1m": (14, 60, 30),
        "3m": (30, 90, 60),
        "5m": (60, 180, 90),
        "15m": (180, 365, 180),
        "30m": (90, 500, 250),
        "1h": (730, 1095, 900),
        "2h": (365, 730, 540),
        "4h": (183, 500, 350),
        "6h": (365, 730, 540),
        "8h": (365, 730, 540),
        "12h": (183, 500, 350),
        "1d": (1825, 3650, 1800),
    }
    bars_per_day = tf_bars.get(timeframe, 60 if category == "crypto" else 30)

    min_lb, max_lb, default_lb = tf_lookbacks.get(
        timeframe, (60, 180, 180)  # fallback to 5m spec for unknown timeframes
    )

    spec = TimeframeSpec(
        bars_per_day_crypto=1440 if category == "crypto" else 30,
        bars_per_day_stock=30 if category == "crypto" else 78,
        min_lookback_days=min_lb,
        max_lookback_days=max_lb,
        default_lookback_days=default_lb,
    )
    # Override bars_per_day based on category
    if category == "crypto":
        spec.bars_per_day_crypto = int(bars_per_day)
    else:
        spec.bars_per_day_stock = float(bars_per_day)

    return spec


def get_bars_per_day(timeframe: str, category: str = "crypto") -> int:
    """
    Return the number of bars per day for a given timeframe and category.
    """
    tf_bars = {
        "1m": 1440 if category == "crypto" else 78,
        "3m": 480 if category == "crypto" else 26,
        "5m": 288 if category == "crypto" else 78,
        "15m": 96 if category == "crypto" else 39,
        "30m": 48 if category == "crypto" else 20,
        "1h": 24 if category == "crypto" else 6.5,
        "2h": 12 if category == "crypto" else 3.25,
        "4h": 6 if category == "crypto" else 3.25,
        "6h": 4 if category == "crypto" else 1.0,
        "1d": 1.0,
    }
    bars_per_day = tf_bars.get(timeframe, 60 if category == "crypto" else 30)
    return int(bars_per_day)


@dataclass
class StrategyParams:
    """Strategy parameters with full UI/Config customizability."""

    strategy_mode: Literal["crossover", "multi_ema", "pullback", "auto"] = "crossover"
    trade_direction: Literal["both", "long_short", "long_only", "short_only"] = (
        "long_only"
    )

    # EMA Periods
    fast_ema: int = 9
    slow_ema: int = 21
    trend_ema: int = 50  # or 55

    # Filters & Gates
    vwap_slope_min: float = (
        0.0001  # Minimum normalized slope magnitude to filter flat VWAP
    )
    vwap_slope_lookback: int = 5
    volume_filter_enabled: bool = True
    volume_multiplier: float = 1.2  # Volume must be >= 1.2x 20-period volume SMA
    volume_sma_period: int = 20

    # VWAP Pullback Settings
    pullback_tolerance_pct: float = (
        0.3  # % distance from VWAP considered a "pullback touch"
    )

    # Risk Management & Stop Loss
    stop_loss_type: Literal["vwap", "atr"] = "vwap"
    vwap_stop_offset_pct: float = 0.1  # Offset % added beyond VWAP line for stop loss
    atr_period: int = 14
    atr_multiplier: float = 2.0  # ATR trailing stop multiplier
    trailing_stop_pct: float = (
        1.5  # Trailing stop % of price (profit lock); 0 to disable
    )
    risk_reward_ratio: float = 2.0  # Target R:R (e.g., 2.0 means Take Profit = 2x Risk)
    risk_per_trade_pct: float = 1.0  # Risk % of account equity per trade
    max_position_pct: float = (
        80.0  # Max total position value as % of equity (portfolio cap)
    )
    max_holding_bars: int | None = (
        None  # Maximum bars to hold before forced time-based exit (None = disabled)
    )

    # Account & Execution
    initial_capital: float = 10000.0
    maker_fee_pct: float = 0.05
    taker_fee_pct: float = 0.075
    slippage_pct: float = 0.05

    # Market Type & Session Anchors
    asset_type: Literal["crypto", "stock", "synthetic"] = "crypto"
    session_mode: Literal["rth", "extended", "crypto", "all"] = "crypto"
    vwap_anchor: Literal["D", "session", "US_EQUITY", "UTC"] = "D"

    # Stock Execution & Regulatory Constraints (Intraday Margin Framework)
    allow_fractional_shares: bool = True
    enforce_margin_calls: bool = (
        False  # Real-time intraday margin monitoring (4:1 longs, 2:1 shorts)
    )
    min_margin_equity: float = (
        2000.0  # Minimum account equity (SEC-approved change, effective June 4, 2026)
    )
    long_buying_power_ratio: float = 4.0  # 4:1 for long positions
    short_buying_power_ratio: float = 2.0  # 2:1 for short positions
    sec_fee_per_million: float = 27.80
    finra_taf_per_share: float = 0.000166
    # Fixed lot size (shares/units) used for margin affordability check.
    # 0.0 = disabled (rely on risk-manager sizing); positive value = fixed lot.
    position_size: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_mode": self.strategy_mode,
            "trade_direction": self.trade_direction,
            "fast_ema": self.fast_ema,
            "slow_ema": self.slow_ema,
            "trend_ema": self.trend_ema,
            "vwap_slope_min": self.vwap_slope_min,
            "vwap_slope_lookback": self.vwap_slope_lookback,
            "volume_filter_enabled": self.volume_filter_enabled,
            "volume_multiplier": self.volume_multiplier,
            "volume_sma_period": self.volume_sma_period,
            "pullback_tolerance_pct": self.pullback_tolerance_pct,
            "stop_loss_type": self.stop_loss_type,
            "vwap_stop_offset_pct": self.vwap_stop_offset_pct,
            "atr_period": self.atr_period,
            "atr_multiplier": self.atr_multiplier,
            "risk_reward_ratio": self.risk_reward_ratio,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_holding_bars": self.max_holding_bars,
            "initial_capital": self.initial_capital,
            "maker_fee_pct": self.maker_fee_pct,
            "taker_fee_pct": self.taker_fee_pct,
            "slippage_pct": self.slippage_pct,
            "asset_type": self.asset_type,
            "session_mode": self.session_mode,
            "vwap_anchor": self.vwap_anchor,
            "allow_fractional_shares": self.allow_fractional_shares,
            "enforce_margin_calls": self.enforce_margin_calls,
            "min_margin_equity": self.min_margin_equity,
            "long_buying_power_ratio": self.long_buying_power_ratio,
            "short_buying_power_ratio": self.short_buying_power_ratio,
            "sec_fee_per_million": self.sec_fee_per_million,
            "finra_taf_per_share": self.finra_taf_per_share,
            "position_size": self.position_size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StrategyParams":
        valid_keys = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class ExchangeConfig:
    """Exchange configuration specifying Gemini and KuCoin settings."""

    primary_exchange: str = "gemini"  # "gemini" or "kucoin" or "synthetic"
    default_symbol: str = "BTC/USD"
    default_timeframe: str = "5m"  # "1m", "5m", "15m", "1h", "1d"
    limit: int = 500


@dataclass
class OptunaConfig:
    """Configuration options for Optuna strategy hyperparameter optimization."""

    target_metric: Literal[
        "sharpe_ratio",
        "calmar_ratio",
        "sortino_ratio",
        "drawdown_penalized_sharpe",
        "drawdown_penalized_sortino",
        "ulcer_index",
        "profit_factor",
        "net_profit",
        "total_return_pct",
        "win_rate",
    ] = "sharpe_ratio"
    n_trials: int = 50
    timeout_seconds: int | None = None
    min_trades: int = 0
    n_startup_trials: int | None = None
    n_jobs: int = 1
    multivariate: bool = True
    seed_base_params: bool = True

    # Parameter Search Ranges (widened and expanded)
    fast_ema_min: int = 3
    fast_ema_max: int = 30
    slow_ema_min: int = 10
    slow_ema_max: int = 100
    trend_ema_min: int = 50
    trend_ema_max: int = 250

    vwap_slope_min: float = 0.00001
    vwap_slope_max: float = 0.003

    volume_multiplier_min: float = 0.8
    sampler_seed: int | None = 42
    enable_warmstart: bool = False  # ema_vwap-ddgr: opt-in warm-start from prior sweeps
    execution_mode: str = "sequential"
    n_workers: int = 1
    volume_multiplier_max: float = 3.5

    atr_multiplier_min: float = 2.0
    atr_multiplier_max: float = 5.0

    risk_reward_min: float = 1.0
    risk_reward_max: float = 6.0

    pullback_tolerance_min: float = 0.05
    pullback_tolerance_max: float = 1.5

    vwap_stop_offset_min: float = 0.02
    vwap_stop_offset_max: float = 0.8

    risk_per_trade_min: float = 0.5
    risk_per_trade_max: float = 3.0

    # Max Holding Bars (time-based stop, None = disabled)
    enable_max_holding_bars: bool = True  # Set True to sweep max_holding_bars
    max_holding_bars_min: int = 4  # Minimum bars before forced exit
    max_holding_bars_max: int = 96  # Maximum bars before forced exit

    # Trailing Stop % of Price (profit lock; 0 to disable)
    trailing_stop_min: float = 1.0  # Min trailing stop % of price
    trailing_stop_max: float = 3.0  # Max trailing stop % of price

    # Hard Drawdown Constraints (Optuna pruning)
    enable_hard_drawdown_constraint: bool = False
    max_drawdown_constraint_pct: float = 25.0

    # Portfolio Position Cap (max total position value as % of equity)
    max_position_pct_min: float = 25.0
    max_position_pct_max: float = 100.0

    # Multi-Objective Optimization (Pareto Frontier)
    enable_multi_objective: bool = False
    multi_objective_metrics: list[str] = field(
        default_factory=lambda: ["sharpe_ratio", "max_drawdown_pct"]
    )

    # Regime-adaptive sweep settings (ema_vwap-0tj)
    regime_sweep_enabled: bool = False
    regime_sweep_days: int = 180
    regime_sweep_n_jobs: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_metric": self.target_metric,
            "n_trials": self.n_trials,
            "timeout_seconds": self.timeout_seconds,
            "min_trades": self.min_trades,
            "n_startup_trials": self.n_startup_trials,
            "n_jobs": self.n_jobs,
            "multivariate": self.multivariate,
            "seed_base_params": self.seed_base_params,
            "fast_ema_min": self.fast_ema_min,
            "fast_ema_max": self.fast_ema_max,
            "slow_ema_min": self.slow_ema_min,
            "slow_ema_max": self.slow_ema_max,
            "trend_ema_min": self.trend_ema_min,
            "trend_ema_max": self.trend_ema_max,
            "vwap_slope_min": self.vwap_slope_min,
            "vwap_slope_max": self.vwap_slope_max,
            "volume_multiplier_min": self.volume_multiplier_min,
            "volume_multiplier_max": self.volume_multiplier_max,
            "atr_multiplier_min": self.atr_multiplier_min,
            "atr_multiplier_max": self.atr_multiplier_max,
            "risk_reward_min": self.risk_reward_min,
            "risk_reward_max": self.risk_reward_max,
            "pullback_tolerance_min": self.pullback_tolerance_min,
            "pullback_tolerance_max": self.pullback_tolerance_max,
            "vwap_stop_offset_min": self.vwap_stop_offset_min,
            "vwap_stop_offset_max": self.vwap_stop_offset_max,
            "risk_per_trade_min": self.risk_per_trade_min,
            "risk_per_trade_max": self.risk_per_trade_max,
            "enable_max_holding_bars": self.enable_max_holding_bars,
            "max_holding_bars_min": self.max_holding_bars_min,
            "max_holding_bars_max": self.max_holding_bars_max,
            "enable_hard_drawdown_constraint": self.enable_hard_drawdown_constraint,
            "max_drawdown_constraint_pct": self.max_drawdown_constraint_pct,
            "enable_multi_objective": self.enable_multi_objective,
            "multi_objective_metrics": self.multi_objective_metrics,
            "regime_sweep_enabled": self.regime_sweep_enabled,
            "regime_sweep_days": self.regime_sweep_days,
            "regime_sweep_n_jobs": self.regime_sweep_n_jobs,
            "sampler_seed": self.sampler_seed,
            "execution_mode": self.execution_mode,
            "n_workers": self.n_workers,
        }


@dataclass
class WFOConfig:
    """Configuration options for Walk-Forward Optimization."""

    num_windows: int = 5
    in_sample_ratio: float = 0.7
    window_type: Literal["rolling", "anchored"] = "rolling"
    trials_per_window: int = 30

    def to_dict(self) -> dict[str, Any]:
        return {
            "num_windows": self.num_windows,
            "in_sample_ratio": self.in_sample_ratio,
            "window_type": self.window_type,
            "trials_per_window": self.trials_per_window,
        }


@dataclass
class MonteCarloConfig:
    """Configuration options for Monte Carlo risk & equity curve simulations."""

    num_simulations: int = 1000
    sample_with_replacement: bool = True
    confidence_levels: list[float] = field(default_factory=lambda: [5.0, 50.0, 95.0])
    random_seed: int | None = 42

    def to_dict(self) -> dict[str, Any]:
        return {
            "num_simulations": self.num_simulations,
            "sample_with_replacement": self.sample_with_replacement,
            "confidence_levels": self.confidence_levels,
            "random_seed": self.random_seed,
        }


def get_vwap_slope_bounds(timeframe: str) -> dict:
    """Return static VWAP slope bounds for the given timeframe."""
    return {
        "vwap_slope_min": 0.0001,
        "vwap_slope_max": 0.05,
    }


def get_ema_bounds(tf: str, asset_type: str = "crypto") -> dict:
    """Compute EMA search bounds by timeframe and asset class."""
    return {
        "fast_ema_min": 3,
        "fast_ema_max": 30,
        "slow_ema_min": 10,
        "slow_ema_max": 60,
        "trend_ema_min": 20,
        "trend_ema_max": 120,
    }
