"""Strategy Signal Engine for EMA + VWAP Trading System.
Implements Crossover, Multi-EMA Confirmation, and VWAP Pullback setups with VWAP Slope and Volume filters.
"""

import pandas as pd

from src.config import StrategyParams
from src.indicators import compute_all_indicators
from src.regime import get_current_regime, regime_to_strategy_mode


class EmaVwapStrategy:
    """Generates trade entry and exit signals based on EMA + VWAP setup rules."""

    def __init__(self, params: StrategyParams):
        self.params = params

    def evaluate_filters(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """
        Evaluate VWAP Slope gate and Volume Confirmation gate.
        Returns boolean series (slope_pass, volume_pass).
        """
        # Slope gate: Slope magnitude must be >= min threshold
        if self.params.vwap_slope_min > 0:
            slope_pass = df["vwap_slope"].abs() >= self.params.vwap_slope_min
        else:
            slope_pass = pd.Series(True, index=df.index)

        # Volume gate: Volume >= multiplier * Volume SMA
        if self.params.volume_filter_enabled:
            volume_pass = df["volume"] >= (
                self.params.volume_multiplier * df["vol_sma"]
            )
        else:
            volume_pass = pd.Series(True, index=df.index)

        return slope_pass, volume_pass

    def generate_crossover_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        Setup 1: Fast EMA x VWAP Crossover.
        Long: Fast EMA crosses above VWAP and Close > VWAP.
        Short: Fast EMA crosses below VWAP and Close < VWAP.
        """
        fast_ema = df[f"ema_{self.params.fast_ema}"]
        vwap = df["vwap"]

        fast_prev = fast_ema.shift(1)
        vwap_prev = vwap.shift(1)

        cross_above = (fast_prev <= vwap_prev) & (fast_ema > vwap)
        cross_below = (fast_prev >= vwap_prev) & (fast_ema < vwap)

        long_cond = cross_above & (df["close"] > vwap)
        short_cond = cross_below & (df["close"] < vwap)

        signals = pd.Series(0, index=df.index)
        signals[long_cond] = 1
        signals[short_cond] = -1
        return signals

    def generate_multi_ema_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        Setup 2: Multi-EMA Confirmation.
        Long: Fast EMA crosses above Slow EMA AND Trend EMA > VWAP AND Close > VWAP.
        Short: Fast EMA crosses below Slow EMA AND Trend EMA < VWAP AND Close < VWAP.
        """
        fast_ema = df[f"ema_{self.params.fast_ema}"]
        slow_ema = df[f"ema_{self.params.slow_ema}"]
        trend_ema = df[f"ema_{self.params.trend_ema}"]
        vwap = df["vwap"]

        fast_prev = fast_ema.shift(1)
        slow_prev = slow_ema.shift(1)

        ema_cross_above = (fast_prev <= slow_prev) & (fast_ema > slow_ema)
        ema_cross_below = (fast_prev >= slow_prev) & (fast_ema < slow_ema)

        long_cond = ema_cross_above & (trend_ema > vwap) & (df["close"] > vwap)
        short_cond = ema_cross_below & (trend_ema < vwap) & (df["close"] < vwap)

        signals = pd.Series(0, index=df.index)
        signals[long_cond] = 1
        signals[short_cond] = -1
        return signals

    def generate_pullback_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        Setup 3: VWAP Pullback Entries.
        Long: Trend is uptrend (Price & Fast EMA > VWAP), Price Low pulls back within tolerance % of VWAP,
              and momentum resumes (Close > Fast EMA).
        Short: Trend is downtrend (Price & Fast EMA < VWAP), Price High pulls back within tolerance % of VWAP,
               and momentum resumes (Close < Fast EMA).
        """
        fast_ema = df[f"ema_{self.params.fast_ema}"]
        vwap = df["vwap"]
        close = df["close"]
        high = df["high"]
        low = df["low"]

        tolerance = self.params.pullback_tolerance_pct / 100.0

        # Long pullback: Price Low is near VWAP (between VWAP*(1-tol) and VWAP*(1+tol))
        near_vwap_long = (low <= vwap * (1.0 + tolerance)) & (
            low >= vwap * (1.0 - 2.0 * tolerance)
        )
        uptrend = (fast_ema > vwap) & (close > vwap)
        recovery_long = (close.shift(1) <= fast_ema.shift(1)) & (close > fast_ema)
        long_cond = uptrend & near_vwap_long & recovery_long

        # Short pullback: Price High is near VWAP
        near_vwap_short = (high >= vwap * (1.0 - tolerance)) & (
            high <= vwap * (1.0 + 2.0 * tolerance)
        )
        downtrend = (fast_ema < vwap) & (close < vwap)
        recovery_short = (close.shift(1) >= fast_ema.shift(1)) & (close < fast_ema)
        short_cond = downtrend & near_vwap_short & recovery_short

        signals = pd.Series(0, index=df.index)
        signals[long_cond] = 1
        signals[short_cond] = -1
        return signals

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Applies requested setup and filters df with all technical indicators.
        Returns DataFrame with 'signal' column (1 = Buy, -1 = Sell, 0 = Hold).
        When strategy_mode='auto', regime is detected via get_current_regime and
        mapped to a strategy mode via REGIME_MAP (trending->multi_ema,
        ranging->pullback, high_vol->crossover).
        """
        df = compute_all_indicators(df, self.params)

        if self.params.strategy_mode == "auto":
            regime = get_current_regime(df)
            effective_mode = regime_to_strategy_mode(regime)
            df["regime"] = pd.Series([regime] * len(df))
        else:
            effective_mode = self.params.strategy_mode
            df["regime"] = pd.Series(["static"] * len(df))

        if effective_mode == "multi_ema":
            raw_signals = self.generate_multi_ema_signals(df)
        elif effective_mode == "pullback":
            raw_signals = self.generate_pullback_signals(df)
        else:
            raw_signals = self.generate_crossover_signals(df)

        # Apply VWAP Slope & Volume Gates
        slope_pass, volume_pass = self.evaluate_filters(df)
        valid_signals = raw_signals.copy()

        # Suppress signals that do not pass filters
        valid_signals[~(slope_pass & volume_pass)] = 0

        # Apply Trade Direction Filter (Long-Only vs Short-Only vs Long-Short vs Both)
        if self.params.trade_direction == "long_only":
            valid_signals[valid_signals == -1] = 0
        elif self.params.trade_direction == "short_only":
            valid_signals[valid_signals == 1] = 0
        # "both" and "long_short": allow all signals (no filtering)

        df["raw_signal"] = raw_signals
        df["signal"] = valid_signals
        df["slope_pass"] = slope_pass
        df["volume_pass"] = volume_pass

        return df
