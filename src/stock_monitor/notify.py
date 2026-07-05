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


class EmailNotifier(Notifier):
    """Delivers alerts/digests to one or more inboxes over SMTP (STARTTLS)."""

    name = "email"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        sender: str,
        recipients: list[str],
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._sender = sender or user
        self._recipients = recipients

    def send(self, title: str, body: str) -> bool:
        import smtplib
        from email.message import EmailMessage

        if not self._recipients:
            logger.warning("email notifier has no recipients; dropping '%s'", title)
            return False
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = self._sender
        msg["To"] = ", ".join(self._recipients)
        msg.set_content(body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=20) as smtp:
                smtp.starttls()
                if self._user and self._password:
                    smtp.login(self._user, self._password)
                smtp.send_message(msg)
            return True
        except Exception:  # noqa: BLE001 — a failed digest must never crash a job
            logger.exception("email delivery failed")
            return False


class MultiNotifier(Notifier):
    """Fan-out notifier: delivers to every configured transport (best-effort)."""

    name = "multi"

    def __init__(self, notifiers: list[Notifier]) -> None:
        self._notifiers = notifiers

    def send(self, title: str, body: str) -> bool:
        results = [n.send(title, body) for n in self._notifiers]
        return any(results)


def get_email_notifier(settings: Settings) -> EmailNotifier | None:
    """Return an EmailNotifier if SMTP is configured, else None."""
    if not settings.smtp_host or not settings.email_to:
        return None
    recipients = [r.strip() for r in settings.email_to.split(",") if r.strip()]
    return EmailNotifier(
        host=settings.smtp_host,
        port=settings.smtp_port,
        user=settings.smtp_user,
        password=settings.smtp_password,
        sender=settings.email_from,
        recipients=recipients,
    )


def get_digest_notifiers(settings: Settings) -> Notifier:
    """Return a notifier that fans out to all configured digest transports.

    Telegram + email if configured; falls back to logging so a digest is always
    delivered *somewhere* visible even with zero secrets.
    """
    channels: list[Notifier] = []
    if settings.telegram_bot_token and settings.telegram_chat_id:
        channels.append(
            TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        )
    email = get_email_notifier(settings)
    if email is not None:
        channels.append(email)
    if not channels:
        channels.append(LoggingNotifier())
    return MultiNotifier(channels)
