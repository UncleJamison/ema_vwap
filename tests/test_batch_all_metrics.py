"""
Tests for Batch Sweep: All Metrics option for single-metric ranking (single-timeframe gate).
Bead: ema_vwap-byl
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import src.batch_optimizer as batch_module
from src.app import _run_all_metrics_batch_task, app
from src.batch_optimizer import (
    ALL_SINGLE_METRICS,
    BatchOptimizer,
    BatchOptimizerConfig,
    cancel_batch,
    get_batch_state,
    reset_batch_state,
)
from src.optimizer import TARGET_METRICS


@pytest.fixture(autouse=True)
def _clean_batch_state():
    reset_batch_state()
    yield
    reset_batch_state()


def test_all_single_metrics_canonical_definition():
    """Verify ALL_SINGLE_METRICS contains 10 canonical non-alias metrics matching TARGET_METRICS."""
    assert len(ALL_SINGLE_METRICS) == 10
    assert "sharpe" not in ALL_SINGLE_METRICS  # alias excluded
    for m in ALL_SINGLE_METRICS:
        assert m in TARGET_METRICS


def test_batch_optimize_all_metrics_multi_timeframe_rejected():
    """Verify HTTP 400 is raised if target_metric is 'all_metrics' with >1 timeframe."""
    client = TestClient(app)
    payload = {
        "symbols": ["BTC/USD"],
        "timeframes": ["5m", "15m"],
        "exchange": "kucoin",
        "target_metric": "all_metrics",
        "n_trials": 10,
    }
    resp = client.post("/api/batch_optimize", json=payload)
    assert resp.status_code == 400
    assert (
        "all_metrics target only supports a single timeframe" in resp.json()["detail"]
    )


def test_batch_optimize_all_combinations_single_timeframe_accepted(monkeypatch):
    """Verify HTTP 200 starts background task for all 10 single metrics."""
    client = TestClient(app)
    mock_add_task = MagicMock()

    payload = {
        "symbols": ["BTC/USD", "ETH/USD"],
        "timeframes": ["15m"],
        "exchange": "kucoin",
        "target_metric": "all_metrics",
        "n_trials": 10,
    }

    # Patch BackgroundTasks.add_task to inspect generated configs without running long trials
    monkeypatch.setattr("starlette.background.BackgroundTasks.add_task", mock_add_task)

    resp = client.post("/api/batch_optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "started"
    assert data["total_metrics"] == 10
    assert data["total_combos"] == 20  # 2 symbols * 1 timeframe * 10 metrics

    mock_add_task.assert_called_once()
    called_fn, configs = mock_add_task.call_args[0]
    assert called_fn == _run_all_metrics_batch_task
    assert len(configs) == 10
    assert [c.target_metric for c in configs] == ALL_SINGLE_METRICS
    for c in configs:
        assert c.timeframes == ["15m"]
        assert c.enable_multi_objective is False


def test_run_all_metrics_batch_task_lifecycle(monkeypatch):
    """Verify _run_all_metrics_batch_task runs configs sequentially and completes state."""
    execution_calls = []

    def fake_run(self, **kwargs):
        execution_calls.append(
            {
                "metric": self.config.target_metric,
                "reset_state": kwargs.get("reset_state"),
                "done_offset": kwargs.get("done_offset"),
                "total_override": kwargs.get("total_override"),
                "combo_label_prefix": kwargs.get("combo_label_prefix"),
                "mark_complete": kwargs.get("mark_complete"),
            }
        )
        return []

    monkeypatch.setattr(BatchOptimizer, "run", fake_run)

    configs = [
        BatchOptimizerConfig(symbols=["BTC/USD"], timeframes=["5m"], target_metric=m)
        for m in ALL_SINGLE_METRICS[:3]
    ]

    _run_all_metrics_batch_task(configs)

    assert len(execution_calls) == 3
    assert execution_calls[0]["done_offset"] == 0
    assert execution_calls[0]["total_override"] == 3
    assert execution_calls[0]["mark_complete"] is False
    assert "[sharpe_ratio — 1/3]" in execution_calls[0]["combo_label_prefix"]

    assert execution_calls[1]["done_offset"] == 1
    assert execution_calls[1]["mark_complete"] is False

    assert execution_calls[2]["done_offset"] == 2
    assert execution_calls[2]["mark_complete"] is True

    state = get_batch_state()
    assert state["running"] is False
    assert state["done"] == 3
    assert state["total"] == 3
    assert state["current_combo"] == "Complete"
    assert state["error"] is None


def test_run_all_metrics_batch_task_cancellation(monkeypatch):
    """Verify cooperative cancellation stops subsequent configs in all-metrics sweep."""
    runs = []

    def fake_run(self, **kwargs):
        runs.append(self.config.target_metric)
        if len(runs) == 1:
            cancel_batch()
        return []

    monkeypatch.setattr(BatchOptimizer, "run", fake_run)

    configs = [
        BatchOptimizerConfig(symbols=["BTC/USD"], timeframes=["5m"], target_metric=m)
        for m in ALL_SINGLE_METRICS[:3]
    ]

    _run_all_metrics_batch_task(configs)

    # Should have stopped after first config due to cancel
    assert len(runs) == 1
    state = get_batch_state()
    assert state["running"] is False
    assert state["error"] == "Batch sweep cancelled"


def test_batch_optimizer_run_offset_and_label_prefix(monkeypatch):
    """Verify BatchOptimizer.run respects combo_label_prefix and done_offset."""
    monkeypatch.setattr(
        BatchOptimizer,
        "_run_single_optuna",
        lambda self, sym, tf, **kwargs: None,
    )
    fake_db = SimpleNamespace(
        save_batch_result=lambda **kwargs: None,
        load_batch_results=lambda **kwargs: [],
    )
    monkeypatch.setattr(batch_module, "db", fake_db)

    cfg = BatchOptimizerConfig(
        symbols=["BTC/USD"], timeframes=["5m"], target_metric="sortino_ratio"
    )
    opt = BatchOptimizer(cfg)

    opt.run(
        reset_state=False,
        done_offset=5,
        total_override=10,
        combo_label_prefix="[sortino_ratio — 6/10] ",
        mark_complete=False,
    )

    state = get_batch_state()
    # mark_complete is False, so running remains True
    assert state["running"] is True
    assert state["done"] == 6
    assert state["total"] == 10
    assert "[sortino_ratio — 6/10] BTC/USD (5m)" in state["current_combo"]


def test_html_and_js_batch_all_metrics_markup():
    """Verify HTML select contains all_metrics option & hint, and JS implements syncBatchMetricUI."""
    from pathlib import Path

    html_content = Path("src/web/index.html").read_text(encoding="utf-8")
    assert 'value="all_metrics"' in html_content
    assert 'id="batch-all-metrics-hint"' in html_content
    assert 'value="all_combinations"' in html_content
    assert 'id="batch-all-multi-hint"' in html_content

    js_content = Path("src/web/app.js").read_text(encoding="utf-8")
    assert "syncBatchMetricUI" in js_content
    assert 'metricVal === "all_metrics"' in js_content
    assert 'multiVal === "all_combinations"' in js_content
    assert "timeframes.length !== 1" in js_content


def test_all_curated_multi_pairs_canonical_definition():
    """Verify ALL_CURATED_MULTI_PAIRS contains 12 non-redundant Pareto pairs from allowed pool."""
    from src.batch_optimizer import ALL_CURATED_MULTI_PAIRS

    assert len(ALL_CURATED_MULTI_PAIRS) == 12
    allowed_pool = TARGET_METRICS | {"max_drawdown_pct"}
    for pair in ALL_CURATED_MULTI_PAIRS:
        assert len(pair) == 2
        assert pair[0] != pair[1]
        assert pair[0] in allowed_pool
        assert pair[1] in allowed_pool


def test_batch_optimize_all_combinations_multi_timeframe_rejected():
    """Verify HTTP 400 is raised if target_metric is 'all_combinations' with >1 timeframe."""
    client = TestClient(app)
    payload = {
        "symbols": ["BTC/USD"],
        "timeframes": ["5m", "15m"],
        "exchange": "kucoin",
        "target_metric": "all_combinations",
        "enable_multi_objective": True,
        "multi_objective_metrics": ["all_combinations"],
        "n_trials": 10,
    }
    resp = client.post("/api/batch_optimize", json=payload)
    assert resp.status_code == 400


def test_run_all_combinations_batch_task_lifecycle(monkeypatch):
    """Verify _run_all_metrics_batch_task labels multi-objective pairs correctly in combo prefix."""
    from src.batch_optimizer import ALL_CURATED_MULTI_PAIRS

    execution_calls = []

    def fake_run(self, **kwargs):
        execution_calls.append(
            {
                "metrics": self.config.multi_objective_metrics,
                "prefix": kwargs.get("combo_label_prefix"),
            }
        )
        return []

    monkeypatch.setattr(BatchOptimizer, "run", fake_run)

    configs = [
        BatchOptimizerConfig(
            symbols=["BTC/USD"],
            timeframes=["5m"],
            target_metric=f"{pair[0]}|{pair[1]}",
            enable_multi_objective=True,
            multi_objective_metrics=pair,
        )
        for pair in ALL_CURATED_MULTI_PAIRS[:3]
    ]

    _run_all_metrics_batch_task(configs)

    assert len(execution_calls) == 3
    assert "[sharpe_ratio|max_drawdown_pct — 1/3]" in execution_calls[0]["prefix"]
    assert "[sharpe_ratio|total_return_pct — 2/3]" in execution_calls[1]["prefix"]
    assert "[sharpe_ratio|win_rate — 3/3]" in execution_calls[2]["prefix"]

    state = get_batch_state()
    assert state["running"] is False
    assert state["done"] == 3
    assert state["total"] == 3
    assert state["current_combo"] == "Complete"


def test_batch_optimize_all_combinations_target_metric_keys(monkeypatch):
    """Verify all_combinations config generation produces compound target_metric keys matching pair."""
    from src.batch_optimizer import ALL_CURATED_MULTI_PAIRS

    client = TestClient(app)
    mock_add_task = MagicMock()

    payload = {
        "symbols": ["BTC/USD"],
        "timeframes": ["5m"],
        "exchange": "kucoin",
        "target_metric": "all_combinations",
        "n_trials": 10,
    }

    monkeypatch.setattr("starlette.background.BackgroundTasks.add_task", mock_add_task)

    resp = client.post("/api/batch_optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "started"
    assert data["total_pairs"] == len(ALL_CURATED_MULTI_PAIRS)
    assert data["total_combos"] == len(ALL_CURATED_MULTI_PAIRS)

    mock_add_task.assert_called_once()
    called_fn, configs = mock_add_task.call_args[0]
    assert called_fn == _run_all_metrics_batch_task
    assert len(configs) == len(ALL_CURATED_MULTI_PAIRS)

    expected_targets = [f"{p[0]}|{p[1]}" for p in ALL_CURATED_MULTI_PAIRS]
    assert [c.target_metric for c in configs] == expected_targets
    for c, pair in zip(configs, ALL_CURATED_MULTI_PAIRS, strict=False):
        assert c.target_metric == f"{pair[0]}|{pair[1]}"
        assert c.enable_multi_objective is True
        assert c.multi_objective_metrics == pair
