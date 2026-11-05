"""
Forensic test for batch_optimize full run-through verification.
Tests that:
1. Single metric (all_metrics) runs all symbols × timeframes × metrics
2. Multi-metric (all_combinations) runs all symbols × timeframes × metric pairs
3. Regular batch runs all symbols × timeframes
4. Results are properly persisted to batch_results table
"""

import json
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.batch_optimizer import (
    ALL_CURATED_MULTI_PAIRS,
    ALL_SINGLE_METRICS,
    BatchOptimizer,
    BatchOptimizerConfig,
)
from src.routes.batch import _run_all_metrics_batch_task
from src.routes.batch import router as batch_router


class TestBatchOptimizeFullRunThrough:
    """Test full run-through of batch optimization for all modes."""

    def test_single_metric_mode_combo_count(self):
        """Verify all_metrics creates correct number of combos."""
        symbols = ["BTC/USD", "ETH/USD"]
        timeframes = ["5m"]
        n_metrics = len(ALL_SINGLE_METRICS)

        expected_combos = len(symbols) * len(timeframes) * n_metrics
        assert expected_combos == 2 * 1 * 10  # 20 combos (10 single metrics)

    def test_multi_metric_mode_combo_count(self):
        """Verify all_combinations creates correct number of combos."""
        symbols = ["BTC/USD", "ETH/USD"]
        timeframes = ["5m"]
        n_pairs = len(ALL_CURATED_MULTI_PAIRS)

        expected_combos = len(symbols) * len(timeframes) * n_pairs
        assert expected_combos == 2 * 1 * 12  # 24 combos (12 curated pairs)

    def test_multi_timeframe_single_metric_combos(self):
        """Verify single metric with multiple timeframes."""
        symbols = ["BTC/USD"]
        timeframes = ["5m", "15m", "1h"]
        n_metrics = len(ALL_SINGLE_METRICS)

        expected_combos = len(symbols) * len(timeframes) * n_metrics
        assert expected_combos == 1 * 3 * 10  # 30 combos

    def test_multi_timeframe_multi_metric_combos(self):
        """Verify multi-metric with multiple timeframes."""
        symbols = ["BTC/USD"]
        timeframes = ["5m", "15m"]
        n_pairs = len(ALL_CURATED_MULTI_PAIRS)

        expected_combos = len(symbols) * len(timeframes) * n_pairs
        assert expected_combos == 1 * 2 * 12  # 24 combos


