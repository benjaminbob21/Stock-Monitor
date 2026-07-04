"""Alerts behind a swappable notifier interface (build-plan §7 Phase 3).

Same seam idea as the data providers: the engine emits alerts through a `Notifier`,
so the transport (Telegram, email, a log line) swaps without touching the logic.
Telegram activates when a bot token + chat id are configured; otherwise we fall back
to a logging notifier so alerts still *work* (visibly) with zero secrets.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from stock_monitor.config import Settings

logger = logging.getLogger("stock_monitor.alerts")


class Notifier(ABC):
    """Sends a short alert somewhere a human will see it."""

    name: str

    @abstractmethod
    def send(self, title: str, body: str) -> bool:
        """Return True if the alert was delivered."""
        raise NotImplementedError


class LoggingNotifier(Notifier):
    """Default notifier — logs the alert. Always available, no secrets."""

    name = "logging"

    def send(self, title: str, body: str) -> bool:
        logger.warning("ALERT — %s\n%s", title, body)
        return True


class TelegramNotifier(Notifier):
    """Delivers alerts to a Telegram chat via the Bot API."""

    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id

    def send(self, title: str, body: str) -> bool:
        import requests

        try:
            resp = requests.post(
                self._url,
                json={
                    "chat_id": self._chat_id,
                    "text": f"*{title}*\n{body}",
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            return bool(resp.ok)
        except Exception:  # noqa: BLE001 — a failed alert must never crash a scan
            logger.exception("Telegram alert failed")
            return False


def get_notifier(settings: Settings) -> Notifier:
    """Return the configured notifier (Telegram if set, else logging)."""
    if settings.telegram_bot_token and settings.telegram_chat_id:
        return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    return LoggingNotifier()
