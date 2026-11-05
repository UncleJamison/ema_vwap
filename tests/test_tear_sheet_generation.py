"""
Tests for Tear Sheet Generation (src/routes/tear_sheet.py):
- POST /api/tear-sheet/json - JSON tear sheet (no WeasyPrint needed)
- POST /api/tear-sheet/pdf - PDF tear sheet (requires WeasyPrint/GTK)
- POST /api/tear-sheet/html - HTML tear sheet (requires WeasyPrint/GTK)
- TearSheetRenderer: context building, HTML/PDF rendering
- build_tear_sheet_context function
- PDF/HTML generation functions (mocked on Windows)
"""

from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.routes.tear_sheet import router as tear_sheet_router
from src.web.pdf_renderer import (
    TearSheetRenderer,
    build_tear_sheet_context,
    generate_tear_sheet_html,
    generate_tear_sheet_pdf,
)


class TestTearSheetRoutesJSON:
    """Tests for POST /api/tear-sheet/json endpoint (no WeasyPrint needed)."""

    def test_generate_tear_sheet_json_success(self, client, mock_tear_sheet_deps):
        """Should generate JSON tear sheet successfully."""
        mock_data_loader, mock_validator, mock_backtest, mock_mc = mock_tear_sheet_deps

        # Setup mocks
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
        mock_data_loader.load_candles.return_value = df

        mock_report = Mock()
        mock_report.to_dict.return_value = {
            "status": "PASSED",
            "overall_score": 85,
            "gate_results": {
                "gate1_backtest": {
                    "metrics": {"sharpe_ratio": 1.5, "total_return": 0.2}
                },
                "gate3_monte_carlo": {"metrics": {"risk_of_ruin_pct": 5.0}},
            },
        }
        mock_validator.return_value.validate.return_value = mock_report

        mock_bt_res = Mock()
        mock_bt_res.trades = [{"pnl": 100}, {"pnl": -50}, {"pnl": 200}]
        mock_backtest.return_value.run.return_value = mock_bt_res

        mock_mc_report = {
            "equity_curves_percentiles": {
                "p5": [1, 2, 3],
                "p50": [10, 20, 30],
                "p95": [100, 200, 300],
            },
            "final_equity_percentiles": {"p5": 5000, "p50": 15000, "p95": 25000},
            "net_profit_percentiles": {"p5": -1000, "p50": 2000, "p95": 5000},
            "max_drawdown_percentiles": {"p5": 0.05, "p50": 0.15, "p95": 0.30},
            "sharpe_ratio_percentiles": {"p5": 0.5, "p50": 1.5, "p95": 2.5},
            "risk_of_ruin_pct": 2.5,
            "num_simulations": 500,
        }
        mock_mc_sim = Mock()
        mock_mc_sim.run.return_value = mock_mc_report
        mock_mc.return_value = mock_mc_sim

        response = client.post(
            "/api/tear-sheet/json",
            json={
                "exchange": "gemini",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "params": {
                    "fast_ema": 9,
                    "slow_ema": 21,
                    "vwap_window": 20,
                    "rsi_period": 14,
                    "rsi_oversold": 30,
                    "rsi_overbought": 70,
                    "atr_period": 14,
                    "atr_multiplier": 2.0,
                    "use_trailing_stop": True,
                    "trailing_stop_type": "atr",
                    "initial_capital": 10000,
                },
                "mc_simulations": 100,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["symbol"] == "BTC/USD"
        assert data["timeframe"] == "5m"
        assert data["exchange"] == "gemini"
        assert "report" in data
        assert "trade_distribution" in data
        assert "equity_curves" in data
        assert "monte_carlo_percentiles" in data

    def test_generate_tear_sheet_json_with_validation_report(
        self, client, mock_tear_sheet_deps
    ):
        """Should use provided validation report instead of running validation."""
        mock_data_loader, mock_validator, mock_backtest, mock_mc = mock_tear_sheet_deps

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
        mock_data_loader.load_candles.return_value = df

        mock_bt_res = Mock()
        mock_bt_res.trades = [{"pnl": 100}]
        mock_backtest.return_value.run.return_value = mock_bt_res

        mock_mc_report = {
            "equity_curves_percentiles": {"p5": [], "p50": [], "p95": []},
            "final_equity_percentiles": {},
            "net_profit_percentiles": {},
            "max_drawdown_percentiles": {},
            "sharpe_ratio_percentiles": {},
            "risk_of_ruin_pct": 0,
            "num_simulations": 500,
        }
        mock_mc_sim = Mock()
        mock_mc_sim.run.return_value = mock_mc_report
        mock_mc.return_value = mock_mc_sim

        validation_report = {
            "status": "PASSED",
            "overall_score": 90,
            "gate_results": {},
        }

        response = client.post(
            "/api/tear-sheet/json",
            json={
                "exchange": "gemini",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "params": {"fast_ema": 9, "slow_ema": 21},
                "validation_report": validation_report,
            },
        )

        assert response.status_code == 200
        # Validator should not have been called
        mock_validator.return_value.validate.assert_not_called()

    def test_generate_tear_sheet_json_requires_auth(self):
        """Should require API key authentication."""
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(tear_sheet_router)
        raw_client = TestClient(app)

        response = raw_client.post(
            "/api/tear-sheet/json",
            json={
                "exchange": "gemini",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "params": {},
            },
        )
        assert response.status_code in (401, 403)

    def test_generate_tear_sheet_json_rate_limited(self, client, mock_tear_sheet_deps):
        """Should be rate limited."""
        # The rate limiter is tested elsewhere; just verify endpoint exists
        mock_data_loader, mock_validator, mock_backtest, mock_mc = mock_tear_sheet_deps
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
        mock_data_loader.load_candles.return_value = df

        mock_report = Mock()
        mock_report.to_dict.return_value = {
            "status": "PASSED",
            "overall_score": 85,
            "gate_results": {},
        }
        mock_validator.return_value.validate.return_value = mock_report

        mock_bt_res = Mock()
        mock_bt_res.trades = []
        mock_backtest.return_value.run.return_value = mock_bt_res

        mock_mc_report = {
            "equity_curves_percentiles": {"p5": [], "p50": [], "p95": []},
            "final_equity_percentiles": {},
            "net_profit_percentiles": {},
            "max_drawdown_percentiles": {},
            "sharpe_ratio_percentiles": {},
            "risk_of_ruin_pct": 0,
            "num_simulations": 500,
        }
        mock_mc_sim = Mock()
        mock_mc_sim.run.return_value = mock_mc_report
        mock_mc.return_value = mock_mc_sim

        response = client.post(
            "/api/tear-sheet/json",
            json={
                "exchange": "gemini",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "params": {},
            },
        )
        assert response.status_code == 200


class TestTearSheetRoutesPDF:
    """Tests for POST /api/tear-sheet/pdf endpoint (mocked on Windows)."""

    def test_generate_tear_sheet_pdf_mocked(
        self, client, mock_tear_sheet_deps, mock_pdf_renderer
    ):
        """Should generate PDF tear sheet when WeasyPrint is available (mocked)."""
        mock_data_loader, mock_validator, mock_backtest, mock_mc = mock_tear_sheet_deps
        mock_generate_pdf, _ = mock_pdf_renderer

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
        mock_data_loader.load_candles.return_value = df

        mock_report = Mock()
        mock_report.to_dict.return_value = {
            "status": "PASSED",
            "overall_score": 85,
            "gate_results": {
                "gate1_backtest": {
                    "metrics": {"total_return_pct": 20.0, "sharpe_ratio": 1.5}
                },
                "gate3_monte_carlo": {"metrics": {"risk_of_ruin_pct": 5.0}},
            },
        }
        mock_validator.return_value.validate.return_value = mock_report

        mock_bt_res = Mock()
        mock_bt_res.trades = [{"pnl": 100}]
        mock_backtest.return_value.run.return_value = mock_bt_res

        mock_mc_report = {
            "equity_curves_percentiles": {"p5": [], "p50": [], "p95": []},
            "final_equity_percentiles": {},
            "net_profit_percentiles": {},
            "max_drawdown_percentiles": {},
            "sharpe_ratio_percentiles": {},
            "risk_of_ruin_pct": 0,
            "num_simulations": 500,
        }
        mock_mc_sim = Mock()
        mock_mc_sim.run.return_value = mock_mc_report
        mock_mc.return_value = mock_mc_sim

        mock_generate_pdf.return_value = b"%PDF-1.4 test pdf content"

        response = client.post(
            "/api/tear-sheet/pdf",
            json={
                "exchange": "gemini",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "params": {},
            },
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "attachment" in response.headers["content-disposition"]
        assert response.content == b"%PDF-1.4 test pdf content"


class TestTearSheetRoutesHTML:
    """Tests for POST /api/tear-sheet/html endpoint (mocked on Windows)."""

    def test_generate_tear_sheet_html_mocked(
        self, client, mock_tear_sheet_deps, mock_pdf_renderer
    ):
        """Should generate HTML tear sheet when WeasyPrint is available (mocked)."""
        mock_data_loader, mock_validator, mock_backtest, mock_mc = mock_tear_sheet_deps
        _, mock_generate_html = mock_pdf_renderer

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
        mock_data_loader.load_candles.return_value = df

        mock_report = Mock()
        mock_report.to_dict.return_value = {
            "status": "PASSED",
            "overall_score": 85,
            "gate_results": {
                "gate1_backtest": {
                    "metrics": {"total_return_pct": 20.0, "sharpe_ratio": 1.5}
                },
                "gate3_monte_carlo": {"metrics": {"risk_of_ruin_pct": 5.0}},
            },
        }
        mock_validator.return_value.validate.return_value = mock_report

        mock_bt_res = Mock()
        mock_bt_res.trades = [{"pnl": 100}]
        mock_backtest.return_value.run.return_value = mock_bt_res

        mock_mc_report = {
            "equity_curves_percentiles": {"p5": [], "p50": [], "p95": []},
            "final_equity_percentiles": {},
            "net_profit_percentiles": {},
            "max_drawdown_percentiles": {},
            "sharpe_ratio_percentiles": {},
            "risk_of_ruin_pct": 0,
            "num_simulations": 500,
        }
        mock_mc_sim = Mock()
        mock_mc_sim.run.return_value = mock_mc_report
        mock_mc.return_value = mock_mc_sim

        mock_generate_html.return_value = "<html><body>Test Tear Sheet</body></html>"

        response = client.post(
            "/api/tear-sheet/html",
            json={
                "exchange": "gemini",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "params": {},
            },
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert b"Test Tear Sheet" in response.content


class TestTearSheetRenderer:
    """Tests for TearSheetRenderer class (mocked WeasyPrint)."""

    @pytest.fixture
    def mock_renderer(self):
        """Create renderer with mocked WeasyPrint."""
        with patch("src.web.pdf_renderer.HTML") as mock_html:
            mock_doc = Mock()
            mock_doc.write_pdf.return_value = b"PDF_CONTENT"
            mock_html.return_value = mock_doc

            with patch("src.web.pdf_renderer.FontConfiguration"):
                renderer = TearSheetRenderer()
                yield renderer, mock_html

    def test_render_html(self, mock_renderer):
        """Should render HTML from template."""
        renderer, _ = mock_renderer

        with patch.object(renderer.env, "get_template") as mock_get_template:
            mock_template = Mock()
            mock_template.render.return_value = "<html>Test</html>"
            mock_get_template.return_value = mock_template

            result = renderer.render_html({"symbol": "BTC/USD"})

            assert result == "<html>Test</html>"
            mock_template.render.assert_called_once_with(symbol="BTC/USD")

    def test_render_pdf(self, mock_renderer):
        """Should render PDF from template."""
        renderer, mock_html = mock_renderer

        with patch.object(renderer.env, "get_template") as mock_get_template:
            mock_template = Mock()
            mock_template.render.return_value = "<html>Test</html>"
            mock_get_template.return_value = mock_template

            result = renderer.render_pdf({"symbol": "BTC/USD"})

            assert result == b"PDF_CONTENT"
            mock_html.assert_called_once()

    def test_save_pdf(self, mock_renderer):
        """Should save PDF to file."""
        renderer, _ = mock_renderer

        with patch.object(renderer, "render_pdf", return_value=b"PDF_CONTENT"), patch(
            "pathlib.Path.write_bytes"
        ) as mock_write:
            _ = renderer.save_pdf({"symbol": "BTC/USD"}, "/tmp/test.pdf")
            mock_write.assert_called_once_with(b"PDF_CONTENT")


class TestBuildTearSheetContext:
    """Tests for build_tear_sheet_context function."""

    def test_build_context_basic(self):
        """Should build context with all required fields."""
        report = {
            "status": "PASSED",
            "overall_score": 85,
            "gate_results": {
                "gate1_backtest": {"metrics": {"sharpe_ratio": 1.5}},
                "gate3_monte_carlo": {"metrics": {"risk_of_ruin_pct": 5.0}},
            },
        }

        context = build_tear_sheet_context(
            report=report,
            strategy_params={"fast_ema": 9, "slow_ema": 21},
            symbol="BTC/USD",
            timeframe="5m",
            exchange="gemini",
        )

        assert context["symbol"] == "BTC/USD"
        assert context["timeframe"] == "5m"
        assert context["exchange"] == "gemini"
        assert context["status"] == "PASSED"
        assert context["overall_score"] == 85
        assert context["g1_metrics"]["sharpe_ratio"] == 1.5
        assert context["g3_metrics"]["risk_of_ruin_pct"] == 5.0
        assert context["strategy_params"]["fast_ema"] == 9
        assert "report_id" in context

    def test_build_context_with_optional_fields(self):
        """Should include optional fields when provided."""
        report = {"status": "PASSED", "overall_score": 85, "gate_results": {}}

        context = build_tear_sheet_context(
            report=report,
            strategy_params={},
            symbol="BTC/USD",
            timeframe="5m",
            exchange="gemini",
            trade_distribution={"total_trades": 10, "win_rate_pct": 60.0},
            equity_curves=[{"p5": 1, "p50": 2, "p95": 3}],
            monte_carlo_percentiles={"final_equity": {"p50": 15000}},
        )

        assert context["trade_distribution"]["total_trades"] == 10
        assert context["equity_curves"] == [{"p5": 1, "p50": 2, "p95": 3}]
        assert context["monte_carlo_percentiles"]["final_equity"]["p50"] == 15000

    def test_build_context_handles_missing_gate_results(self):
        """Should handle missing gate results gracefully."""
        report = {"status": "FAILED", "overall_score": 0}

        context = build_tear_sheet_context(
            report=report,
            strategy_params={},
            symbol="BTC/USD",
            timeframe="5m",
            exchange="gemini",
        )

        assert context["g1_metrics"] == {}
        assert context["g3_metrics"] == {}


class TestGenerateFunctions:
    """Tests for generate_tear_sheet_pdf and generate_tear_sheet_html functions."""

    def test_generate_tear_sheet_pdf_mocked(self):
        """Should call renderer and return PDF bytes."""
        with patch("src.web.pdf_renderer.TearSheetRenderer") as mock_renderer_class:
            mock_renderer = Mock()
            mock_renderer.render_pdf.return_value = b"PDF_BYTES"
            mock_renderer_class.return_value = mock_renderer

            result = generate_tear_sheet_pdf(
                report={"status": "PASSED"},
                strategy_params={},
                symbol="BTC/USD",
                timeframe="5m",
                exchange="gemini",
            )

            assert result == b"PDF_BYTES"
            mock_renderer.render_pdf.assert_called_once()

    def test_generate_tear_sheet_html_mocked(self):
        """Should call renderer and return HTML string."""
        with patch("src.web.pdf_renderer.TearSheetRenderer") as mock_renderer_class:
            mock_renderer = Mock()
            mock_renderer.render_html.return_value = "<html>Test</html>"
            mock_renderer_class.return_value = mock_renderer

            result = generate_tear_sheet_html(
                report={"status": "PASSED"},
                strategy_params={},
                symbol="BTC/USD",
                timeframe="5m",
                exchange="gemini",
            )

            assert result == "<html>Test</html>"
            mock_renderer.render_html.assert_called_once()


class TestTearSheetErrorHandling:
    """Tests for error handling in tear sheet endpoints."""

    def test_json_endpoint_handles_data_loader_error(self, client):
        """Should return 500 when data loading fails."""
        with patch("src.routes.tear_sheet.DataLoader") as mock_dl:
            mock_dl.load_candles.side_effect = Exception("Database error")

            response = client.post(
                "/api/tear-sheet/json",
                json={
                    "exchange": "gemini",
                    "symbol": "BTC/USD",
                    "timeframe": "5m",
                    "params": {},
                },
            )

            assert response.status_code == 500

    def test_json_endpoint_handles_validation_error(self, client, mock_tear_sheet_deps):
        """Should return 500 when validation fails."""
        mock_data_loader, mock_validator, _mock_backtest, _mock_mc = (
            mock_tear_sheet_deps
        )

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
        mock_data_loader.load_candles.return_value = df
        mock_validator.return_value.validate.side_effect = Exception(
            "Validation failed"
        )

        response = client.post(
            "/api/tear-sheet/json",
            json={
                "exchange": "gemini",
                "symbol": "BTC/USD",
                "timeframe": "5m",
                "params": {},
            },
        )

        assert response.status_code == 500


class TestTearSheetRequestModel:
    """Tests for TearSheetRequest Pydantic model."""

    def test_valid_request(self):
        """Should accept valid request."""
        from src.routes.tear_sheet import TearSheetRequest

        req = TearSheetRequest(
            exchange="gemini",
            symbol="BTC/USD",
            timeframe="5m",
            params={"fast_ema": 9, "slow_ema": 21},
        )

        assert req.exchange == "gemini"
        assert req.symbol == "BTC/USD"
        assert req.timeframe == "5m"
        assert req.mc_simulations == 500  # default
        assert req.target_metric == "sharpe_ratio"  # default

    def test_request_with_all_optional_fields(self):
        """Should accept request with all optional fields."""
        from src.routes.tear_sheet import TearSheetRequest

        req = TearSheetRequest(
            exchange="gemini",
            symbol="BTC/USD",
            timeframe="5m",
            params={},
            validation_report={"status": "PASSED"},
            mc_simulations=1000,
            target_metric="calmar_ratio",
            enable_multi_objective=True,
            multi_objective_metrics=["sharpe_ratio", "calmar_ratio"],
        )

        assert req.validation_report == {"status": "PASSED"}
        assert req.mc_simulations == 1000
        assert req.target_metric == "calmar_ratio"
        assert req.enable_multi_objective is True
        assert req.multi_objective_metrics == ["sharpe_ratio", "calmar_ratio"]


# Fixtures
@pytest.fixture
def client():
    """Create test client with tear sheet router and auth override."""
    from fastapi import FastAPI

    from src.auth import get_api_key

    app = FastAPI()
    app.include_router(tear_sheet_router)

    # Override auth dependency for testing
    async def override_get_api_key():
        return "test-api-key"

    app.dependency_overrides[get_api_key] = override_get_api_key

    return TestClient(app)


@pytest.fixture
def mock_tear_sheet_deps():
    """Mock all dependencies for tear sheet generation."""
    with patch("src.routes.tear_sheet.DataLoader") as mock_data_loader, patch(
        "src.routes.tear_sheet.StrategyValidator"
    ) as mock_validator, patch(
        "src.routes.tear_sheet.BacktestEngine"
    ) as mock_backtest, patch(
        "src.routes.tear_sheet.MonteCarloSimulator"
    ) as mock_mc, patch(
        "src.routes.tear_sheet.get_optimal_lookback_days", return_value=30
    ):
        mock_data_loader.resolve_canonical_symbol.return_value = ("gemini", "BTC/USD")
        mock_data_loader.aresolve_canonical_symbol.return_value = ("gemini", "BTC/USD")
        yield mock_data_loader, mock_validator, mock_backtest, mock_mc


@pytest.fixture
def mock_pdf_renderer():
    """Mock PDF/HTML generation functions."""
    # The endpoint imports from src.web.pdf_renderer
    with patch("src.web.pdf_renderer.generate_tear_sheet_pdf") as mock_pdf, patch(
        "src.web.pdf_renderer.generate_tear_sheet_html"
    ) as mock_html:

        yield mock_pdf, mock_html


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
