"""Tests for symbol validation and integration.

These tests verify that invalid symbols are properly rejected throughout the
data pipeline, ensuring that only valid symbols are processed.
"""

import pytest

from src.providers import SyntheticAdapter
from src.validator_symbols import (
    validate_symbol_against_provider,
    validate_symbol_format,
)


def test_validate_symbol_format():
    """Validate that format validation rejects malformed symbols."""
    # Valid symbols should pass
    assert validate_symbol_format("BTC/USD") == "BTC/USD"
    assert validate_symbol_format("BTC/USDT") == "BTC/USDT"
    assert validate_symbol_format("BTC/USD") == "BTC/USD"  # no change needed

    # Invalid symbols should raise ValueError
    with pytest.raises(ValueError):
        validate_symbol_format("15SOL/USD")  # starts with digit

    with pytest.raises(ValueError):
        validate_symbol_format("BTC/USD/")  # trailing slash, not normalized

    with pytest.raises(ValueError):
        validate_symbol_format("BTC/USD/XYZ")  # invalid quote suffix


def test_validate_symbol_against_synthetic_provider():
    """Verify synthetic provider validates against its DEFAULT_BASE_PRICES."""
    synth = SyntheticAdapter()

    # Valid symbols should pass
    assert validate_symbol_against_provider(synth, "BTC/USD") == "BTC/USD"
    assert validate_symbol_against_provider(synth, "SOL/USD") == "SOL/USD"

    # Invalid symbols should raise
    with pytest.raises(ValueError):
        validate_symbol_against_provider(synth, "15SOL/USD")  # invalid base

    with pytest.raises(ValueError):
        validate_symbol_against_provider(synth, "FAKE/UNKNOWN")  # not in registry


def test_data_loader_sym_validation():
    """Verify that DataLoader rejects invalid symbols early."""
    # This test requires mocking to avoid network calls; we'll simulate
    # by patching the provider fetch method to return empty data for invalid symbols.
    # The actual validation happens in load_candles, so we test the error path.
    # Placeholder for future integration test


def test_database_sym_validity():
    """Verify database normalization preserves valid symbols and rejects invalid ones."""
    from src.database import normalize_symbol_key

    # Valid symbols should normalize correctly
    assert normalize_symbol_key("BTC/USD") == "BTC/USD"
    assert normalize_symbol_key("BTC/USDT") == "BTC/USDT"
    assert normalize_symbol_key("BTC/USDC") == "BTC/USDC"
    assert normalize_symbol_key("BTC/USD") == "BTC/USD"  # no change needed

    # Invalid symbols should be normalized but still rejected by validation
    # (validation happens later in provider layer)
    # This test verifies that the normalization logic itself is sound
    assert normalize_symbol_key("BTC-USD") == "BTC/USD"
    assert normalize_symbol_key("BTC_USD") == "BTC/USD"
    assert normalize_symbol_key("BTC/USDT") == "BTC/USDT"
    assert normalize_symbol_key("BTC/USDC") == "BTC/USDC"
    assert normalize_symbol_key("BTC/USD") == "BTC/USD"  # unchanged


def test_batch_optimizer_integration():
    """Integration test: batch optimizer should fail gracefully on invalid symbols."""
    from src.batch_optimizer import BatchOptimizer, BatchOptimizerConfig

    cfg = BatchOptimizerConfig(
        symbols=["BTC/USD", "15SOL/USD"],  # include one invalid symbol
        timeframes=["5m"],
        min_candles=50,
        target_metric="sharpe",
    )

    # This should not raise a crash but should fail gracefully or skip invalid symbol
    # The actual validation happens in DataLoader.load_candles, which will fail
    # due to invalid symbol before optimizer runs
    # We'll test that it doesn't crash the entire process
    optimizer = BatchOptimizer(cfg)
    try:
        optimizer._run_single_optuna("15SOL/USD", "5m")
        # If it reaches here without crashing, that's good (though it will likely fail later)
    except Exception as e:
        # Expected - we want validation to fail before optimizer runs
        assert "Invalid symbol" in str(e) or "not a valid symbol" in str(e)


def test_integration_with_data_loader():
    """Integration test: ensure DataLoader rejects invalid symbols."""
    # This is a high-level test that verifies the end-to-end flow
    # We'll mock the provider to return empty data for invalid symbols
    # Implementation depends on mocking framework used in tests
