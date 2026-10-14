"""Test doubles for things outside our control: Telegram and the clock."""

import threading
from datetime import datetime, timedelta

from app.notify.telegram import TelegramBlocked, TelegramError


class FakeTelegram:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.webhook: tuple[str, str] | None = None
        self._failures_left = 0
        self._blocked: set[int] = set()
        self._lock = threading.Lock()

    def fail_next(self, n: int) -> None:
        self._failures_left = n

    def block(self, chat_id: int) -> None:
        self._blocked.add(chat_id)

    def send_message(self, chat_id: int, html: str) -> None:
        with self._lock:
            if chat_id in self._blocked:
                raise TelegramBlocked("Forbidden: bot was blocked by the user")
            if self._failures_left:
                self._failures_left -= 1
                raise TelegramError("Bad Gateway")
            self.sent.append((chat_id, html))

    def set_webhook(self, url: str, secret: str) -> None:
        self.webhook = (url, secret)


class FakeClock:
    def __init__(self, now: datetime):
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta
