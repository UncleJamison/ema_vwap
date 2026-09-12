"""
Timezone, US Market Calendar, and Session Normalization Pipeline.
Provides UTC normalization, US/Eastern timezone conversion, market holiday tracking,
and session-aware VWAP anchor grouping for both 24/7 Crypto and NYSE/NASDAQ Equities.
"""

import datetime as dt

import pandas as pd

# Standard US NYSE / NASDAQ Market Holidays (Observed)
# Rules: New Year's Day (Jan 1), Martin Luther King Jr. Day (3rd Mon in Jan),
# Washington's Birthday / Presidents' Day (3rd Mon in Feb), Good Friday (Easter - 2 days),
# Memorial Day (Last Mon in May), Juneteenth (Jun 19), Independence Day (Jul 4),
# Labor Day (1st Mon in Sep), Thanksgiving Day (4th Thu in Nov), Christmas Day (Dec 25).


def _get_nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> dt.date:
    """Find the nth occurrence of a weekday in a given month (1-indexed n). Monday is 0."""
    first_day = dt.date(year, month, 1)
    day_diff = (weekday - first_day.weekday()) % 7
    first_target = first_day + dt.timedelta(days=day_diff)
    return first_target + dt.timedelta(weeks=n - 1)


def _get_last_weekday_of_month(year: int, month: int, weekday: int) -> dt.date:
    """Find the last occurrence of a weekday in a given month."""
    if month == 12:
        next_month_first = dt.date(year + 1, 1, 1)
    else:
        next_month_first = dt.date(year, month + 1, 1)
    last_day = next_month_first - dt.timedelta(days=1)
    day_diff = (last_day.weekday() - weekday) % 7
    return last_day - dt.timedelta(days=day_diff)


