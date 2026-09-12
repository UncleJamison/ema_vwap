"""
Unit and integration tests for NYSE/NASDAQ Market Calendar,
Holiday exclusions, Market Hours filtering, and 09:30 AM ET Session-Anchored VWAP.
"""

import datetime as dt

import pandas as pd

from src.config import StrategyParams
from src.indicators import compute_all_indicators, compute_vwap
from src.providers.stock_synthetic import SyntheticStockAdapter
from src.providers.timezone import (
    filter_market_hours,
    get_nyse_holidays,
    get_session_id,
    is_market_hours,
    is_nyse_holiday,
)


def test_nyse_holidays_calculation():
    """Verify standard NYSE market holiday dates for 2026."""
    holidays_2026 = get_nyse_holidays(2026)

    # 1. New Year's Day: Jan 1, 2026
    assert dt.date(2026, 1, 1) in holidays_2026

    # 2. MLK Jr Day: 3rd Mon of Jan 2026 -> Jan 19, 2026
    assert dt.date(2026, 1, 19) in holidays_2026

    # 3. Presidents' Day: 3rd Mon of Feb 2026 -> Feb 16, 2026
    assert dt.date(2026, 2, 16) in holidays_2026

    # 4. Good Friday: Easter is Apr 5, 2026 -> Good Friday is Apr 3, 2026
    assert dt.date(2026, 4, 3) in holidays_2026

    # 5. Memorial Day: Last Mon of May 2026 -> May 25, 2026
    assert dt.date(2026, 5, 25) in holidays_2026

    # 6. Juneteenth: Jun 19, 2026 (Friday)
    assert dt.date(2026, 6, 19) in holidays_2026

    # 7. Independence Day: Jul 4, 2026 (Saturday -> observed Jul 3)
    assert dt.date(2026, 7, 3) in holidays_2026

    # 8. Labor Day: 1st Mon of Sep 2026 -> Sep 7, 2026
    assert dt.date(2026, 9, 7) in holidays_2026

    # 9. Thanksgiving Day: 4th Thu of Nov 2026 -> Nov 26, 2026
    assert dt.date(2026, 11, 26) in holidays_2026

    # 10. Christmas Day: Dec 25, 2026 (Friday)
    assert dt.date(2026, 12, 25) in holidays_2026

    # Helper function check
    assert is_nyse_holiday("2026-01-01") is True
    assert is_nyse_holiday("2026-08-31") is False  # Regular trading day


def test_market_hours_and_filtering():
    """Verify is_market_hours and filter_market_hours correctly filter RTH, pre-market, and weekends."""
    # 09:30 AM ET on Monday Aug 31, 2026 is 13:30 UTC
    ts_open = "2026-08-31T13:30:00+00:00"
    # 12:00 PM ET is 16:00 UTC
    ts_mid = "2026-08-31T16:00:00+00:00"
    # 04:05 PM ET is 20:05 UTC (after-hours)
    ts_after = "2026-08-31T20:05:00+00:00"
    # Sunday
    ts_sun = "2026-08-30T15:00:00+00:00"
    # Thanksgiving
    ts_thanksgiving = "2026-11-26T15:00:00+00:00"

    assert is_market_hours(ts_open, session_type="rth") is True
    assert is_market_hours(ts_mid, session_type="rth") is True
    assert is_market_hours(ts_after, session_type="rth") is False
    assert is_market_hours(ts_after, session_type="extended") is True
    assert is_market_hours(ts_sun, session_type="rth") is False
    assert (
        is_market_hours(ts_thanksgiving, session_type="rth", filter_holidays=True)
        is False
    )

    # Filter dataframe
    df = pd.DataFrame(
        {
            "timestamp": [ts_open, ts_mid, ts_after, ts_sun, ts_thanksgiving],
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [102.0, 103.0, 104.0, 105.0, 106.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0],
            "close": [101.0, 102.0, 103.0, 104.0, 105.0],
            "volume": [1000, 1000, 1000, 1000, 1000],
        }
    )
    df_filtered = filter_market_hours(df, session_type="rth")
    assert len(df_filtered) == 2  # Only ts_open and ts_mid pass RTH


def test_session_id_generation():
    """Verify session IDs correctly group bars starting at 09:30 AM ET."""
    # Day 1: 09:30 ET (13:30 UTC), 15:55 ET (19:55 UTC)
    # Day 2: 09:30 ET (13:30 UTC next day)
    ts_list = [
        "2026-08-31T13:30:00+00:00",
        "2026-08-31T19:55:00+00:00",
        "2026-09-01T13:30:00+00:00",
    ]
    s_ids = get_session_id(pd.Series(ts_list), asset_type="stock")
    assert s_ids.iloc[0] == s_ids.iloc[1]  # Same trading day session
    assert (
        s_ids.iloc[0] != s_ids.iloc[2]
    )  # New trading day session starting at 09:30 ET


def test_stock_vwap_reset_at_market_open():
    """Verify VWAP resets precisely at 09:30 AM ET on each trading day."""
    adapter = SyntheticStockAdapter()
    df = adapter.fetch_historical_candles(symbol="AAPL", timeframe="5m", days=3)

    # Compute stock session VWAP
    vwap_series = compute_vwap(df, anchor="US_EQUITY", asset_type="stock")

    assert not vwap_series.isna().any()
    assert len(vwap_series) == len(df)

    # First bar of each day (at 13:30 UTC / 09:30 ET) should have VWAP == Typical Price
    # because it is the initial bar of the session reset
    ts_dt = pd.to_datetime(df["timestamp"], utc=True)
    day_starts = df.index[(ts_dt.dt.hour == 13) & (ts_dt.dt.minute == 30)].tolist()
    assert len(day_starts) >= 2

    for idx in day_starts:
        typical_p = (
            df["high"].iloc[idx] + df["low"].iloc[idx] + df["close"].iloc[idx]
        ) / 3.0
        assert round(vwap_series.iloc[idx], 4) == round(typical_p, 4)


def test_compute_all_indicators_with_stock_strategy_params():
    """Verify compute_all_indicators calculates EMAs, ATR, and session VWAP cleanly for stocks."""
    adapter = SyntheticStockAdapter()
    df = adapter.fetch_historical_candles(symbol="AAPL", timeframe="5m", days=3)

    params = StrategyParams(
        fast_ema=9,
        slow_ema=21,
        trend_ema=50,
        asset_type="stock",
        vwap_anchor="US_EQUITY",
    )
    df_ind = compute_all_indicators(df, params)

    assert "ema_9" in df_ind.columns
    assert "ema_21" in df_ind.columns
    assert "ema_50" in df_ind.columns
    assert "vwap" in df_ind.columns
    assert "vwap_slope" in df_ind.columns
    assert "atr" in df_ind.columns
    assert "vol_sma" in df_ind.columns
    assert not df_ind["vwap"].isna().any()
