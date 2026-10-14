"""The web process: Telegram webhook, product pages and status page.

Run with `uvicorn --factory app.web.main:build_default_app`.
"""

import hmac
import logging

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.bot.commands import handle_update
from app.bot.limits import CommandLimiter
from app.clock import Clock, SystemClock
from app.config import Settings
from app.models import User
from app.notify.telegram import TelegramBlocked, TelegramClient, TelegramError

log = logging.getLogger("web")

SLOW_DOWN = "Slow down a little: you're sending commands too fast. Try again in a minute."


def create_app(
    *,
    session_factory: sessionmaker[Session],
    telegram: TelegramClient,
    settings: Settings,
    limiter: CommandLimiter | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    app = FastAPI(title="MY Price Tracker", docs_url=None, redoc_url=None, openapi_url=None)
    limiter = limiter or CommandLimiter()
    clock = clock or SystemClock()

    @app.post("/telegram/webhook")
    def telegram_webhook(
        update: dict,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ):
        expected = settings.telegram_webhook_secret
        given = x_telegram_bot_api_secret_token or ""
        if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
            raise HTTPException(status_code=403)

        # Always answer 200 from here on: Telegram retries anything else, and
        # a bad update would be retried forever.
        chat_id = ((update.get("message") or {}).get("chat") or {}).get("id")
        if chat_id is not None and not limiter.allow(chat_id):
            if limiter.should_warn(chat_id):
                _send(session_factory, telegram, chat_id, SLOW_DOWN)
            return {"ok": True}

        try:
            with session_factory() as session:
                replies = handle_update(session, update, now=clock.now(), base_url=settings.public_base_url)
        except Exception:
            log.exception("failed to handle update %s", update.get("update_id"))
            return {"ok": True}
        for reply in replies:
            _send(session_factory, telegram, reply.chat_id, reply.html)
        return {"ok": True}

    @app.get("/healthz")
    def healthz():
        try:
            with session_factory() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            raise HTTPException(status_code=503, detail="database unavailable")
        return {"status": "ok"}

    return app


def _send(session_factory, telegram: TelegramClient, chat_id: int, html: str) -> None:
    try:
        telegram.send_message(chat_id, html)
    except TelegramBlocked:
        with session_factory() as session:
            user = session.scalar(select(User).where(User.telegram_chat_id == chat_id))
            if user is not None:
                user.is_blocked = True
                session.commit()
    except TelegramError:
        log.warning("could not send reply to chat %s", chat_id, exc_info=True)


def build_default_app() -> FastAPI:
    """App wired to the real database and Telegram, from environment settings."""
    from app.db import make_engine, make_session_factory

    settings = Settings()
    return create_app(
        session_factory=make_session_factory(make_engine(settings.database_url)),
        telegram=TelegramClient(settings.telegram_bot_token, httpx.Client()),
        settings=settings,
    )
