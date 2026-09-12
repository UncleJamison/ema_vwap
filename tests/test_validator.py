"""
Unit tests for Automated Strategy Validator and Multi-Stage Robustness Gates.
"""

import os
import tempfile

from src.config import StrategyParams
from src.data_loader import DataLoader
from src.database import CandleDatabase
from src.settings import SettingsManager
from src.validator import StrategyValidator, ValidationCriteria, ValidationReport


def test_validator_insufficient_data():
    validator = StrategyValidator()
    df_empty = DataLoader.generate_synthetic_candles(num_bars=10, seed=1)
    params = StrategyParams()
    report = validator.validate(df_empty, params)

    assert isinstance(report, ValidationReport)
    assert report.status == "FAIL"
    assert report.gates_passed == 0
    assert report.recommended_action == "REJECT"


def test_validator_gate1_failure_on_poor_performer():
    # Parameters designed to fail (high penalty/strict filter causing 0 or negative trades)
    df = DataLoader.generate_synthetic_candles(num_bars=200, seed=42)
    # Using an impossibly high slope min will yield 0 trades or fail min return
    params = StrategyParams(
        strategy_mode="crossover",
        vwap_slope_min=100.0,
        volume_filter_enabled=True,
        volume_multiplier=100.0,
    )

    validator = StrategyValidator(
        criteria=ValidationCriteria(min_trade_count=5, min_backtest_return_pct=0.0)
    )
    report = validator.validate(df, params)

    assert report.status == "FAIL"
    assert "gate1_backtest" in report.gate_results
    assert report.gate_results["gate1_backtest"].passed is False
    assert len(report.reasons) >= 1


def test_validator_full_pipeline_robust():
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    # Reasonable trending crossover parameters
    params = StrategyParams(
        strategy_mode="crossover",
        fast_ema=5,
        slow_ema=15,
        trend_ema=50,
        vwap_slope_min=0.0,
        volume_filter_enabled=False,
    )

    # Lenient criteria to test full 3-gate execution and report serialization
    criteria = ValidationCriteria(
        min_backtest_return_pct=-50.0,
        min_backtest_sharpe=-50.0,
        min_trade_count=1,
        min_walk_forward_efficiency=0.0,
        min_profitable_windows_pct=0.0,
        min_oos_return_pct=-50.0,
        max_monte_carlo_ruin_pct=50.0,
        max_monte_carlo_drawdown_pct=80.0,
    )
    validator = StrategyValidator(criteria)
    report = validator.validate(df, params)

    assert isinstance(report, ValidationReport)
    assert report.total_gates == 3
    assert len(report.gate_results) == 3
    assert "gate1_backtest" in report.gate_results
    assert "gate2_wfo" in report.gate_results
    assert "gate3_monte_carlo" in report.gate_results

    # Verify report dictionary serialization
    report_dict = report.to_dict()
    assert "status" in report_dict
    assert "overall_score" in report_dict
    assert "gates_passed" in report_dict
    assert "gate_results" in report_dict


def test_validator_multi_objective_pipeline():
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    params = StrategyParams(
        strategy_mode="crossover",
        fast_ema=5,
        slow_ema=15,
        trend_ema=50,
        vwap_slope_min=0.0,
        volume_filter_enabled=False,
    )
    criteria = ValidationCriteria(
        min_backtest_return_pct=-50.0,
        min_backtest_sharpe=-50.0,
        min_trade_count=1,
        min_walk_forward_efficiency=0.0,
        min_profitable_windows_pct=0.0,
        min_oos_return_pct=-50.0,
        max_monte_carlo_ruin_pct=50.0,
        max_monte_carlo_drawdown_pct=80.0,
    )
    validator = StrategyValidator(criteria)
    report = validator.validate(
        df,
        params,
        n_trials=4,
        enable_multi_objective=True,
        multi_objective_metrics=["total_return_pct", "max_drawdown_pct"],
    )

    assert isinstance(report, ValidationReport)
    assert report.total_gates == 3
    assert len(report.gate_results) == 3
    assert "gate1_backtest" in report.gate_results
    assert "gate2_wfo" in report.gate_results
    assert "gate3_monte_carlo" in report.gate_results
    assert report.overall_score >= 0.0


def test_validator_mc_risk_default():
    """Test that StrategyValidator has default MC risk thresholds."""
    from src.validator import StrategyValidator

    validator = StrategyValidator()
    assert validator.criteria.max_monte_carlo_ruin_pct == 5.0
    assert validator.criteria.max_monte_carlo_drawdown_pct == 35.0


def test_validator_mc_risk_custom():
    """Test that custom ValidationCriteria sets MC risk thresholds."""
    from src.validator import StrategyValidator, ValidationCriteria

    crit = ValidationCriteria(
        max_monte_carlo_ruin_pct=10.0, max_monte_carlo_drawdown_pct=50.0
    )
    validator = StrategyValidator(criteria=crit)
    assert validator.criteria.max_monte_carlo_ruin_pct == 10.0
    assert validator.criteria.max_monte_carlo_drawdown_pct == 50.0


def test_validator_mc_risk_custom_criteria_overrides_settings():
    """Test that explicit ValidationCriteria overrides SettingsManager values."""

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_db_path = tmp.name
    try:
        db = CandleDatabase(db_path=tmp_db_path)
        mgr = SettingsManager(db)

        # Set custom values in settings
        mgr.set("max_monte_carlo_ruin_pct", 10.0)
        mgr.set("max_monte_carlo_drawdown_pct", 50.0)

        # But provide explicit criteria that should take precedence
        crit = ValidationCriteria(
            max_monte_carlo_ruin_pct=15.0, max_monte_carlo_drawdown_pct=45.0
        )
        validator = StrategyValidator(criteria=crit, settings_mgr=mgr)

        # Explicit criteria should win
        assert validator.criteria.max_monte_carlo_ruin_pct == 15.0
        assert validator.criteria.max_monte_carlo_drawdown_pct == 45.0
    finally:
        try:
            os.unlink(tmp_db_path)
        except PermissionError:
            pass  # File may be locked by another process; best-effort cleanup


def test_validator_mc_risk_with_settings_manager():
    """Test that StrategyValidator can read MC risk from SettingsManager."""

    # Use a temp db; cleanup best-effort (Windows file locks may persist)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_db_path = tmp.name
    try:
        db = CandleDatabase(db_path=tmp_db_path)
        mgr = SettingsManager(db)

        # Defaults should be read
        validator = StrategyValidator(settings_mgr=mgr)
        assert validator.criteria.max_monte_carlo_ruin_pct == 5.0
        assert validator.criteria.max_monte_carlo_drawdown_pct == 35.0

        # Custom values should override
        mgr.set("max_monte_carlo_ruin_pct", 10.0)
        mgr.set("max_monte_carlo_drawdown_pct", 50.0)
        validator2 = StrategyValidator(settings_mgr=mgr)
        assert validator2.criteria.max_monte_carlo_ruin_pct == 10.0
        assert validator2.criteria.max_monte_carlo_drawdown_pct == 50.0
    finally:
        try:
            os.unlink(tmp_db_path)
        except PermissionError:
            pass  # File may be locked by another process; best-effort cleanup
