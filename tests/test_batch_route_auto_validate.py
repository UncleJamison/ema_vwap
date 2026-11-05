"""
Tests for Batch Sweep Auto-Validate endpoint (src/routes/batch.py):
- POST /api/batch_results/auto-validate
Tests cover: empty results, successful validation, validation with errors,
filtering by target_metric, integration with StrategyValidator,
persistence of validation status/report to database.
"""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.routes.batch import BatchResultsAutoValidateRequest
from src.validator import ValidationReport


@pytest.fixture
def mock_batch_db():
    """Create a mock batch database for PG functions."""
    db = Mock()
    # Patch pg functions directly since they're imported into the
    # src.routes.batch namespace at module load.
    with (
        patch(
            "src.routes.batch.load_batch_results_pg", new_callable=AsyncMock
        ) as mock_load,
        patch(
            "src.routes.batch.update_batch_result_validation_pg",
            new_callable=AsyncMock,
        ) as mock_update,
    ):
        db.load_batch_results = mock_load
        db.update_batch_result_validation_pg = mock_update
        yield db


@pytest.fixture
def mock_app(mock_batch_db):
    """Create a mock src.app module with batch_db."""
    with patch("src.routes.batch._st", return_value=Mock(_batch_db=mock_batch_db)):
        yield


class TestAutoValidateBatchResults:
    """Tests for POST /api/batch_results/auto-validate endpoint."""

    pytestmark = pytest.mark.asyncio

    async def test_no_results_to_validate(self, mock_app, mock_batch_db):
        """Should return success with 0 validated when no results exist."""
        mock_batch_db.load_batch_results.return_value = []

        from src.routes.batch import auto_validate_batch_results

        req = BatchResultsAutoValidateRequest(target_metric="sharpe_ratio")
        result = await auto_validate_batch_results(req)

        assert result["status"] == "success"
        assert result["validated_count"] == 0
        assert result["message"] == "No results to validate."
        mock_batch_db.load_batch_results.assert_called_once_with(
            target_metric="sharpe_ratio", limit=1000
        )

    async def test_successful_validation_single_result(self, mock_app, mock_batch_db):
        """Should validate a single batch result and persist validation."""
        mock_batch_db.load_batch_results.return_value = [
            {
                "id": 1,
                "exchange": "kucoin",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "strategy_mode": "crossover",
                "target_metric": "sharpe_ratio",
                "best_value": 2.5,
                "best_params": json.dumps(
                    {"fast_ema": 9, "slow_ema": 21, "risk_per_trade_pct": 1.0}
                ),
                "trade_count": 10,
            }
        ]

        # Mock candle data
        with patch(
            "src.routes.batch.DataLoader.load_candles",
            new_callable=AsyncMock,
        ) as mock_load_candles:
            import pandas as pd

            dates = pd.date_range("2024-01-01", periods=200, freq="5min", tz="UTC")
            mock_load_candles.return_value = pd.DataFrame(
                {
                    "timestamp": dates,
                    "open": [50000] * 200,
                    "high": [50100] * 200,
                    "low": [49900] * 200,
                    "close": [50000] * 200,
                    "volume": [100] * 200,
                }
            )

            # Mock validator to return PASS
            with patch("src.validator.StrategyValidator") as mock_validator_class:
                mock_validator = Mock()
                mock_report = Mock(spec=ValidationReport)
                mock_report.status = "PASS"
                mock_report.to_dict.return_value = {
                    "status": "PASS",
                    "gate_results": {},
                    "summary": "All gates passed",
                }
                mock_validator.validate.return_value = mock_report
                mock_validator_class.return_value = mock_validator

                from src.routes.batch import auto_validate_batch_results

                req = BatchResultsAutoValidateRequest(target_metric="sharpe_ratio")
                result = await auto_validate_batch_results(req)

                assert result["status"] == "success"
                assert result["validated_count"] == 1
                assert result["total_results"] == 1
                assert result["errors"] == []

                # Verify validation was persisted
                mock_batch_db.update_batch_result_validation_pg.assert_called_once_with(
                    result_id=1,
                    status="PASS",
                    report_json=json.dumps(
                        {
                            "status": "PASS",
                            "gate_results": {},
                            "summary": "All gates passed",
                        }
                    ),
                )

    async def test_multiple_results_validation(self, mock_app, mock_batch_db):
        """Should validate multiple batch results."""
        mock_batch_db.load_batch_results.return_value = [
            {
                "id": 1,
                "exchange": "kucoin",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "strategy_mode": "crossover",
                "target_metric": "sharpe_ratio",
                "best_value": 2.5,
                "best_params": json.dumps({"fast_ema": 9, "slow_ema": 21}),
                "trade_count": 10,
            },
            {
                "id": 2,
                "exchange": "kucoin",
                "symbol": "ETH/USD",
                "timeframe": "15m",
                "strategy_mode": "crossover",
                "target_metric": "sharpe_ratio",
                "best_value": 1.8,
                "best_params": json.dumps({"fast_ema": 12, "slow_ema": 26}),
                "trade_count": 8,
            },
        ]

        with patch(
            "src.routes.batch.DataLoader.load_candles",
            new_callable=AsyncMock,
        ) as mock_load_candles:
            import pandas as pd

            dates = pd.date_range("2024-01-01", periods=200, freq="5min", tz="UTC")
            mock_load_candles.return_value = pd.DataFrame(
                {
                    "timestamp": dates,
                    "open": [50000] * 200,
                    "high": [50100] * 200,
                    "low": [49900] * 200,
                    "close": [50000] * 200,
                    "volume": [100] * 200,
                }
            )

            with patch("src.validator.StrategyValidator") as mock_validator_class:
                mock_validator = Mock()
                mock_report = Mock(spec=ValidationReport)
                mock_report.status = "PASS"
                mock_report.to_dict.return_value = {"status": "PASS"}
                mock_validator.validate.return_value = mock_report
                mock_validator_class.return_value = mock_validator

                from src.routes.batch import auto_validate_batch_results

                req = BatchResultsAutoValidateRequest(target_metric="sharpe_ratio")
                result = await auto_validate_batch_results(req)

                assert result["validated_count"] == 2
                assert result["total_results"] == 2
                assert mock_batch_db.update_batch_result_validation_pg.call_count == 2

    async def test_validation_with_errors_continues(self, mock_app, mock_batch_db):
        """Should continue validating other results when one fails."""
        mock_batch_db.load_batch_results.return_value = [
            {
                "id": 1,
                "exchange": "kucoin",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "strategy_mode": "crossover",
                "target_metric": "sharpe_ratio",
                "best_value": 2.5,
                "best_params": json.dumps({"fast_ema": 9}),
                "trade_count": 10,
            },
            {
                "id": 2,
                "exchange": "kucoin",
                "symbol": "ETH/USD",
                "timeframe": "15m",
                "strategy_mode": "crossover",
                "target_metric": "sharpe_ratio",
                "best_value": 1.8,
                "best_params": json.dumps({"fast_ema": 12}),
                "trade_count": 8,
            },
        ]

        with patch(
            "src.routes.batch.DataLoader.load_candles",
            new_callable=AsyncMock,
        ) as mock_load_candles:
            import pandas as pd

            dates = pd.date_range("2024-01-01", periods=200, freq="5min", tz="UTC")
            mock_load_candles.return_value = pd.DataFrame(
                {
                    "timestamp": dates,
                    "open": [50000] * 200,
                    "high": [50100] * 200,
                    "low": [49900] * 200,
                    "close": [50000] * 200,
                    "volume": [100] * 200,
                }
            )

            with patch("src.validator.StrategyValidator") as mock_validator_class:
                mock_validator = Mock()
                # First call succeeds, second raises exception
                mock_report = Mock(spec=ValidationReport)
                mock_report.status = "PASS"
                mock_report.to_dict.return_value = {"status": "PASS"}
                mock_validator.validate.side_effect = [
                    mock_report,
                    Exception("Validation failed: insufficient trades"),
                ]
                mock_validator_class.return_value = mock_validator

                from src.routes.batch import auto_validate_batch_results

                req = BatchResultsAutoValidateRequest(target_metric="sharpe_ratio")
                result = await auto_validate_batch_results(req)

                assert result["validated_count"] == 1
                assert result["total_results"] == 2
                assert len(result["errors"]) == 1
                assert "ETH/USD/15m" in result["errors"][0]
                assert "insufficient trades" in result["errors"][0].lower()

                # Only first result's validation should be persisted
                assert mock_batch_db.update_batch_result_validation_pg.call_count == 1

    async def test_filtering_by_target_metric(self, mock_app, mock_batch_db):
        """Should filter results by target_metric when provided."""
        mock_batch_db.load_batch_results.return_value = []

        from src.routes.batch import auto_validate_batch_results

        req = BatchResultsAutoValidateRequest(target_metric="total_return_pct")
        _ = await auto_validate_batch_results(req)

        mock_batch_db.load_batch_results.assert_called_once_with(
            target_metric="total_return_pct", limit=1000
        )

    async def test_default_target_metric_sharpe(self, mock_app, mock_batch_db):
        """Should pass None to load_batch_results when target_metric not specified (default applied per-row)."""
        mock_batch_db.load_batch_results.return_value = []

        from src.routes.batch import auto_validate_batch_results

        req = BatchResultsAutoValidateRequest()  # No target_metric specified
        _ = await auto_validate_batch_results(req)

        # load_batch_results receives None (default applied per-row later)
        mock_batch_db.load_batch_results.assert_called_once_with(
            target_metric=None, limit=1000
        )

    async def test_multi_objective_metric_handling(self, mock_app, mock_batch_db):
        """Should handle multi-objective metrics (pipe-separated)."""
        mock_batch_db.load_batch_results.return_value = [
            {
                "id": 1,
                "exchange": "kucoin",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "strategy_mode": "crossover",
                "target_metric": "sharpe_ratio|max_drawdown_pct",
                "best_value": 2.5,
                "best_params": json.dumps({"fast_ema": 9}),
                "trade_count": 10,
            }
        ]

        with patch(
            "src.routes.batch.DataLoader.load_candles",
            new_callable=AsyncMock,
        ) as mock_load_candles:
            import pandas as pd

            dates = pd.date_range("2024-01-01", periods=200, freq="5min", tz="UTC")
            mock_load_candles.return_value = pd.DataFrame(
                {
                    "timestamp": dates,
                    "open": [50000] * 200,
                    "high": [50100] * 200,
                    "low": [49900] * 200,
                    "close": [50000] * 200,
                    "volume": [100] * 200,
                }
            )

            with patch("src.validator.StrategyValidator") as mock_validator_class:
                mock_validator = Mock()
                mock_report = Mock(spec=ValidationReport)
                mock_report.status = "PASS"
                mock_report.to_dict.return_value = {"status": "PASS"}
                mock_validator.validate.return_value = mock_report
                mock_validator_class.return_value = mock_validator

                from src.routes.batch import auto_validate_batch_results

                req = BatchResultsAutoValidateRequest(
                    target_metric="sharpe_ratio|max_drawdown_pct"
                )
                result = await auto_validate_batch_results(req)

                assert result["validated_count"] == 1
                # Verify validator was called with multi-objective params
                call_kwargs = mock_validator.validate.call_args.kwargs
                assert call_kwargs["enable_multi_objective"] is True
                assert call_kwargs["multi_objective_metrics"] == [
                    "sharpe_ratio",
                    "max_drawdown_pct",
                ]

    async def test_optimal_lookback_days_used(self, mock_app, mock_batch_db):
        """Should use optimal lookback days for validation (Auto mode)."""
        mock_batch_db.load_batch_results.return_value = [
            {
                "id": 1,
                "exchange": "kucoin",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "strategy_mode": "crossover",
                "target_metric": "sharpe_ratio",
                "best_value": 2.5,
                "best_params": json.dumps({"fast_ema": 9}),
                "trade_count": 10,
            }
        ]

        with patch(
            "src.routes.batch.DataLoader.load_candles",
            new_callable=AsyncMock,
        ) as mock_load_candles:
            import pandas as pd

            dates = pd.date_range("2024-01-01", periods=200, freq="5min", tz="UTC")
            mock_load_candles.return_value = pd.DataFrame(
                {
                    "timestamp": dates,
                    "open": [50000] * 200,
                    "high": [50100] * 200,
                    "low": [49900] * 200,
                    "close": [50000] * 200,
                    "volume": [100] * 200,
                }
            )

            with patch("src.validator.StrategyValidator") as mock_validator_class:
                mock_validator = Mock()
                mock_report = Mock(spec=ValidationReport)
                mock_report.status = "PASS"
                mock_report.to_dict.return_value = {"status": "PASS"}
                mock_validator.validate.return_value = mock_report
                mock_validator_class.return_value = mock_validator

                with patch(
                    "src.routes.batch.get_optimal_lookback_days"
                ) as mock_get_lookback:
                    mock_get_lookback.return_value = 180

                    from src.routes.batch import auto_validate_batch_results

                    req = BatchResultsAutoValidateRequest(target_metric="sharpe_ratio")
                    _ = await auto_validate_batch_results(req)

                    # Verify get_optimal_lookback_days was called for the timeframe
                    mock_get_lookback.assert_called_with("5m")
                    # Verify load_candles was called with days=180, limit=None
                    mock_load_candles.assert_called_once()
                    call_kwargs = mock_load_candles.call_args.kwargs
                    assert call_kwargs["days"] == 180
                    assert call_kwargs["limit"] is None

    async def test_invalid_params_json_handled(self, mock_app, mock_batch_db):
        """Should handle invalid JSON in best_params gracefully (skips result)."""
        mock_batch_db.load_batch_results.return_value = [
            {
                "id": 1,
                "exchange": "kucoin",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "strategy_mode": "crossover",
                "target_metric": "sharpe_ratio",
                "best_value": 2.5,
                "best_params": "not valid json",
                "trade_count": 10,
            }
        ]

        with patch(
            "src.routes.batch.DataLoader.load_candles",
            new_callable=AsyncMock,
        ) as mock_load_candles:
            import pandas as pd

            dates = pd.date_range("2024-01-01", periods=200, freq="5min", tz="UTC")
            mock_load_candles.return_value = pd.DataFrame(
                {
                    "timestamp": dates,
                    "open": [50000] * 200,
                    "high": [50100] * 200,
                    "low": [49900] * 200,
                    "close": [50000] * 200,
                    "volume": [100] * 200,
                }
            )

            with patch("src.validator.StrategyValidator") as mock_validator_class:
                mock_validator = Mock()
                mock_report = Mock(spec=ValidationReport)
                mock_report.status = "PASS"
                mock_report.to_dict.return_value = {"status": "PASS"}
                mock_validator.validate.return_value = mock_report
                mock_validator_class.return_value = mock_validator

                from src.routes.batch import auto_validate_batch_results

                req = BatchResultsAutoValidateRequest(target_metric="sharpe_ratio")
                result = await auto_validate_batch_results(req)

                # Should skip result due to JSON parse error
                assert result["validated_count"] == 0
                assert len(result["errors"]) == 1
                assert "BTC/USD/5m" in result["errors"][0]
                assert "Expecting value" in result["errors"][0]

    async def test_empty_candles_handled(self, mock_app, mock_batch_db):
        """Should handle empty candle data gracefully."""
        mock_batch_db.load_batch_results.return_value = [
            {
                "id": 1,
                "exchange": "kucoin",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "strategy_mode": "crossover",
                "target_metric": "sharpe_ratio",
                "best_value": 2.5,
                "best_params": json.dumps({"fast_ema": 9}),
                "trade_count": 10,
            }
        ]

        with patch(
            "src.routes.batch.DataLoader.load_candles",
            new_callable=AsyncMock,
        ) as mock_load_candles:
            import pandas as pd

            mock_load_candles.return_value = pd.DataFrame()  # Empty dataframe

            with patch("src.validator.StrategyValidator") as mock_validator_class:
                mock_validator = Mock()
                mock_report = Mock(spec=ValidationReport)
                mock_report.status = "FAIL"
                mock_report.to_dict.return_value = {
                    "status": "FAIL",
                    "reason": "No data",
                }
                mock_validator.validate.return_value = mock_report
                mock_validator_class.return_value = mock_validator

                from src.routes.batch import auto_validate_batch_results

                req = BatchResultsAutoValidateRequest(target_metric="sharpe_ratio")
                result = await auto_validate_batch_results(req)

                assert result["validated_count"] == 1
                # Validation should still be persisted even if it fails
                mock_batch_db.update_batch_result_validation_pg.assert_called_once()

    async def test_auto_validate_respects_custom_n_trials_and_criteria(
        self, mock_app, mock_batch_db
    ):
        """Should forward custom n_trials and criteria to StrategyValidator."""
        mock_batch_db.load_batch_results.return_value = [
            {
                "id": 42,
                "exchange": "kucoin",
                "symbol": "BTC/USD",
                "timeframe": "15m",
                "strategy_mode": "crossover",
                "target_metric": "sharpe_ratio",
                "best_value": 2.1,
                "best_params": json.dumps({"fast_ema": 9, "slow_ema": 21}),
                "trade_count": 15,
            }
        ]

        with patch(
            "src.routes.batch.DataLoader.load_candles",
            new_callable=AsyncMock,
        ) as mock_load_candles:
            import pandas as pd

            dates = pd.date_range("2024-01-01", periods=200, freq="15min", tz="UTC")
            mock_load_candles.return_value = pd.DataFrame(
                {
                    "timestamp": dates,
                    "open": [50000] * 200,
                    "high": [50100] * 200,
                    "low": [49900] * 200,
                    "close": [50000] * 200,
                    "volume": [100] * 200,
                }
            )

            with patch("src.validator.StrategyValidator") as mock_validator_class:
                mock_validator = Mock()
                mock_report = Mock(spec=ValidationReport)
                mock_report.status = "PASS"
                mock_report.to_dict.return_value = {
                    "status": "PASS",
                    "gate_results": {},
                    "summary": "Passed with custom criteria",
                }
                mock_validator.validate.return_value = mock_report
                mock_validator_class.return_value = mock_validator

                from src.routes.batch import auto_validate_batch_results

                custom_criteria = {
                    "min_backtest_sharpe": 1.5,
                    "min_trade_count": 8,
                }
                req = BatchResultsAutoValidateRequest(
                    target_metric="sharpe_ratio",
                    n_trials=750,
                    criteria=custom_criteria,
                )
                result = await auto_validate_batch_results(req)

                assert result["status"] == "success"
                assert result["validated_count"] == 1

                # Verify StrategyValidator was instantiated with custom criteria
                mock_validator_class.assert_called_once()
                passed_crit = mock_validator_class.call_args[0][0]
                assert passed_crit.min_backtest_sharpe == 1.5
                assert passed_crit.min_trade_count == 8

                # Verify validate was called with n_trials=750
                mock_validator.validate.assert_called_once()
                call_kwargs = mock_validator.validate.call_args[1]
                assert call_kwargs["n_trials"] == 750

    async def test_exception_in_outer_try_block(self, mock_app, mock_batch_db):
        """Should return HTTP 500 for unexpected exceptions."""
        from fastapi import HTTPException

        mock_batch_db.load_batch_results.side_effect = Exception(
            "Database connection failed"
        )

        from src.routes.batch import auto_validate_batch_results

        req = BatchResultsAutoValidateRequest(target_metric="sharpe_ratio")

        with pytest.raises(HTTPException) as exc_info:
            await auto_validate_batch_results(req)

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Internal server error"


class TestAutoValidateRequestModel:
    """Tests for BatchResultsAutoValidateRequest Pydantic model."""

    def test_default_values(self):
        """Should have correct default values."""
        req = BatchResultsAutoValidateRequest()
        assert req.target_metric is None
        assert req.exchange == "kucoin"

    def test_custom_values(self):
        """Should accept custom values."""
        req = BatchResultsAutoValidateRequest(
            target_metric="total_return_pct",
            exchange="binance",
            n_trials=500,
            criteria={"min_backtest_sharpe": 1.2},
        )
        assert req.target_metric == "total_return_pct"
        assert req.exchange == "binance"
        assert req.n_trials == 500
        assert req.criteria == {"min_backtest_sharpe": 1.2}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
