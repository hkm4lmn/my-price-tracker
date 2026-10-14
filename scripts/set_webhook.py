"""Tell Telegram where to deliver messages for the bot.

Run once after deploying, and again if the domain changes:
    docker compose -f deploy/docker-compose.prod.yml --env-file .env run --rm web python -m scripts.set_webhook
"""

import httpx

from app.config import Settings
from app.notify.telegram import TelegramClient


def register_webhook(telegram: TelegramClient, settings: Settings) -> str:
    if not settings.public_base_url.startswith("https://"):
        raise SystemExit("PUBLIC_BASE_URL must start with https:// (Telegram only calls HTTPS webhooks)")
    if not settings.telegram_webhook_secret:
        raise SystemExit("TELEGRAM_WEBHOOK_SECRET is not set")
    url = f"{settings.public_base_url.rstrip('/')}/telegram/webhook"
    telegram.set_webhook(url, settings.telegram_webhook_secret)
    return url


def main() -> None:
    settings = Settings()
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    url = register_webhook(TelegramClient(settings.telegram_bot_token, httpx.Client()), settings)
    print(f"Webhook set to {url}")


if __name__ == "__main__":
    main()
