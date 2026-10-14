"""
Tests for ema_vwap-l7v.12: Reduce repeated paper polling work and lock contention.

Acceptance criteria validated:
1. Measure poll latency and provider/indicator work by active profile count.
2. Reuse a candle/indicator snapshot for profiles sharing an exchange/symbol/timeframe on one poll.
3. Avoid holding a global lock across remote data fetches while retaining per-position serialized settlement.
4. Bound/expire in-memory evaluation state and prevent overlapping poll cycles.
5. Concurrency tests for poll/manual API interaction and benchmark smoke coverage.
"""

import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from src.config import StrategyParams
from src.database import CandleDatabase
from src.paper.engine import PaperTradingEngine
from src.paper.models import PaperPosition


def _make_db() -> CandleDatabase:
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, f"test_paper_opt_{time.time_ns()}.db")
    return CandleDatabase(db_path=db_path)


def _make_candles(n: int = 50, base_price: float = 50000.0) -> pd.DataFrame:
    base_ts = pd.to_datetime("2024-01-01T00:00:00+00:00", utc=True)
    rows = []
    for i in range(n):
        ts = base_ts + pd.Timedelta(minutes=5 * i)
        price = base_price + (i * 10.0)
        rows.append(
            {
                "timestamp": ts,
                "open": price,
                "high": price + 50.0,
                "low": price - 50.0,
                "close": price + 10.0,
                "volume": 100.0,
            }
        )
    return pd.DataFrame(rows)


class TestSnapshotReuse:
    def test_snapshot_reuse_for_shared_exchange_symbol_timeframe(self):
        """Active profiles sharing exchange/symbol/timeframe reuse candle and indicator snapshots."""
        db = _make_db()
        engine = PaperTradingEngine(db=db)
        engine.ledger.deposit(amount=100_000.0)

        # Profile 1: Binance BTC/USDT 5m
        engine.registry.save_profile(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe="5m",
            strategy_mode="conservative",
            target_metric="sharpe",
            params=StrategyParams(),
            is_active=True,
        )
        # Profile 2: Binance BTC/USDT 5m (same symbol/timeframe, same params)
        engine.registry.save_profile(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe="5m",
            strategy_mode="aggressive",
            target_metric="calmar",
            params=StrategyParams(),
            is_active=True,
        )
        # Profile 3: Binance ETH/USDT 5m (different symbol)
        engine.registry.save_profile(
            exchange="binance",
            symbol="ETH/USDT",
            timeframe="5m",
            strategy_mode="conservative",
            target_metric="sharpe",
            params=StrategyParams(),
            is_active=True,
        )

        btc_candles = _make_candles(50, 50000.0)
        eth_candles = _make_candles(50, 3000.0)

        call_counts = {"fetch": 0}

        def mock_fetch(exchange, symbol, timeframe, limit=200):
            call_counts["fetch"] += 1
            if "BTC" in symbol:
                return btc_candles
            return eth_candles

        with patch(
            "src.data_loader.DataLoader.fetch_and_update_candles",
            side_effect=mock_fetch,
        ):
            results = engine.step_all_active_profiles()

        assert len(results) == 3
        # 3 profiles, but only 2 unique (exchange, symbol, timeframe) -> 2 fetches
        assert call_counts["fetch"] == 2

        metrics = engine.last_poll_metrics
        assert metrics["active_profiles_count"] == 3
        assert metrics["provider_fetches"] == 2
        assert metrics["cached_candle_hits"] == 1
        assert metrics["indicator_computations"] == 2  # 1 for BTC, 1 for ETH
        assert metrics["cached_signal_hits"] == 1
        assert metrics["poll_latency_ms"] >= 0.0


