"""
Unit tests for Gemini, KuCoin, and Robinhood Crypto API Key Settings Management.
"""

from src.database import CandleDatabase
from src.settings import SettingsManager, is_secret_key, mask_secret_value


def test_secret_key_detection_and_masking():
    """Verify secret keywords and masking logic for crypto exchanges."""
    assert is_secret_key("gemini_api_key") is True
    assert is_secret_key("gemini_secret_key") is True
    assert is_secret_key("kucoin_passphrase") is True
    assert is_secret_key("robinhood_private_key") is True
    assert is_secret_key("exchange_name") is False

    assert mask_secret_value("1234567890abcdef") == "••••••••cdef"
    assert mask_secret_value("short") == "••••••••hort"
    assert mask_secret_value("") == ""


def test_crypto_exchange_credentials_storage(tmp_path):
    """Verify storing and retrieving Gemini, KuCoin, and Robinhood credentials."""
    db_path = str(tmp_path / "test_settings.db")
    db = CandleDatabase(db_path=db_path)
    sm = SettingsManager(db=db)

    # Save credentials via update_bulk
    payload = {
        "gemini_api_key": "gemini-live-key-12345",
        "gemini_secret_key": "gemini-super-secret-xyz",
        "gemini_sandbox": "false",
        "kucoin_api_key": "kucoin-api-key-9999",
        "kucoin_secret_key": "kucoin-secret-key-8888",
        "kucoin_passphrase": "kucoin-passphrase-7777",
        "kucoin_sandbox": "true",
        "robinhood_api_key": "rh-key-1234-abcd",
        "robinhood_private_key": "b64_encoded_private_key_ed25519",
        "robinhood_account_number": "RH-ACC-5555",
    }
    sm.update_bulk(payload)

    # Verify Gemini credentials
    gemini = sm.get_gemini_credentials()
    assert gemini["api_key"] == "gemini-live-key-12345"
    assert gemini["secret_key"] == "gemini-super-secret-xyz"
    assert gemini["is_sandbox"] is False

    # Verify KuCoin credentials
    kucoin = sm.get_kucoin_credentials()
    assert kucoin["api_key"] == "kucoin-api-key-9999"
    assert kucoin["secret_key"] == "kucoin-secret-key-8888"
    assert kucoin["passphrase"] == "kucoin-passphrase-7777"
    assert kucoin["is_sandbox"] is True

    # Verify Robinhood credentials
    robinhood = sm.get_robinhood_credentials()
    assert robinhood["api_key"] == "rh-key-1234-abcd"
    assert robinhood["private_key"] == "b64_encoded_private_key_ed25519"
    assert robinhood["account_number"] == "RH-ACC-5555"

    # Verify get_all_masked masks the sensitive values
    masked = sm.get_all_masked()
    assert masked["gemini_api_key"].startswith("••••")
    assert masked["gemini_secret_key"].startswith("••••")
    assert masked["kucoin_passphrase"].startswith("••••")
    assert masked["robinhood_private_key"].startswith("••••")

    # Verify sending back masked value does NOT overwrite raw value
    sm.update_bulk({"gemini_secret_key": masked["gemini_secret_key"]})
    gemini_after = sm.get_gemini_credentials()
    assert gemini_after["secret_key"] == "gemini-super-secret-xyz"  # Intact!
