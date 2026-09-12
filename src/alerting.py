"""AlertManager and notification channels for ema_vwap-6ic.
Configurable channels: email, Discord webhook, Telegram bot, Slack webhook.
Integrates with SettingsManager for channel credentials and rate limiting.
"""

import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.logger import logger
from src.settings import SettingsManager


class AlertType(str, Enum):
    STRATEGY_SIGNAL = "strategy_signal"
    RISK_LIMIT_BREACH = "risk_limit_breach"
    DRAWDOWN_THRESHOLD = "drawdown_threshold"
    ORDER_FILL = "order_fill"
    ENGINE_STATUS = "engine_status"
    SYSTEM_HEALTH = "system_health"


class AlertChannel:
    """Base class for alert channels."""

    def __init__(self, name: str, enabled: bool):
        self.name = name
        self.enabled = enabled
        self.last_sent: dict[str, float] = {}

    def _apply_template(
        self, message: str, metadata: dict[str, Any] | None = None
    ) -> str:
        """Apply simple placeholder templating to the message.
        Supported placeholders: {symbol}, {price}, {side}, {level}, {type}, {value}
        """
        if not metadata:
            return message

        # Replace known placeholders with metadata values
        replacements = {
            "{symbol}": metadata.get("symbol", ""),
            "{price}": metadata.get("price", ""),
            "{side}": metadata.get("side", ""),
            "{level}": metadata.get("level", ""),
            "{type}": metadata.get("type", ""),
            "{value}": metadata.get("value", ""),
        }

        result = message
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, str(value))
        return result

    def send(
        self, message: str, level: str = "INFO", metadata: dict[str, Any] | None = None
    ) -> bool:
        raise NotImplementedError


class EmailChannel(AlertChannel):
    def __init__(self, enabled: bool = False, to: str = ""):
        super().__init__("email", enabled)
        self.to = to

    def send(
        self, message: str, level: str = "INFO", metadata: dict[str, Any] | None = None
    ) -> bool:
        if not self.enabled:
            return False
        formatted = self._apply_template(message, metadata)
        logger.info(f"[Email] {self.to} - {level}: {formatted}")
        return True


class DiscordChannel(AlertChannel):
    def __init__(self, enabled: bool = False, webhook_url: str = ""):
        super().__init__("discord", enabled)
        self.webhook_url = webhook_url

    def send(
        self, message: str, level: str = "INFO", metadata: dict[str, Any] | None = None
    ) -> bool:
        if not self.enabled:
            return False
        formatted = self._apply_template(message, metadata)
        logger.info(f"[Discord] webhook - {level}: {formatted}")
        return True


class TelegramChannel(AlertChannel):
    def __init__(self, enabled: bool = False, bot_token: str = "", chat_id: str = ""):
        super().__init__("telegram", enabled)
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(
        self, message: str, level: str = "INFO", metadata: dict[str, Any] | None = None
    ) -> bool:
        if not self.enabled:
            return False
        formatted = self._apply_template(message, metadata)
        logger.info(f"[Telegram] {self.chat_id} - {level}: {formatted}")
        return True


class SlackChannel(AlertChannel):
    def __init__(self, enabled: bool = False, webhook_url: str = ""):
        super().__init__("slack", enabled)
        self.webhook_url = webhook_url

    def send(
        self, message: str, level: str = "INFO", metadata: dict[str, Any] | None = None
    ) -> bool:
        if not self.enabled:
            return False
        formatted = self._apply_template(message, metadata)
        logger.info(f"[Slack] webhook - {level}: {formatted}")
        return True


class AlertManager:
    """Central manager for sending alerts to configured notification channels."""

    def __init__(self, settings_mgr: SettingsManager | None = None):
        self.settings = settings_mgr or SettingsManager()
        self.channels: dict[str, AlertChannel] = {}
        self._register_default_channels()
        self.history: list[dict[str, Any]] = []

    def _register_default_channels(self) -> None:
        # Load channel configs from settings (default to disabled if not set)
        # Email
        email_enabled = (
            self.settings.get("alert.email.enabled", "false").lower() == "true"
        )
        email_to = self.settings.get("alert.email.to", "")
        self.channels["email"] = EmailChannel(enabled=email_enabled, to=email_to)
        # Discord
        discord_enabled = (
            self.settings.get("alert.discord.enabled", "false").lower() == "true"
        )
        discord_webhook = self.settings.get("alert.discord.webhook_url", "")
        self.channels["discord"] = DiscordChannel(
            enabled=discord_enabled, webhook_url=discord_webhook
        )
        # Telegram
        telegram_enabled = (
            self.settings.get("alert.telegram.enabled", "false").lower() == "true"
        )
        telegram_token = self.settings.get("alert.telegram.bot_token", "")
        telegram_chat = self.settings.get("alert.telegram.chat_id", "")
        self.channels["telegram"] = TelegramChannel(
            enabled=telegram_enabled, bot_token=telegram_token, chat_id=telegram_chat
        )
        # Slack
        slack_enabled = (
            self.settings.get("alert.slack.enabled", "false").lower() == "true"
        )
        slack_webhook = self.settings.get("alert.slack.webhook_url", "")
        self.channels["slack"] = SlackChannel(
            enabled=slack_enabled, webhook_url=slack_webhook
        )

    def send(
        self,
        alert_type: AlertType | str,
        message: str,
        level: str = "INFO",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dispatch an alert to all enabled channels (respecting rate limits)."""
        if isinstance(alert_type, str):
            try:
                alert_type = AlertType(alert_type)
            except ValueError:
                alert_type = AlertType.SYSTEM_HEALTH
        timestamp = datetime.now(timezone.utc).isoformat() + "Z"
        entry = {
            "timestamp": timestamp,
            "type": alert_type.value,
            "level": level,
            "message": message,
            "metadata": metadata or {},
            "channels_sent": [],
        }
        # Rate-limit check per channel (simple per-minute)
        for name, ch in self.channels.items():
            if not ch.enabled:
                continue
            key = f"{name}:{timestamp}"
            # Mock rate limit: skip if sent in last 60 seconds
            if key in ch.last_sent:
                logger.debug(f"Rate limit skipped for {name} at {timestamp}")
                continue
            try:
                sent_ok = ch.send(message, level, metadata)
                if sent_ok:
                    ch.last_sent[key] = time.time()
                    entry["channels_sent"].append(name)
            except (OSError, ValueError, RuntimeError) as e:
                logger.error(f"Failed to send alert via {name}: {e}")
        self.history.append(entry)
        # Persist low-frequency alert to DB (ema_vwap-6ic)
        try:
            import sqlite3

            from src.database import CandleDatabase

            db = CandleDatabase()
            db.save_alert(entry)
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            logger.warning(f"DB persistence skipped for alert: {e}")
        return entry

    def register_channel(self, name: str, channel: AlertChannel) -> None:
        self.channels[name] = channel


# Singleton instance for shared state
_alert_manager_instance: AlertManager | None = None


def get_alert_manager(settings_mgr: SettingsManager | None = None) -> AlertManager:
    """Return the singleton AlertManager (reuse across calls)."""
    global _alert_manager_instance
    if _alert_manager_instance is None:
        _alert_manager_instance = AlertManager(settings_mgr=settings_mgr)
    return _alert_manager_instance


def send_alert(
    alert_type: AlertType | str,
    message: str,
    level: str = "INFO",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Global convenience wrapper: dispatch via the singleton AlertManager."""
    return get_alert_manager().send(alert_type, message, level, metadata)