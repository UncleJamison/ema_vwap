"""
Tests for SQLite Database WAL mode, PRAGMA configuration, multi-threaded concurrency, and new schema abstractions.
"""

import os
import threading
import time

from src.data_loader import DataLoader
from src.database import CandleDatabase


def test_wal_mode_and_pragmas(tmp_path):
    db_file = os.path.join(tmp_path, "test_wal.db")
    db = CandleDatabase(db_path=db_file)

    with db.get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        assert journal_mode.lower() == "wal"

        cursor.execute("PRAGMA synchronous;")
        synchronous = cursor.fetchone()[0]
        assert synchronous in (1, "1", "NORMAL")

        cursor.execute("PRAGMA cache_size;")
        cache_size = cursor.fetchone()[0]
        assert cache_size == -64000


def test_multi_threaded_read_write_concurrency(tmp_path):
    db_file = os.path.join(tmp_path, "test_concurrency.db")
    db = CandleDatabase(db_path=db_file)

    # Pre-populate database with seed candles
    df_seed = DataLoader.generate_synthetic_candles(num_bars=200, seed=1)
    db.save_candles("kucoin", "BTC/USDT", "5m", df_seed)

    errors = []
    writer_counts = [0] * 5
    reader_counts = [0] * 5

    def writer_thread(thread_idx):
        try:
            for i in range(15):
                df_synth = DataLoader.generate_synthetic_candles(
                    num_bars=20, seed=thread_idx * 1000 + i
                )
                db.save_candles("kucoin", f"COIN{thread_idx}/USDT", "5m", df_synth)
                db.save_paper_trade(
                    "kucoin",
                    f"COIN{thread_idx}/USDT",
                    "5m",
                    "BUY",
                    100.0 + i,
                    1.5,
                    150.0,
                    f"2026-08-21T12:00:{i:02d}",
                )
                db.save_optuna_profile(
                    "kucoin",
                    f"COIN{thread_idx}/USDT",
                    "5m",
                    "crossover",
                    "sharpe_ratio",
                    '{"ema": 9}',
                    1.5 + i * 0.1,
                    f"2026-08-21T12:00:{i:02d}",
                )
                db.update_portfolio_balance(
                    "USDT", 1000.0 + i, 800.0, "2026-08-21T12:00:00"
                )
                writer_counts[thread_idx] += 1
                time.sleep(0.005)
        except (RuntimeError, ValueError, TypeError, KeyError) as e:
            errors.append(f"Writer-{thread_idx} error: {e}")

    def reader_thread(thread_idx):
        try:
            for _ in range(25):
                df = db.load_candles("kucoin", "BTC/USDT", "5m", limit=50)
                assert len(df) <= 50
                trades = db.load_paper_trades(limit=10)
                assert isinstance(trades, list)
                balances = db.get_portfolio_balances()
                assert isinstance(balances, dict)
                reader_counts[thread_idx] += 1
                time.sleep(0.003)
        except (RuntimeError, ValueError, TypeError, KeyError) as e:
            errors.append(f"Reader-{thread_idx} error: {e}")

    threads = []
    for i in range(5):
        t_w = threading.Thread(target=writer_thread, args=(i,))
        t_r = threading.Thread(target=reader_thread, args=(i,))
        threads.extend([t_w, t_r])

    for t in threads:
        t.start()

    for t in threads:
        t.join(timeout=15)

    assert not errors, f"Concurrency errors encountered: {errors}"
    assert sum(writer_counts) == 5 * 15
    assert sum(reader_counts) == 5 * 25


def test_paper_trading_ledger_crud(tmp_path):
    db_file = os.path.join(tmp_path, "test_ledger.db")
    db = CandleDatabase(db_path=db_file)

    trade_id = db.save_paper_trade(
        exchange="kucoin",
        symbol="ETH/USDT",
        timeframe="15m",
        action="BUY",
        price=3000.0,
        quantity=0.5,
        total_usd=1500.0,
        timestamp="2026-08-21T10:00:00Z",
        metadata='{"stop_loss": 2900.0}',
    )

    assert trade_id > 0
    trades = db.load_paper_trades(exchange="kucoin", symbol="ETH/USDT")
    assert len(trades) == 1
    assert trades[0]["price"] == 3000.0
    assert trades[0]["action"] == "BUY"
    assert trades[0]["metadata"] == '{"stop_loss": 2900.0}'


def test_optuna_profile_registry_crud(tmp_path):
    db_file = os.path.join(tmp_path, "test_optuna.db")
    db = CandleDatabase(db_path=db_file)

    db.save_optuna_profile(
        exchange="gemini",
        symbol="BTC/USD",
        timeframe="1h",
        strategy_mode="pullback",
        target_metric="sharpe_ratio",
        best_params='{"fast_ema": 8, "slow_ema": 21}',
        best_value=2.45,
        updated_at="2026-08-21T11:00:00Z",
    )

    profile = db.load_optuna_profile(
        exchange="gemini",
        symbol="BTC/USD",
        timeframe="1h",
        strategy_mode="pullback",
        target_metric="sharpe_ratio",
    )
    assert profile is not None
    assert profile["best_value"] == 2.45
    assert profile["best_params"] == '{"fast_ema": 8, "slow_ema": 21}'

    # Update profile
    db.save_optuna_profile(
        exchange="gemini",
        symbol="BTC/USD",
        timeframe="1h",
        strategy_mode="pullback",
        target_metric="sharpe_ratio",
        best_params='{"fast_ema": 9, "slow_ema": 21}',
        best_value=2.80,
        updated_at="2026-08-21T12:00:00Z",
    )
    updated_profile = db.load_optuna_profile(
        exchange="gemini",
        symbol="BTC/USD",
        timeframe="1h",
        strategy_mode="pullback",
        target_metric="sharpe_ratio",
    )
    assert updated_profile["best_value"] == 2.80
    assert updated_profile["best_params"] == '{"fast_ema": 9, "slow_ema": 21}'


def test_portfolio_balances_crud(tmp_path):
    db_file = os.path.join(tmp_path, "test_balances.db")
    db = CandleDatabase(db_path=db_file)

    db.update_portfolio_balance("USDT", 10000.0, 7500.0, "2026-08-21T12:00:00Z")
    db.update_portfolio_balance("BTC", 1.5, 0.5, "2026-08-21T12:00:00Z")

    balances = db.get_portfolio_balances()
    assert "USDT" in balances
    assert "BTC" in balances
    assert balances["USDT"]["total"] == 10000.0
    assert balances["USDT"]["available"] == 7500.0
    assert balances["BTC"]["total"] == 1.5
