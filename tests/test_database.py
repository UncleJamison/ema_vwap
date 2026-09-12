"""
Unit tests for SQLite Candle Database.
"""

import os

from src.data_loader import DataLoader
from src.database import CandleDatabase


def test_sqlite_candle_db_crud(tmp_path):
    db_file = os.path.join(tmp_path, "test_candles.db")
    db = CandleDatabase(db_path=db_file)

    df_synth = DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    saved_count = db.save_candles(
        exchange="kucoin", symbol="BTC/USDT", timeframe="5m", df=df_synth
    )

    assert saved_count == 100
    assert (
        db.get_candle_count(exchange="kucoin", symbol="BTC/USDT", timeframe="5m") == 100
    )

    df_loaded = db.load_candles(
        exchange="kucoin", symbol="BTC/USDT", timeframe="5m", limit=50
    )
    assert len(df_loaded) == 50
    assert list(df_loaded.columns) == [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]


def test_symbol_normalization_is_consistent(tmp_path):
    db = CandleDatabase(db_path=os.path.join(tmp_path, "test_symbol_keys.db"))
    df_synth = DataLoader.generate_synthetic_candles(num_bars=3, seed=42)

    db.save_candles("kucoin", "BTC_USDT", "5m", df_synth)

    assert db.get_candle_count("kucoin", "BTC/USDT", "5m") == 3
    assert len(db.load_candles("kucoin", "BTC-USDT", "5m")) == 3
    assert db.clear_cache("kucoin", "BTC/USDT") == 3


def test_batch_result_delete_and_purge(tmp_path):
    db = CandleDatabase(db_path=os.path.join(tmp_path, "test_batch.db"))
    values = [
        ("kucoin", "BTC/USD", "5m", "sharpe_ratio", "2020-01-01T00:00:00+00:00"),
        ("kucoin", "ETH/USD", "5m", "sharpe_ratio", "2099-01-01T00:00:00+00:00"),
    ]
    for exchange, symbol, timeframe, metric, timestamp in values:
        db.save_batch_result(
            exchange,
            symbol,
            timeframe,
            "auto",
            metric,
            1.0,
            5,
            1.0,
            1.0,
            50.0,
            2.0,
            3.0,
            "{}",
            timestamp,
        )

    assert db.purge_batch_results(30) == 1
    rows = db.load_batch_results(limit=10)
    assert len(rows) == 1
    assert db.purge_batch_results(30) == 0
    assert db.delete_batch_results([rows[0]["id"]]) == 1
    assert not db.load_batch_results(limit=10)


def test_batch_results_can_load_all_metrics(tmp_path):
    db = CandleDatabase(db_path=os.path.join(tmp_path, "test_batch_metrics.db"))
    for metric in ("sharpe_ratio", "drawdown_penalized_sharpe"):
        db.save_batch_result(
            "kucoin",
            "BTC/USD",
            "5m",
            "crossover",
            metric,
            1.0,
            5,
            1.0,
            1.0,
            50.0,
            2.0,
            3.0,
            "{}",
            "2026-08-19T00:00:00+00:00",
        )

    all_rows = db.load_batch_results(limit=10)
    sharpe_rows = db.load_batch_results(target_metric="sharpe_ratio", limit=10)

    assert len(all_rows) == 2
    assert len(sharpe_rows) == 1


def test_batch_result_favorite_persists_across_upsert(tmp_path):
    db = CandleDatabase(db_path=os.path.join(tmp_path, "test_batch_favorites.db"))
    args = (
        "kucoin",
        "BTC/USD",
        "5m",
        "crossover",
        "sharpe_ratio",
        1.0,
        5,
        1.0,
        1.0,
        50.0,
        2.0,
        3.0,
        "{}",
        "2026-08-19T00:00:00+00:00",
    )
    db.save_batch_result(*args)
    result_id = db.load_batch_results(limit=1)[0]["id"]
    assert db.set_batch_result_favorite(result_id, True)

    db.save_batch_result(*args[:-2], '{"updated": true}', args[-1])
    row = db.load_batch_results(limit=1)[0]
    assert row["favorite"] == 1
    assert db.set_batch_result_favorite(row["id"], False)
    assert db.load_batch_results(limit=1)[0]["favorite"] == 0


def test_sqlite_candle_db_handles_timezone_offset_strings(tmp_path):
    import pandas as pd

    from src.backtester import BacktestEngine
    from src.config import StrategyParams
    from src.strategy import EmaVwapStrategy

    db = CandleDatabase(db_path=os.path.join(tmp_path, "test_tz_offsets.db"))

    # Direct dataframe with ISO8601 strings containing +00:00 offset
    df_raw = pd.DataFrame(
        {
            "timestamp": [
                "2026-08-22 11:36:00+00:00",
                "2026-08-22 11:41:00+00:00",
                "2026-08-22 11:46:00+00:00",
            ],
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
            "volume": [1000.0, 1200.0, 1100.0],
        }
    )

    db.save_candles("kucoin", "ZEC/USD", "5m", df_raw)
    df_loaded = db.load_candles("kucoin", "ZEC/USD", "5m")
    assert len(df_loaded) == 3

    # Ensure strategy & indicators process it without error
    params = StrategyParams(strategy_mode="crossover", fast_ema=2)
    strat = EmaVwapStrategy(params)
    df_signals = strat.generate_signals(df_loaded)
    assert "signal" in df_signals.columns

    # Ensure backtester computes metrics without error
    from src.backtester import BacktestResult

    engine = BacktestEngine(params)
    res = engine.run(df_loaded)
    assert isinstance(res, BacktestResult)


def test_batch_result_validation_persistence(tmp_path):
    db = CandleDatabase(db_path=os.path.join(tmp_path, "test_batch_validation.db"))
    db.save_batch_result(
        "kucoin",
        "BTC/USD",
        "5m",
        "crossover",
        "sharpe_ratio",
        1.5,
        10,
        1.5,
        1.2,
        60.0,
        5.0,
        15.0,
        "{}",
        "2026-08-20T00:00:00+00:00",
    )
    rows = db.load_batch_results(limit=1)
    assert len(rows) == 1
    res_id = rows[0]["id"]

    # Update validation status
    report_json = '{"status": "ROBUST", "overall_score": 85.0}'
    assert db.update_batch_result_validation(res_id, "ROBUST", report_json) is True

    # Reload and verify
    reloaded = db.load_batch_results(limit=1)[0]
    assert reloaded["validation_status"] == "ROBUST"
    assert reloaded["validation_json"] == report_json
