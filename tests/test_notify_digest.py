"""Digest notifier tests: email over SMTP, fan-out, and channel selection."""

from __future__ import annotations

from stock_monitor.config import Settings


def test_email_notifier_sends_over_smtp(monkeypatch) -> None:
    from stock_monitor.notify import EmailNotifier

    sent: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=0):
            sent["host"], sent["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent["tls"] = True

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, msg):
            sent["subject"] = msg["Subject"]
            sent["to"] = msg["To"]
            sent["body"] = msg.get_content()

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    notifier = EmailNotifier(
        host="smtp.example.com", port=587, user="u", password="p",
        sender="from@example.com", recipients=["a@example.com", "b@example.com"],
    )
    assert notifier.send("Subject line", "Body text") is True
    assert sent["subject"] == "Subject line"
    assert sent["to"] == "a@example.com, b@example.com"
    assert sent["tls"] is True
    assert sent["login"] == ("u", "p")


def test_email_notifier_without_recipients_returns_false() -> None:
    from stock_monitor.notify import EmailNotifier

    notifier = EmailNotifier(
        host="smtp.example.com", port=587, user="", password="",
        sender="from@example.com", recipients=[],
    )
    assert notifier.send("t", "b") is False


def test_multi_notifier_fans_out() -> None:
    from stock_monitor.notify import MultiNotifier, Notifier

    class Capture(Notifier):
        name = "capture"

        def __init__(self) -> None:
            self.messages: list[tuple[str, str]] = []

        def send(self, title: str, body: str) -> bool:
            self.messages.append((title, body))
            return True

    a, b = Capture(), Capture()
    assert MultiNotifier([a, b]).send("hi", "there") is True
    assert a.messages and b.messages


def test_get_email_notifier_none_when_unset() -> None:
    from stock_monitor.notify import get_email_notifier

    assert get_email_notifier(Settings(smtp_host="", email_to="")) is None


def test_get_email_notifier_parses_recipients() -> None:
    from stock_monitor.notify import get_email_notifier

    notifier = get_email_notifier(
        Settings(smtp_host="smtp.x", email_to="a@x.com, b@x.com ")
    )
    assert notifier is not None
    assert notifier._recipients == ["a@x.com", "b@x.com"]


def test_get_digest_notifiers_falls_back_to_logging() -> None:
    from stock_monitor.notify import get_digest_notifiers

    notifier = get_digest_notifiers(Settings(telegram_bot_token="", smtp_host=""))
    assert notifier.name == "multi"
    assert notifier.send("t", "b") is True
