"""
Unit tests for SettingsManager and secret masking.
"""

import os
from unittest.mock import AsyncMock

from src.database import CandleDatabase
from src.settings import SettingsManager, mask_secret_value


def test_secret_masking_format():
    assert mask_secret_value("") == ""
    assert mask_secret_value("123") == "••••"
    assert mask_secret_value("1234567890") == "••••••••7890"


def test_settings_manager_crud_and_masking(tmp_path, monkeypatch):

    # Mock PG functions
    mock_save = AsyncMock()
    mock_load = AsyncMock(return_value=None)
    mock_load_all = AsyncMock(return_value=[])

    monkeypatch.setattr("src.settings.save_setting_pg", mock_save)
    monkeypatch.setattr("src.settings.get_setting_pg", mock_load)
    monkeypatch.setattr("src.settings.load_all_settings_pg", mock_load_all)

    db_file = os.path.join(tmp_path, "system_test_settings.db")
    CandleDatabase(db_path=db_file)
    mgr = SettingsManager()

    # Track settings in memory for get() calls
    settings_store = {}

    def mock_save(key, value, is_secret=None):
        settings_store[key] = str(value)

    def mock_load(key):
        return {"value": settings_store.get(key)} if key in settings_store else None

    def mock_load_all():
        return [
            {"key": k, "value": v, "is_secret": False}
            for k, v in settings_store.items()
        ]

    monkeypatch.setattr(
        "src.settings.save_setting_pg", AsyncMock(side_effect=mock_save)
    )
    monkeypatch.setattr("src.settings.get_setting_pg", AsyncMock(side_effect=mock_load))
    monkeypatch.setattr(
        "src.settings.load_all_settings_pg", AsyncMock(side_effect=mock_load_all)
    )

    mgr.set("exchange_name", "kucoin")
    mgr.set("kucoin_api_secret", "super_secret_passphrase_1234")

    assert mgr.get("exchange_name") == "kucoin"
    assert mgr.get("kucoin_api_secret") == "super_secret_passphrase_1234"

    assert mgr.get_masked("exchange_name") == "kucoin"
    assert mgr.get_masked("kucoin_api_secret") == "••••••••1234"

    all_masked = mgr.get_all_masked()
    assert all_masked["exchange_name"] == "kucoin"
    assert all_masked["kucoin_api_secret"] == "••••••••1234"


def test_settings_manager_bulk_update_skips_placeholders(tmp_path, monkeypatch):

    # Mock PG functions
    mock_save = AsyncMock()
    mock_load = AsyncMock(return_value=None)
    mock_load_all = AsyncMock(return_value=[])

    monkeypatch.setattr("src.settings.save_setting_pg", mock_save)
    monkeypatch.setattr("src.settings.get_setting_pg", mock_load)
    monkeypatch.setattr("src.settings.load_all_settings_pg", mock_load_all)

    db_file = os.path.join(tmp_path, "system_test_settings_bulk.db")
    CandleDatabase(db_path=db_file)
    mgr = SettingsManager()

    # Track settings in memory for get() calls
    settings_store = {}

    def mock_save(key, value, is_secret=None):
        settings_store[key] = str(value)

    def mock_load(key):
        return {"value": settings_store.get(key)} if key in settings_store else None

    def mock_load_all():
        return [
            {"key": k, "value": v, "is_secret": False}
            for k, v in settings_store.items()
        ]

    monkeypatch.setattr(
        "src.settings.save_setting_pg", AsyncMock(side_effect=mock_save)
    )
    monkeypatch.setattr("src.settings.get_setting_pg", AsyncMock(side_effect=mock_load))
    monkeypatch.setattr(
        "src.settings.load_all_settings_pg", AsyncMock(side_effect=mock_load_all)
    )

    mgr.set("gemini_api_secret", "original_secret_9999")

    # Bulk update with masked placeholder should skip modifying secret
    updated = mgr.update_bulk(
        {
            "gemini_api_secret": "••••••••9999",
            "default_risk_pct": "2.5",
        }
    )

    # Only default_risk_pct should be updated
    assert updated == 1
    assert mgr.get("gemini_api_secret") == "original_secret_9999"
    assert mgr.get("default_risk_pct") == "2.5"
