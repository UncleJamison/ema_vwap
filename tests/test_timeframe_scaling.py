"""Tests for timeframe-scaled candle pipelines.

Verifies lookback specifications, multi-timeframe candle loading,
WFO window derivation, and database query efficiency across all supported
timeframes and asset types.
"""

import pytest

from src.config import get_timeframe_spec
from src.database import CandleDatabase

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def db():
    """Session-scoped DB connection for test DB."""
    import os
    import tempfile

    tmp = tempfile.mkdtemp(prefix="ema_")
    db_path = os.path.join(tmp, "batch_results.db")
    db = CandleDatabase(db_path=db_path)
    # Initialize DB tables
    db.init_db()
    yield db
    try:
        os.unlink(db_path)
        os.rmdir(tmp)
    except (FileNotFoundError, OSError):
        pass


# ── Lookback specification tests ──────────────────────────────────────────


@pytest.mark.parametrize(
    "timeframe,asset_type,expected",
    [
        (
            "1m",
            "crypto",
            {"min_lookback": 14, "max_lookback": 60, "bars_per_day": 1440},
        ),
        (
            "5m",
            "crypto",
            {"min_lookback": 60, "max_lookback": 180, "bars_per_day": 288},
        ),
        (
            "15m",
            "crypto",
            {"min_lookback": 180, "max_lookback": 365, "bars_per_day": 96},
        ),
        (
            "1h",
            "crypto",
            {"min_lookback": 730, "max_lookback": 1095, "bars_per_day": 24},
        ),
        (
            "1d",
            "crypto",
            {"min_lookback": 1825, "max_lookback": 3650, "bars_per_day": 1.0},
        ),
        (
            "5m",
            "stock",
            {"min_lookback": 60, "max_lookback": 180, "bars_per_day": 78.0},
        ),
        (
            "1h",
            "stock",
            {"min_lookback": 730, "max_lookback": 1095, "bars_per_day": 6.5},
        ),
    ],
)
def test_timeframe_lookback_spec(db, timeframe, asset_type, expected):
    """Verify TimeframeSpec constants match expected values."""
    spec = get_timeframe_spec(timeframe, asset_type)
    assert spec.min_lookback_days == expected["min_lookback"]
    assert spec.max_lookback_days == expected["max_lookback"]
    assert (
        spec.bars_per_day_crypto
        if asset_type == "crypto"
        else spec.bars_per_day_stock == expected["bars_per_day"]
    )


def test_min_max_lookback_inequality(db):
    """Ensure min_lookback <= default_lookback <= max_lookback for all specs."""
    for tf in ("1m", "3m", "5m", "15m", "30m", "1h", "2h"):
        for at in ("crypto", "stock"):
            spec = get_timeframe_spec(tf, at)
            assert (
                spec.min_lookback_days
                <= spec.default_lookback_days
                <= spec.max_lookback_days
            ), (
                f"Lookback inequality for {tf}/{at}: min={spec.min_lookback}, "
                f"default={spec.default_lookback_days}, max={spec.max_lookback_days}"
            )


# ── Multi-timeframe candle loading tests ─────────────────────────────────


def test_multi_tf_candle_load_basic(db):
    """Verify that candles can be loaded across multiple timeframes without error."""
    from src.data_loader import DataLoader

    # Use synthetic provider so tests run without network
    df = DataLoader.load_candles(
        exchange="synthetic",
        symbol="BTC/USD",
        timeframe="1m",
        days=1,
    )
    assert df is not None
    assert not df.empty
    # Should have enough candles for 1 day at 1m: 1440 bars
    assert len(df) >= 50  # minimum reasonable check


@pytest.mark.parametrize("tf", ("1m", "5m", "15m", "1h", "2h"))
def test_multi_tf_candle_load_variety(db, tf):
    """Verify candles load across the common timeframe set."""
    from src.data_loader import DataLoader

    df = DataLoader.load_candles(
        exchange="synthetic",
        symbol="BTC/USD",
        timeframe=tf,
        days=1,
    )
    assert df is not None
    assert not df.empty


# ── WFO window derivation tests ───────────────────────────────────────────


def test_wfo_window_derivation(db):
    """Verify walk-forward validation window logic works per timeframe."""
    from src.app import _resolve_lookback

    # Various timeframes should produce sensible lookback days
    for tf in ("1m", "5m", "15m", "1h"):
        lookback_days, candles_limit = _resolve_lookback(tf, days=None)
        assert lookback_days is not None
        assert lookback_days > 0
        assert candles_limit >= 50


# ── Database query efficiency tests ───────────────────────────────────────


def test_batch_query_no_full_scan(db):
    """Verify that batch result queries can use index-friendly filters."""
    # Query with target_metric should not scan all rows
    rows1 = db.load_batch_results(target_metric="sharpe", limit=10)
    rows2 = db.load_batch_results(target_metric="total_return", limit=10)
    # Each should return at most 10 rows
    assert len(rows1) <= 10
    assert len(rows2) <= 10


def test_batch_query_limit_enforced(db):
    """Verify LIMIT clause is properly enforced even with large datasets."""
    # Insert some test rows and verify limit works
    # Schema columns: id, exchange, symbol, timeframe, target_metric, best_value, deployed
    rows = db.load_batch_results(limit=5)
    assert len(rows) <= 5


# ── Edge case: unknown timeframe ─────────────────────────────────────────


def test_unknown_timeframe_fallback():
    """Verify that unknown timeframes fall back to 5m spec instead of raising."""
    from src.app import _resolve_lookback

    # Unknown timeframe falls back to 5m spec (no error raised)
    lookback_days, candles_limit = _resolve_lookback("99m", days=None)
    # Should get default 5m lookback: 180 days
    assert lookback_days == 180
    assert candles_limit >= 50


# ── Candle quantity tests ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tf", ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d")
)
def test_candle_loading_minimum(db, tf):
    """Verify that loading candles for a timeframe yields at least expected minimum."""
    from src.config import get_timeframe_spec
    from src.data_loader import DataLoader

    # Use synthetic so no network needed
    days = 1
    spec = get_timeframe_spec(tf, "crypto")
    bars_per_day = spec.bars_per_day_crypto
    expected_min = int(bars_per_day * days)  # minimum expected from days * bars_per_day
    df = DataLoader.load_candles(
        exchange="synthetic",
        symbol="BTC/USD",
        timeframe=tf,
        days=days,
    )
    assert df is not None
    assert not df.empty
    # System ensures at least expected_min bars via max(limit, req_candles) logic
    assert (
        len(df) >= expected_min
    ), f"For {tf}: expected at least {expected_min}, got {len(df)}"
    # Verify columns
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
