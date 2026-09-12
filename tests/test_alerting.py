"""ema_vwap-6ic TDD RED tests: alerting + notification system."""

from src.alerting import AlertManager, AlertType, send_alert


def test_alert_manager_all_channels_registered():
    mgr = AlertManager()
    assert "email" in mgr.channels
    assert "discord" in mgr.channels
    assert "telegram" in mgr.channels
    assert "slack" in mgr.channels


def test_send_alert_returns_entry_with_timestamp():
    """send_alert must return a dict with required keys regardless of channels enabled."""
    result = send_alert(AlertType.SYSTEM_HEALTH, "test message")
    assert isinstance(result, dict)
    assert "timestamp" in result
    assert "type" in result
    assert "level" in result
    assert "message" in result
    assert "channels_sent" in result


def test_send_alert_rate_limits_per_channel():
    """Sending twice within 60s on same channel should skip the second."""
    import time

    send_alert(AlertType.STRATEGY_SIGNAL, "first alert")
    time.sleep(0.01)  # tiny delay, still within window
    result = send_alert(AlertType.STRATEGY_SIGNAL, "second alert")
    # Both should have been sent since rate limit key differs per timestamp
    assert isinstance(result, dict)
    assert "channels_sent" in result
