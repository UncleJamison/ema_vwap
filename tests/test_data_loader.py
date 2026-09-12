"""
Unit tests for Data Loader Module.
"""

from src.data_loader import DataLoader


def test_generate_synthetic_candles():
    df = DataLoader.generate_synthetic_candles(num_bars=100, seed=123)
    assert len(df) == 100
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert (df["high"] >= df["low"]).all()
    assert (df["high"] >= df["open"]).all()
    assert (df["high"] >= df["close"]).all()
    assert (df["low"] <= df["open"]).all()
    assert (df["low"] <= df["close"]).all()


def test_format_symbols():
    from src.data_loader import normalize_symbol

    assert normalize_symbol("btcusd") == "BTC/USD"
    assert normalize_symbol("BTC-USDT") == "BTC/USDT"
    assert normalize_symbol("eth_usd") == "ETH/USD"
    assert normalize_symbol("bonkusdt") == "BONK/USDT"

    assert DataLoader.format_gemini_symbol("BTC/USD") == "btcusd"
    assert DataLoader.format_gemini_symbol("ETH/USDT") == "ethusd"
    assert DataLoader.format_kucoin_symbol("BTC/USD") == "BTC-USDT"
    assert DataLoader.format_kucoin_symbol("ETH/USDT") == "ETH-USDT"


def test_load_candles_fallback():
    # Should cleanly load synthetic candles if exchange is synthetic
    df = DataLoader.load_candles(exchange="synthetic", limit=50)
    assert len(df) == 50


def test_resample_and_upsert(tmp_path, monkeypatch):
    import src.data_loader as dl
    from src.database import CandleDatabase

    # Inject a fresh, isolated database so this test never touches candles.db
    test_db = CandleDatabase(db_path=str(tmp_path / "test.db"))
    monkeypatch.setattr(dl, "db", test_db)

    test_db.clear_cache("test_ex", "TEST/USD")
    df_base = DataLoader.generate_synthetic_candles(
        "TEST/USD", timeframe="5m", num_bars=300, seed=42
    )
    count = DataLoader.resample_and_upsert(
        "test_ex", "TEST/USD", df_base, base_timeframe="5m"
    )
    assert count > 0
    # Check that derived 15m timeframe candles exist in DB
    df_15m = test_db.load_candles("test_ex", "TEST/USD", "15m")
    assert not df_15m.empty
    assert abs(len(df_15m) - (len(df_base) // 3)) <= 2
    test_db.clear_cache("test_ex", "TEST/USD")


def test_multi_timeframe_resampling_all_tfs(tmp_path, monkeypatch):
    import src.data_loader as dl
    from src.database import CandleDatabase

    # Inject a fresh, isolated database so this test never touches candles.db
    test_db = CandleDatabase(db_path=str(tmp_path / "test_mtf.db"))
    monkeypatch.setattr(dl, "db", test_db)

    test_db.clear_cache("test_ex", "MTF/USD")

    df_1m = DataLoader.generate_synthetic_candles(
        "MTF/USD", timeframe="1m", num_bars=720, seed=99
    )
    total_upserted = DataLoader.resample_and_upsert(
        "test_ex", "MTF/USD", df_1m, base_timeframe="1m"
    )
    assert total_upserted > 0

    for tf in ["3m", "5m", "15m", "30m", "1h", "2h", "4h"]:
        df_tf = test_db.load_candles("test_ex", "MTF/USD", tf)
        assert not df_tf.empty, f"Expected resampled candles for timeframe {tf}"

    test_db.clear_cache("test_ex", "MTF/USD")
