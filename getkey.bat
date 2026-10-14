@echo off
docker exec ema_vwap_trading_daemon python -c "from src.settings import SettingsManager; from src.db_connection_manager import get_database; print(SettingsManager(get_database('system')).get('app_api_key'))"
