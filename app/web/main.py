"""The web process: Telegram webhook, product pages and status page.

Run with `uvicorn --factory app.web.main:build_default_app`.
"""

import hmac
import logging
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.bot.commands import handle_update
from app.bot.limits import CommandLimiter
from app.clock import Clock, SystemClock
from app.config import Settings
from app.models import PricePoint, Product, User
from app.money import format_sen
from app.notify.telegram import LogOnlyTelegram, TelegramBlocked, TelegramClient, TelegramError
from app.web.stats import plugin_health, price_history, product_counts, time_ago

log = logging.getLogger("web")

HERE = Path(__file__).parent
MYT = ZoneInfo("Asia/Kuala_Lumpur")

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
    templates = Jinja2Templates(directory=HERE / "templates")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return templates.TemplateResponse(request, "home.html", {"bot_username": settings.bot_username})

    @app.get("/p/{public_id}", response_class=HTMLResponse)
    def product_page(request: Request, public_id: str):
        with session_factory() as session:
            product = session.scalar(select(Product).where(Product.public_id == public_id))
            if product is None:
                return templates.TemplateResponse(request, "not_found.html", {}, status_code=404)
            history = price_history(session, product)
            points = session.scalars(
                select(PricePoint).where(PricePoint.product_id == product.id).order_by(PricePoint.observed_at)
            ).all()
        now = clock.now()
        price = product.current_price_sen
        return templates.TemplateResponse(
            request,
            "product.html",
            {
                "product": product,
                "title": product.title or product.url,
                "shop_host": (urlsplit(product.url).hostname or "").removeprefix("www."),
                "price_amount": format_sen(price).removeprefix("RM ") if price is not None else None,
                "lowest": format_sen(min(p.price_sen for p in points)) if len(points) > 1 else None,
                "checked_ago": time_ago(product.last_success_at, now) if product.last_success_at else "never",
                "history": history,
                "changes": _changes(points),
            },
        )

    @app.get("/status", response_class=HTMLResponse)
    def status_page(request: Request):
        with session_factory() as session:
            counts = product_counts(session)
            health = plugin_health(session, clock.now())
        return templates.TemplateResponse(
            request, "status.html", {"counts": counts, "health": health, "rate": _rate}
        )

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


def _changes(points: list[PricePoint]) -> list[dict]:
    """Price changes, newest first, with dates in Malaysia time."""
    changes = []
    previous = None
    for p in points:
        if previous is None or p.price_sen == previous:
            direction = "same"
        else:
            direction = "down" if p.price_sen < previous else "up"
        local = p.observed_at.astimezone(MYT)
        changes.append(
            {
                "iso": local.isoformat(),
                "date": f"{local.day} {local:%b %Y}",
                "price": format_sen(p.price_sen),
                "direction": direction,
                "in_stock": p.in_stock,
            }
        )
        previous = p.price_sen
    return list(reversed(changes))


def _rate(ok: int, total: int) -> str:
    return f"{ok} of {total} ({round(100 * ok / total)}%)" if total else "no checks"


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
        telegram=(
            TelegramClient(settings.telegram_bot_token, httpx.Client())
            if settings.telegram_bot_token
            else LogOnlyTelegram()
        ),
        settings=settings,
    )
