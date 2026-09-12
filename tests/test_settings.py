"""
Unit tests for SettingsManager and secret masking.
"""

import os

from src.database import CandleDatabase
from src.settings import SettingsManager, mask_secret_value


def test_secret_masking_format():
    assert mask_secret_value("") == ""
    assert mask_secret_value("123") == "••••"
    assert mask_secret_value("1234567890") == "••••••••7890"


def test_settings_manager_crud_and_masking(tmp_path):
    db_file = os.path.join(tmp_path, "test_settings.db")
    db = CandleDatabase(db_path=db_file)
    mgr = SettingsManager(db=db)

    mgr.set("exchange_name", "kucoin")
    mgr.set("kucoin_api_secret", "super_secret_passphrase_1234")

    assert mgr.get("exchange_name") == "kucoin"
    assert mgr.get("kucoin_api_secret") == "super_secret_passphrase_1234"

    assert mgr.get_masked("exchange_name") == "kucoin"
    assert mgr.get_masked("kucoin_api_secret") == "••••••••1234"

    all_masked = mgr.get_all_masked()
    assert all_masked["exchange_name"] == "kucoin"
    assert all_masked["kucoin_api_secret"] == "••••••••1234"


def test_settings_manager_bulk_update_skips_placeholders(tmp_path):
    db_file = os.path.join(tmp_path, "test_settings_bulk.db")
    db = CandleDatabase(db_path=db_file)
    mgr = SettingsManager(db=db)

    mgr.set("gemini_api_secret", "original_secret_9999")

    # Bulk update with masked placeholder should skip modifying secret
    updated = mgr.update_bulk(
        {
            "gemini_api_secret": "••••••••9999",
            "default_risk_pct": "2.5",
        }
    )

    assert updated == 1
    assert mgr.get("gemini_api_secret") == "original_secret_9999"
    assert mgr.get("default_risk_pct") == "2.5"
