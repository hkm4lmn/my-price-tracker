"""A small client for the two Telegram Bot API calls the app uses."""

import logging

import httpx

log = logging.getLogger("telegram")


class TelegramError(Exception):
    """Sending failed; worth retrying later."""


class TelegramBlocked(TelegramError):
    """The user blocked the bot or the chat no longer exists; don't retry."""


class TelegramClient:
    def __init__(self, token: str, http: httpx.Client):
        self._base = f"https://api.telegram.org/bot{token}"
        self._http = http

    def send_message(self, chat_id: int, html: str) -> None:
        self._call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": html,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
        )

    def set_webhook(self, url: str, secret: str) -> None:
        self._call("setWebhook", {"url": url, "secret_token": secret, "allowed_updates": ["message"]})

    def _call(self, method: str, body: dict) -> None:
        try:
            response = self._http.post(f"{self._base}/{method}", json=body, timeout=10.0)
        except httpx.HTTPError as e:
            # The URL contains the bot token, so don't include the exception text.
            raise TelegramError(f"{method}: {type(e).__name__}") from None
        if response.status_code == 200:
            return
        try:
            description = response.json().get("description", "")
        except ValueError:
            description = response.text[:200]
        if response.status_code == 403 or "chat not found" in description.lower():
            raise TelegramBlocked(description)
        raise TelegramError(f"{method}: HTTP {response.status_code} {description}".strip())


class LogOnlyTelegram:
    """Stand-in for local development without a bot token: logs instead of sending."""

    def send_message(self, chat_id: int, html: str) -> None:
        log.info("would send to %s: %s", chat_id, html)

    def set_webhook(self, url: str, secret: str) -> None:
        log.info("would set webhook to %s", url)