class TestBatchOptimizeEndpointBehavior:
    """Test the /api/batch_optimize endpoint behavior."""

    @pytest.fixture(autouse=True)
    def _fast_background_tasks(self, monkeypatch):
        """No-op the background sweep tasks for endpoint-contract tests.

        TestClient blocks until BackgroundTasks finish; a real sweep would
        run network fetches + Optuna for minutes. Sweep execution itself is
        covered by the dedicated _run_all_metrics_batch_task tests below.
        """
        monkeypatch.setattr(
            "src.routes.batch._run_all_metrics_batch_task",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "src.routes.batch._run_batch_task",
            lambda *args, **kwargs: None,
        )

    @pytest.fixture
    def client(self):
        """Create test client with rate limiting disabled."""
        from fastapi import FastAPI

        from src.app_limiter import limiter
        from src.auth import get_api_key
        from src.batch_optimizer import reset_batch_state

        app = FastAPI()
        app.include_router(batch_router)
        app.state.limiter = limiter

        # Disable rate limiting for tests (slowapi reads `enabled` at request
        # time; `_enabled` is not a real attribute) and clear the counters.
        limiter.enabled = False
        limiter.reset()
        # Clear any leftover "running" sweep state from earlier test files.
        reset_batch_state()

        async def override_get_api_key():
            return "test-api-key"

        app.dependency_overrides[get_api_key] = override_get_api_key

        yield TestClient(app)

        limiter.enabled = True
        limiter.reset()
        reset_batch_state()

    def test_all_metrics_single_timeframe_accepted(self, client):
        """all_metrics with single timeframe should start."""
        payload = {
            "symbols": ["BTC/USD"],
            "timeframes": ["5m"],
            "exchange": "kucoin",
            "target_metric": "all_metrics",
            "n_trials": 10,
        }
        response = client.post("/api/batch_optimize", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["total_combos"] == len(ALL_SINGLE_METRICS)  # 10 single metrics

    def test_all_metrics_multi_timeframe_rejected(self, client):
        """all_metrics with multiple timeframes should be rejected."""
        payload = {
            "symbols": ["BTC/USD"],
            "timeframes": ["5m", "15m"],
            "exchange": "kucoin",
            "target_metric": "all_metrics",
            "n_trials": 10,
        }
        response = client.post("/api/batch_optimize", json=payload)
        assert response.status_code == 400

    def test_all_combinations_single_timeframe_accepted(self, client):
        """all_combinations with single timeframe should start."""
        payload = {
            "symbols": ["BTC/USD"],
            "timeframes": ["5m"],
            "exchange": "kucoin",
            "target_metric": "all_combinations",
            "n_trials": 10,
        }
        response = client.post("/api/batch_optimize", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["total_combos"] == len(ALL_CURATED_MULTI_PAIRS)  # 12 curated pairs
        assert data["total_pairs"] == len(ALL_CURATED_MULTI_PAIRS)  # 12 curated pairs

    def test_all_combinations_multi_timeframe_rejected(self, client):
        """all_combinations with multiple timeframes should be rejected."""
        payload = {
            "symbols": ["BTC/USD"],
            "timeframes": ["5m", "15m"],
            "exchange": "kucoin",
            "target_metric": "all_combinations",
            "n_trials": 10,
        }
        response = client.post("/api/batch_optimize", json=payload)
        assert response.status_code == 400

    def test_regular_batch_multiple_symbols_timeframes(self, client):
        """Regular batch with multiple symbols and timeframes."""
        payload = {
            "symbols": ["BTC/USD", "ETH/USD"],
            "timeframes": ["5m", "15m"],
            "exchange": "kucoin",
            "target_metric": "sharpe_ratio",
            "n_trials": 10,
        }
        response = client.post("/api/batch_optimize", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["total_combos"] == 4  # 2 symbols × 2 timeframes


async def _fake_save_batch_result_pg(**kwargs):
    """No-op save for tests (patched onto src.batch_optimizer.save_batch_result_pg)."""
    return


async def _fake_load_batch_results_pg(**kwargs):
    """Return canned results for tests (patched onto src.batch_optimizer.load_batch_results_pg)."""
    return []


class TestBatchOptimizerRunMethod:
    """Test BatchOptimizer.run() executes all combos."""

    @pytest.fixture
    def mock_config(self):
        """Create a minimal BatchOptimizerConfig for testing."""
        return BatchOptimizerConfig(
            symbols=["BTC/USD", "ETH/USD"],
            timeframes=["5m"],
            exchange="kucoin",
            target_metric="sharpe_ratio",
            n_trials=5,
            min_trades=10,
        )

    @pytest.mark.asyncio
    async def test_run_method_iterates_all_combos(self, mock_config):
        """Test that run() iterates through all symbol/timeframe combos."""
        call_log = []

        async def mock_run_single_optuna(
            self, symbol, timeframe, is_cancelled=None, progress_callback=None
        ):
            print("DEBUG MOCK: " + symbol + " " + timeframe)
            call_log.append((symbol, timeframe))
            return {
                "status": "completed",
                "best_params": {"fast_ema": 10, "slow_ema": 20},
                "best_value": 1.5,
                "n_trials": 5,
                "study_name": "test_study",
            }

        import pandas as pd

        df = pd.DataFrame(
            {
                "timestamp": pd.date_range("2024-01-01", periods=100, freq="5min"),
                "open": [100] * 100,
                "high": [101] * 100,
                "low": [99] * 100,
                "close": [100] * 100,
                "volume": [1000] * 100,
            }
        )

        with patch.object(
            BatchOptimizer, "_run_single_optuna", mock_run_single_optuna
        ), patch.object(BatchOptimizer, "_get_base_candles", return_value=None), patch(
            "src.batch_optimizer.DataLoader.load_candles_sync", return_value=df
        ), patch(
            "src.batch_optimizer.DataLoader.load_candles", return_value=df
        ), patch(
            "src.batch_optimizer.db.load_batch_results", return_value=[]
        ), patch(
            "src.batch_optimizer.save_batch_result_pg", _fake_save_batch_result_pg
        ), patch(
            "src.batch_optimizer.load_batch_results_pg", _fake_load_batch_results_pg
        ):
            optimizer = BatchOptimizer(mock_config)
            await optimizer.run()

        # Should have called for each symbol/timeframe combo
        assert len(call_log) == 2  # 2 symbols x 1 timeframe
        assert ("BTC/USD", "5m") in call_log
        assert ("ETH/USD", "5m") in call_log

    @pytest.mark.asyncio
    async def test_run_method_respects_cancellation(self, mock_config):
        """Test that run() respects cancellation."""
        call_log = []

        def mock_is_cancelled():
            return len(call_log) >= 1

        async def mock_run_single_optuna(
            self, symbol, timeframe, is_cancelled=None, progress_callback=None
        ):
            print("DEBUG MOCK: " + symbol + " " + timeframe)
            call_log.append((symbol, timeframe))
            return {
                "status": "completed",
                "best_params": {"fast_ema": 10, "slow_ema": 20},
                "best_value": 1.5,
                "n_trials": 5,
                "study_name": "test_study",
            }

        import pandas as pd

        df = pd.DataFrame(
            {
                "timestamp": pd.date_range("2024-01-01", periods=100, freq="5min"),
                "open": [100] * 100,
                "high": [101] * 100,
                "low": [99] * 100,
                "close": [100] * 100,
                "volume": [1000] * 100,
            }
        )

        with patch.object(
            BatchOptimizer, "_run_single_optuna", mock_run_single_optuna
        ), patch.object(BatchOptimizer, "_get_base_candles", return_value=None), patch(
            "src.batch_optimizer.DataLoader.load_candles_sync", return_value=df
        ), patch(
            "src.batch_optimizer.DataLoader.load_candles", return_value=df
        ), patch(
            "src.batch_optimizer.db.load_batch_results", return_value=[]
        ), patch(
            "src.batch_optimizer.save_batch_result_pg", _fake_save_batch_result_pg
        ), patch(
            "src.batch_optimizer.load_batch_results_pg", _fake_load_batch_results_pg
        ):
            optimizer = BatchOptimizer(mock_config)
            await optimizer.run(is_cancelled=mock_is_cancelled)

        # Should have stopped after first combo due to cancellation
        assert len(call_log) == 1

    @pytest.mark.asyncio
    async def test_run_method_all_combos_already_completed_checkpoint(
        self, mock_config
    ):
        """Test that run() returns results without sync_bridge loop collision when all combos completed."""
        import asyncio

        from src.batch_optimizer import get_batch_state
        from src.sync_bridge import get_main_loop, set_main_loop

        old_loop = get_main_loop()
        running_loop = asyncio.get_running_loop()
        set_main_loop(running_loop)

        canned_results = [{"id": 1, "symbol": "BTC/USD", "timeframe": "5m"}]

        async def _mock_load_results(**kwargs):
            return canned_results

        async def _mock_load_checkpoint(self):
            return {("BTC/USD", "5m"), ("ETH/USD", "5m")}

        try:
            with patch.object(
                BatchOptimizer, "_load_checkpoint", _mock_load_checkpoint
            ), patch("src.batch_optimizer.load_batch_results_pg", _mock_load_results):
                optimizer = BatchOptimizer(mock_config)
                results = await optimizer.run()

            assert results == canned_results
            state = get_batch_state()
            assert state["running"] is False
            assert state["current_combo"] == "Complete"
            assert state["combo_progress"] == 1.0
            assert state["done"] == 2
        finally:
            set_main_loop(old_loop)


class TestBatchResultsPersistence:
    """Test that batch results are properly persisted."""

    def test_batch_results_saved_with_correct_target_metric(self, pg_container):
        """Verify results are saved with proper target_metric."""
        import asyncio as _asyncio

        from src.database import CandleDatabase
        from src.sync_bridge import get_main_loop

        async def _cleanup():
            from src.pg_connection_manager import get_pg_manager

            pool = get_pg_manager().get_pool("batch_results")
            async with pool.acquire() as conn:
                await conn.execute("TRUNCATE batch_results RESTART IDENTITY")

        _asyncio.run_coroutine_threadsafe(_cleanup(), get_main_loop()).result(
            timeout=10
        )
        db = CandleDatabase()

        # Test direct save via database
        db.save_batch_result(
            exchange="kucoin",
            symbol="BTC/USD",
            timeframe="5m",
            strategy_mode="crossover",
            target_metric="sharpe_ratio",
            best_value=1.5,
            trade_count=50,
            sharpe_ratio=1.5,
            calmar_ratio=1.0,
            win_rate=60.0,
            max_drawdown_pct=10.0,
            total_return_pct=20.0,
            best_params=json.dumps({"fast_ema": 9, "slow_ema": 21}),
            run_timestamp="2024-01-01T00:00:00",
        )

        # Verify result was saved
        results = db.load_batch_results(target_metric="sharpe_ratio", limit=10)
        assert len(results) == 1
        assert results[0]["symbol"] == "BTC/USD"
        assert results[0]["timeframe"] == "5m"
        assert results[0]["target_metric"] == "sharpe_ratio"
        assert results[0]["best_value"] == 1.5


class TestRunAllMetricsBatchTask:
    """Test _run_all_metrics_batch_task executes all configs."""

    def test_runs_all_configs(self):
        """Test that _run_all_metrics_batch_task runs all provided configs."""
        call_log = []

        configs = [
            BatchOptimizerConfig(
                symbols=["BTC/USD"],
                timeframes=["5m"],
                exchange="kucoin",
                target_metric="sharpe_ratio",
                n_trials=1,
            ),
            BatchOptimizerConfig(
                symbols=["BTC/USD"],
                timeframes=["5m"],
                exchange="kucoin",
                target_metric="calmar_ratio",
                n_trials=1,
            ),
        ]

        def mock_run(self, **kwargs):
            call_log.append(self.config.target_metric)

        with patch.object(BatchOptimizer, "run", mock_run):
            _run_all_metrics_batch_task(configs)

        assert len(call_log) == 2
        assert "sharpe_ratio" in call_log
        assert "calmar_ratio" in call_log


class TestBatchProgressReporting:
    """Test that batch progress is properly reported."""

    def test_progress_state_updates(self):
        """Test that batch state updates during run."""
        from src.batch_optimizer import _update_state, get_batch_state

        _update_state(
            running=True,
            current_combo="BTC/USD (5m)",
            done=0,
            total=2,
            combo_progress=0.0,
        )
        state = get_batch_state()
        assert state["running"] is True
        assert state["current_combo"] == "BTC/USD (5m)"
        assert state["done"] == 0
        assert state["total"] == 2

        _update_state(combo_progress=0.5)
        state = get_batch_state()
        assert state["combo_progress"] == 0.5

        _update_state(done=1)
        state = get_batch_state()
        assert state["done"] == 1


class TestBatchAutoValidate:
    """Test batch auto-validation functionality."""

    def test_auto_validate_calls_validator_per_result(self, pg_container, monkeypatch):
        """Test that auto-validate runs validator on each result."""
        import asyncio as _asyncio

        import src.app as app_module
        from src.database import CandleDatabase
        from src.sync_bridge import get_main_loop

        async def _cleanup():
            from src.pg_connection_manager import get_pg_manager

            pool = get_pg_manager().get_pool("batch_results")
            async with pool.acquire() as conn:
                await conn.execute("TRUNCATE batch_results RESTART IDENTITY")

        _asyncio.run_coroutine_threadsafe(_cleanup(), get_main_loop()).result(
            timeout=10
        )
        db = CandleDatabase()

        # Add a fake batch result
        db.save_batch_result(
            exchange="kucoin",
            symbol="BTC/USD",
            timeframe="5m",
            strategy_mode="crossover",
            target_metric="sharpe_ratio",
            best_value=1.5,
            trade_count=50,
            sharpe_ratio=1.5,
            calmar_ratio=1.0,
            win_rate=60.0,
            max_drawdown_pct=10.0,
            total_return_pct=20.0,
            best_params=json.dumps({"fast_ema": 9, "slow_ema": 21}),
            run_timestamp="2024-01-01T00:00:00",
        )

        # Point the app's _batch_db at our test DB
        monkeypatch.setattr(app_module, "_batch_db", db)

        # Mock the validator (imported inside the endpoint from src.validator)
        with patch("src.validator.StrategyValidator") as mock_validator_class:
            mock_report = Mock()
            mock_report.status = "PASS"
            mock_report.to_dict.return_value = {"status": "PASS"}
            mock_validator = Mock()
            mock_validator.validate.return_value = mock_report
            mock_validator_class.return_value = mock_validator

            with patch("src.routes.batch.DataLoader") as mock_loader:
                from unittest.mock import AsyncMock

                import pandas as pd

                df = pd.DataFrame(
                    {
                        "timestamp": pd.date_range(
                            "2024-01-01", periods=100, freq="5min"
                        ),
                        "open": [100] * 100,
                        "high": [101] * 100,
                        "low": [99] * 100,
                        "close": [100] * 100,
                        "volume": [1000] * 100,
                    }
                )
                # auto-validate awaits DataLoader.load_candles (async variant)
                mock_loader.load_candles = AsyncMock(return_value=df)

                from fastapi import FastAPI
                from fastapi.testclient import TestClient

                from src.auth import get_api_key
                from src.routes.batch import router as batch_router

                app = FastAPI()
                app.include_router(batch_router)

                async def override_get_api_key():
                    return "test-api-key"

                app.dependency_overrides[get_api_key] = override_get_api_key

                client = TestClient(app)

                response = client.post(
                    "/api/batch_results/auto-validate",
                    json={"target_metric": "sharpe_ratio"},
                )

                assert response.status_code == 200
                data = response.json()
                assert data["validated_count"] == 1
                assert mock_validator.validate.called

    @pytest.mark.asyncio
    async def test_batch_optimizer_saves_win_rate_and_return_pct(self):
        """Verify _run_single_optuna passes win_rate, total_return_pct, and max_drawdown_pct to save_batch_result_pg."""
        import pandas as pd

        from src.batch_optimizer import BatchOptimizer, BatchOptimizerConfig

        saved_kwargs = {}

        async def _mock_save(**kwargs):
            saved_kwargs.update(kwargs)

        cfg = BatchOptimizerConfig(
            symbols=["SOL/USD"],
            timeframes=["5m"],
            exchange="kucoin",
            target_metric="sharpe_ratio",
            n_trials=5,
            min_candles=50,
        )
        optimizer = BatchOptimizer(cfg)

        mock_result = {
            "status": "completed",
            "best_params": {"fast_ema": 9, "slow_ema": 21},
            "best_value": 4.80,
            "best_backtest_result": {
                "total_trades": 12,
                "win_rate": 66.7,
                "total_return_pct": 18.5,
                "max_drawdown_pct": 4.2,
                "sharpe_ratio": 4.80,
                "calmar_ratio": 3.10,
            },
        }

        df = pd.DataFrame(
            {
                "timestamp": pd.date_range("2024-01-01", periods=100, freq="5min"),
                "open": [100] * 100,
                "high": [101] * 100,
                "low": [99] * 100,
                "close": [100] * 100,
                "volume": [1000] * 100,
            }
        )

        with patch.object(optimizer, "_get_base_candles", return_value=df), patch(
            "src.batch_optimizer.OptunaOptimizer.optimize", return_value=mock_result
        ), patch("src.batch_optimizer.save_batch_result_pg", _mock_save):
            await optimizer._run_single_optuna("SOL/USD", "5m")

        assert saved_kwargs["win_rate"] == 66.7
        assert saved_kwargs["total_return_pct"] == 18.5
        assert saved_kwargs["max_drawdown_pct"] == 4.2
        assert saved_kwargs["trade_count"] == 12
        assert saved_kwargs["sharpe_ratio"] == 4.80

    def test_heal_batch_result_metrics_from_validation_json(self):
        """Verify _heal_batch_result_metrics populates null metrics from validation_json."""
        import json

        from src.pg_database import _heal_batch_result_metrics

        val_report = {
            "status": "CAUTION",
            "overall_score": 68.8,
            "gate_results": {
                "gate1": {
                    "metrics": {
                        "win_rate": 75.0,
                        "total_return_pct": 24.3,
                        "max_drawdown_pct": 6.1,
                        "sharpe_ratio": 3.45,
                        "trade_count": 8,
                    }
                }
            },
        }

        row = {
            "id": 1,
            "symbol": "SOL/USD",
            "timeframe": "5m",
            "win_rate": None,
            "total_return_pct": None,
            "max_drawdown_pct": None,
            "sharpe_ratio": None,
            "trade_count": 0,
            "validation_json": json.dumps(val_report),
        }

        healed = _heal_batch_result_metrics(row)
        assert healed["win_rate"] == 75.0
        assert healed["total_return_pct"] == 24.3
        assert healed["max_drawdown_pct"] == 6.1
        assert healed["sharpe_ratio"] == 3.45
        assert healed["trade_count"] == 8


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