class TestLockContentionAvoidance:
    def test_no_global_lock_during_remote_data_fetch(self):
        """Manual close_position_market is NOT blocked by a slow remote candle fetch on evaluate_symbol."""
        db = _make_db()
        engine = PaperTradingEngine(db=db)
        engine.ledger.deposit(amount=100_000.0)

        now_iso = datetime.now(timezone.utc).isoformat()
        pos = PaperPosition(
            account_id="default",
            exchange="binance",
            symbol="BTC/USDT",
            side="LONG",
            entry_price=50000.0,
            current_price=50000.0,
            quantity=1.0,
            cost_basis=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
            unrealized_pnl=0.0,
            unrealized_pnl_pct=0.0,
            entry_time=now_iso,
            last_bar_time=None,
        )
        engine.ledger.save_position(pos)

        fetch_started = threading.Event()
        can_proceed_fetch = threading.Event()

        candles = _make_candles(50, 50000.0)

        def slow_fetch(exchange, symbol, timeframe, limit=200):
            fetch_started.set()
            can_proceed_fetch.wait(timeout=2.0)
            return candles

        eval_thread_result = []

        def run_eval():
            res = engine.evaluate_symbol(
                exchange="binance",
                symbol="BTC/USDT",
                timeframe="5m",
                fetch_live=True,
            )
            eval_thread_result.append(res)

        with patch(
            "src.data_loader.DataLoader.fetch_and_update_candles",
            side_effect=slow_fetch,
        ):
            t_eval = threading.Thread(target=run_eval)
            t_eval.start()

            # Wait until fetch has started
            assert fetch_started.wait(timeout=1.0)

            # While fetch is stalled, manual close MUST NOT block
            t0 = time.perf_counter()
            trade = engine.close_position_market(
                "binance", "BTC/USDT", current_price=51_000.0
            )
            elapsed = time.perf_counter() - t0

            assert trade is not None
            # Immediate acquisition of position lock without waiting for remote fetch
            assert elapsed < 0.2

            # Now allow fetch to complete
            can_proceed_fetch.set()
            t_eval.join(timeout=2.0)

        assert not t_eval.is_alive()
        # Verify position is closed
        assert engine.ledger.get_position("binance", "BTC/USDT") is None

    def test_concurrent_evaluations_for_different_symbols(self):
        """Concurrent evaluations for different symbols do not serialize or block each other."""
        db = _make_db()
        engine = PaperTradingEngine(db=db)
        engine.ledger.deposit(amount=100_000.0)

        candles_btc = _make_candles(50, 50000.0)
        candles_eth = _make_candles(50, 3000.0)

        t1_start = threading.Event()
        t2_start = threading.Event()

        def mock_fetch(exchange, symbol, timeframe, limit=200):
            if "BTC" in symbol:
                t1_start.set()
                time.sleep(0.05)
                return candles_btc
            else:
                t2_start.set()
                time.sleep(0.05)
                return candles_eth

        with patch(
            "src.data_loader.DataLoader.fetch_and_update_candles",
            side_effect=mock_fetch,
        ):
            t1 = threading.Thread(
                target=engine.evaluate_symbol,
                kwargs={
                    "exchange": "binance",
                    "symbol": "BTC/USDT",
                    "fetch_live": True,
                },
            )
            t2 = threading.Thread(
                target=engine.evaluate_symbol,
                kwargs={
                    "exchange": "binance",
                    "symbol": "ETH/USDT",
                    "fetch_live": True,
                },
            )

            t0 = time.perf_counter()
            t1.start()
            t2.start()
            t1.join(timeout=2.0)
            t2.join(timeout=2.0)
            total_time = time.perf_counter() - t0

            # If serialized, would take > 0.1s. Running with unblocked fetches overlaps
            assert total_time < 0.25