def _get_easter_sunday(year: int) -> dt.date:
    """Calculate Easter Sunday using the Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    L = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * L) // 451
    month = (h + L - 7 * m + 114) // 31
    day = ((h + L - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def get_nyse_holidays(year: int) -> set[dt.date]:
    """Calculate all official NYSE/NASDAQ closed market holidays for a given year."""
    holidays: set[dt.date] = set()

    # 1. New Year's Day (Jan 1, observed on Dec 31 if Sat, Jan 2 if Sun)
    nyd = dt.date(year, 1, 1)
    if nyd.weekday() == 5:
        holidays.add(dt.date(year - 1, 12, 31))
    elif nyd.weekday() == 6:
        holidays.add(dt.date(year, 1, 2))
    else:
        holidays.add(nyd)

    # 2. Martin Luther King Jr. Day (Third Monday in January)
    holidays.add(_get_nth_weekday_of_month(year, 1, 0, 3))

    # 3. Washington's Birthday / Presidents' Day (Third Monday in February)
    holidays.add(_get_nth_weekday_of_month(year, 2, 0, 3))

    # 4. Good Friday (Friday before Easter Sunday)
    easter = _get_easter_sunday(year)
    holidays.add(easter - dt.timedelta(days=2))

    # 5. Memorial Day (Last Monday in May)
    holidays.add(_get_last_weekday_of_month(year, 5, 0))

    # 6. Juneteenth National Independence Day (June 19, observed)
    june19 = dt.date(year, 6, 19)
    if june19.weekday() == 5:
        holidays.add(dt.date(year, 6, 18))
    elif june19.weekday() == 6:
        holidays.add(dt.date(year, 6, 20))
    else:
        holidays.add(june19)

    # 7. Independence Day (July 4, observed)
    jul4 = dt.date(year, 7, 4)
    if jul4.weekday() == 5:
        holidays.add(dt.date(year, 7, 3))
    elif jul4.weekday() == 6:
        holidays.add(dt.date(year, 7, 5))
    else:
        holidays.add(jul4)

    # 8. Labor Day (First Monday in September)
    holidays.add(_get_nth_weekday_of_month(year, 9, 0, 1))

    # 9. Thanksgiving Day (Fourth Thursday in November)
    holidays.add(_get_nth_weekday_of_month(year, 11, 3, 4))

    # 10. Christmas Day (December 25, observed)
    xmas = dt.date(year, 12, 25)
    if xmas.weekday() == 5:
        holidays.add(dt.date(year, 12, 24))
    elif xmas.weekday() == 6:
        holidays.add(dt.date(year, 12, 26))
    else:
        holidays.add(xmas)

    return holidays


def is_nyse_holiday(d: dt.date | pd.Timestamp | str) -> bool:
    """Check if a date is an official NYSE market holiday."""
    if isinstance(d, str):
        parsed = pd.to_datetime(d).date()
    elif isinstance(d, pd.Timestamp):
        parsed = d.date()
    else:
        parsed = d
    holidays = get_nyse_holidays(parsed.year)
    return parsed in holidays


def to_utc_series(
    timestamps: pd.Series | pd.DatetimeIndex | list,
    source_tz: str | None = None,
) -> pd.Series:
    """
    Convert any timestamp series, sequence, or datetime index into UTC-normalized pandas Series.
    """
    s = pd.Series(timestamps)
    if not pd.api.types.is_datetime64_any_dtype(s):
        if pd.api.types.is_numeric_dtype(s):
            max_val = s.max() if len(s) > 0 else 0
            unit = "ms" if max_val > 1e11 else "s"
            s = pd.to_datetime(s, unit=unit, utc=True)
        else:
            s = pd.to_datetime(s, utc=True)
    elif s.dt.tz is None:
        if source_tz:
            s = s.dt.tz_localize(source_tz).dt.tz_convert("UTC")
        else:
            s = s.dt.tz_localize("UTC")
    else:
        s = s.dt.tz_convert("UTC")
    return s


def utc_to_market_tz(
    timestamps: pd.Series | pd.DatetimeIndex | pd.DataFrame,
    target_tz: str = "America/New_York",
) -> pd.Series | pd.DataFrame:
    """
    Convert UTC timestamps to the target market timezone (e.g. 'America/New_York').
    """
    if isinstance(timestamps, pd.DataFrame):
        df = timestamps.copy()
        if "timestamp" in df.columns:
            ts = to_utc_series(df["timestamp"])
            df["timestamp"] = ts.dt.tz_convert(target_tz)
        return df

    s = to_utc_series(timestamps)
    return s.dt.tz_convert(target_tz)


def is_market_hours(
    timestamp: pd.Timestamp | pd.Series | str,
    market_tz: str = "America/New_York",
    session_type: str = "rth",
    filter_holidays: bool = False,
) -> bool | pd.Series:
    """
    Check whether a timestamp falls within US market trading hours and non-holidays.

    Session types:
    - 'rth': Regular Trading Hours (09:30 - 16:00 ET, Monday-Friday, non-holiday)
    - 'pre': Pre-market (04:00 - 09:30 ET, Monday-Friday)
    - 'post': After-hours (16:00 - 20:00 ET, Monday-Friday)
    - 'extended': All trading hours (04:00 - 20:00 ET, Monday-Friday)
    - 'crypto': 24/7 (Always True)
    """
    if session_type.lower() == "crypto":
        if isinstance(timestamp, pd.Series):
            return pd.Series(True, index=timestamp.index)
        return True

    is_scalar = False
    if not isinstance(timestamp, pd.Series):
        is_scalar = True
        s = pd.Series([pd.Timestamp(timestamp)])
    else:
        s = timestamp

    market_ts = to_utc_series(s).dt.tz_convert(market_tz)
    is_weekday = market_ts.dt.weekday < 5
    minutes_from_midnight = market_ts.dt.hour * 60 + market_ts.dt.minute

    session_lower = session_type.lower()
    if session_lower == "rth":
        # 09:30 (570 min) to 16:00 (960 min)
        in_time = (minutes_from_midnight >= 570) & (minutes_from_midnight < 960)
    elif session_lower == "pre":
        in_time = (minutes_from_midnight >= 240) & (minutes_from_midnight < 570)
    elif session_lower == "post":
        in_time = (minutes_from_midnight >= 960) & (minutes_from_midnight < 1200)
    elif session_lower == "extended":
        in_time = (minutes_from_midnight >= 240) & (minutes_from_midnight < 1200)
    else:
        in_time = pd.Series(True, index=market_ts.index)

    result = is_weekday & in_time

    if filter_holidays:
        # Vectorized holiday filter
        years = market_ts.dt.year.unique()
        all_holidays: set[dt.date] = set()
        for y in years:
            all_holidays.update(get_nyse_holidays(int(y)))

        dates = market_ts.dt.date
        is_holiday = dates.isin(all_holidays)
        result = result & (~is_holiday)

    if is_scalar:
        return bool(result.iloc[0])
    return result


def filter_market_hours(
    df: pd.DataFrame,
    session_type: str = "rth",
    market_tz: str = "America/New_York",
    filter_holidays: bool = True,
) -> pd.DataFrame:
    """Filter DataFrame rows to only include bars within specified market trading session."""
    if df.empty or "timestamp" not in df.columns or session_type.lower() == "crypto":
        return df

    mask = is_market_hours(
        df["timestamp"],
        market_tz=market_tz,
        session_type=session_type,
        filter_holidays=filter_holidays,
    )
    return df[mask].reset_index(drop=True)


def get_session_id(
    timestamps: pd.Series | pd.DatetimeIndex,
    market_tz: str = "America/New_York",
    asset_type: str = "crypto",
) -> pd.Series:
    """
    Generate a session identifier series for grouping intraday bars into daily trading sessions.
    - For crypto: Groups by UTC date ('YYYY-MM-DD').
    - For US stocks: Groups by trading day anchored at 09:30 AM US/Eastern.
      Bars before 09:30 ET are attributed to previous session or labeled pre-market;
      bars between 09:30 ET and next 09:30 ET belong to that trading session.
    """
    ts_utc = to_utc_series(timestamps)

    if asset_type.lower() == "stock":
        ts_ny = ts_utc.dt.tz_convert(market_tz)
        # Shift timestamps back by 9 hours and 30 minutes so that 09:30 AM ET marks the start of the date
        shifted = ts_ny - pd.Timedelta(hours=9, minutes=30)
        return shifted.dt.strftime("%Y-%m-%d")
    else:
        return ts_utc.dt.strftime("%Y-%m-%d")


def get_session_anchor_utc(
    timestamp: pd.Timestamp | str,
    market_tz: str = "America/New_York",
    asset_type: str = "crypto",
) -> pd.Timestamp:
    """
    Get the daily session anchor start timestamp in UTC for VWAP resets.
    - For US stocks: 09:30 AM US/Eastern on the trading day converted to UTC.
    - For crypto: 00:00:00 UTC midnight.
    """
    ts = pd.Timestamp(timestamp)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")

    if asset_type.lower() == "stock":
        local_ts = ts.tz_convert(market_tz)
        anchor_local = local_ts.replace(hour=9, minute=30, second=0, microsecond=0)
        if local_ts < anchor_local:
            anchor_local = (local_ts - pd.Timedelta(days=1)).replace(
                hour=9, minute=30, second=0, microsecond=0
            )
        return anchor_local.tz_convert("UTC")
    else:
        return ts.floor("D")
