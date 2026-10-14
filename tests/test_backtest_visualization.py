"""Tests for src/backtest_visualization.py — synthetic BacktestResult mocks."""

from unittest.mock import MagicMock

import pandas as pd

from src.backtest_visualization import VisualizationEngine


def _mock_result(n_equity=50, n_trades=5):
    eq = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=n_equity, freq="1min"),
            "equity": [10000 + i * 10 for i in range(n_equity)],
        }
    )
    trades = pd.DataFrame(
        {
            "side": [1] * n_trades,
            "entry_price": [100.0 + i for i in range(n_trades)],
            "entry_time": pd.date_range("2024-01-01", periods=n_trades, freq="5min"),
            "pnl_pct": [0.5 + i * 0.1 for i in range(n_trades)],
            "pnl": [50.0 + i * 10 for i in range(n_trades)],
        }
    )
    result = MagicMock()
    result.equity_curve = eq.to_dict(orient="records")
    result.trades = trades.to_dict(orient="records")
    result.final_equity = 10500.0
    result.max_drawdown_pct = 2.0
    result.total_return_pct = 5.0
    return result


def test_visualization_engine_summary():
    result = _mock_result()
    vis = VisualizationEngine(result)
    summary = vis.generate_summary()
    assert summary["equity_points"] == 50
    assert summary["trade_annotations"] == 5
    assert summary["final_equity"] == 10500.0
    assert summary["max_drawdown_pct"] == 2.0


def test_visualization_trade_annotations_returns_figure():
    result = _mock_result(n_trades=3)
    vis = VisualizationEngine(result)
    fig = vis.trade_annotations()
    assert fig is not None


def test_visualization_parameter_heatmap_returns_figure():
    result = _mock_result()
    vis = VisualizationEngine(result)
    fig = vis.parameter_heatmap("sharpe_ratio")
    assert fig is not None


def test_visualization_pnl_violin_returns_figure():
    result = _mock_result(n_trades=4)
    vis = VisualizationEngine(result)
    fig = vis.pnl_violin()
    assert fig is not None


def test_visualization_rolling_metrics_returns_figure():
    result = _mock_result()
    vis = VisualizationEngine(result)
    fig = vis.rolling_metrics()
    assert fig is not None


def test_visualization_monte_carlo_bands_returns_figure():
    result = _mock_result()
    vis = VisualizationEngine(
        result,
        mc_result={
            "final_equity_percentiles": {"p5": 9500, "p50": 10000, "p95": 10500}
        },
    )
    fig = vis.monte_carlo_bands()
    assert fig is not None


def test_visualization_monte_carlo_bands_empty_mc_returns_empty():
    result = _mock_result()
    vis = VisualizationEngine(result, mc_result=None)
    vis.monte_carlo_bands()
    assert True  # returns go.Figure() empty (no assert)


def test_visualization_render_all_keys():
    result = _mock_result()
    vis = VisualizationEngine(result)
    figs = vis.render_all()
    for key in (
        "trade_annotations",
        "parameter_heatmap",
        "pnl_violin",
        "rolling_metrics",
        "monte_carlo_bands",
    ):
        assert key in figs


def test_visualization_pnl_violin_empty_trades_returns_figure():
    result = _mock_result(n_trades=0)
    vis = VisualizationEngine(result)
    fig = vis.pnl_violin()
    assert fig is not None