class TestPollCycleConcurrencyAndRetention:
    def test_prevent_overlapping_poll_cycles(self):
        """Overlapping step_all_active_profiles calls are rejected when a cycle is already active."""
        db = _make_db()
        engine = PaperTradingEngine(db=db)

        engine.registry.save_profile(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe="5m",
            strategy_mode="conservative",
            target_metric="sharpe",
            params=StrategyParams(),
            is_active=True,
        )

        poll_in_progress = threading.Event()
        finish_poll = threading.Event()
        candles = _make_candles(50, 50000.0)

        def blocking_fetch(exchange, symbol, timeframe, limit=200):
            poll_in_progress.set()
            finish_poll.wait(timeout=2.0)
            return candles

        first_results = []

        def run_first_poll():
            res = engine.step_all_active_profiles()
            first_results.extend(res)

        with patch(
            "src.data_loader.DataLoader.fetch_and_update_candles",
            side_effect=blocking_fetch,
        ):
            t1 = threading.Thread(target=run_first_poll)
            t1.start()

            assert poll_in_progress.wait(timeout=1.0)

            # Second call should skip immediately because cycle is already in progress
            res2 = engine.step_all_active_profiles()
            assert res2 == []

            finish_poll.set()
            t1.join(timeout=2.0)

        assert len(first_results) == 1

    def test_bounded_evaluations_capacity(self):
        """Evaluation cache evicts oldest entries when exceeding max_evaluations."""
        db = _make_db()
        engine = PaperTradingEngine(db=db, max_evaluations=5)

        for i in range(10):
            engine._record_evaluation(f"key_{i}", {"symbol": f"SYM_{i}", "val": i})
            time.sleep(0.005)

        # Capacity was set to 5
        assert len(engine._last_evaluations) <= 5
        # Newest items must be present
        for i in range(5, 10):
            assert f"key_{i}" in engine._last_evaluations
        # Oldest items must have been evicted
        assert "key_0" not in engine._last_evaluations
        assert "key_1" not in engine._last_evaluations

    def test_expired_evaluations_ttl(self):
        """Evaluation entries older than eval_ttl_seconds are pruned."""
        db = _make_db()
        # Set TTL to 0.05 seconds
        engine = PaperTradingEngine(db=db, eval_ttl_seconds=0.05)

        engine._record_evaluation("stale_key", {"status": "old"})
        assert "stale_key" in engine._last_evaluations

        # Wait past TTL
        time.sleep(0.08)

        engine.prune_evaluations()
        assert "stale_key" not in engine._last_evaluations


class TestBenchmarkSmokeCoverage:
    def test_benchmark_smoke_latency_and_metrics_by_profile_count(self):
        """Benchmark smoke test measuring poll latency and cache hits scaling with profile count."""
        db = _make_db()
        engine = PaperTradingEngine(db=db)
        engine.ledger.deposit(amount=100_000.0)

        # Register 10 profiles: 5 sharing BTC 5m, 3 sharing ETH 5m, 2 unique
        params = StrategyParams()
        for i in range(5):
            engine.registry.save_profile(
                exchange="binance",
                symbol="BTC/USDT",
                timeframe="5m",
                strategy_mode=f"mode_btc_{i}",
                target_metric="sharpe",
                params=params,
                is_active=True,
            )
        for i in range(3):
            engine.registry.save_profile(
                exchange="binance",
                symbol="ETH/USDT",
                timeframe="5m",
                strategy_mode=f"mode_eth_{i}",
                target_metric="sharpe",
                params=params,
                is_active=True,
            )
        engine.registry.save_profile(
            exchange="binance",
            symbol="SOL/USDT",
            timeframe="5m",
            strategy_mode="mode_sol",
            target_metric="sharpe",
            params=params,
            is_active=True,
        )
        engine.registry.save_profile(
            exchange="binance",
            symbol="AVAX/USDT",
            timeframe="5m",
            strategy_mode="mode_avax",
            target_metric="sharpe",
            params=params,
            is_active=True,
        )

        candles_map = {
            "BTC/USDT": _make_candles(50, 50000.0),
            "ETH/USDT": _make_candles(50, 3000.0),
            "SOL/USDT": _make_candles(50, 150.0),
            "AVAX/USDT": _make_candles(50, 30.0),
        }

        call_count = {"fetches": 0}

        def mock_fetch(exchange, symbol, timeframe, limit=200):
            call_count["fetches"] += 1
            return candles_map.get(symbol, _make_candles(50, 10.0))

        with patch(
            "src.data_loader.DataLoader.fetch_and_update_candles",
            side_effect=mock_fetch,
        ):
            results = engine.step_all_active_profiles()

        assert len(results) == 10
        # 10 profiles across 4 unique symbols -> exactly 4 provider fetches
        assert call_count["fetches"] == 4

        metrics = engine.last_poll_metrics
        assert metrics["active_profiles_count"] == 10
        assert metrics["provider_fetches"] == 4
        assert metrics["cached_candle_hits"] == 6
        assert metrics["cached_signal_hits"] == 6
        assert metrics["poll_latency_ms"] >= 0.0

        status = engine.get_status()
        assert status["last_poll_metrics"]["cached_candle_hits"] == 6
