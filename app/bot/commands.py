"""What the bot does with each message. Returns replies; the webhook sends them."""

import math
from urllib.parse import urlsplit
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.fetching.plugins import plugin_for
from app.models import Product, ProductStatus, User, Watch, WatchAddition, new_public_id
from app.money import MAX_PRICE_SEN, format_sen, parse_ringgit
from app.notify.messages import short_title
from app.urls import InvalidUrl, extract_url, normalize_url

MAX_WATCHES = 20
MAX_NEW_PER_DAY = 10
CHECK_COOLDOWN = timedelta(minutes=60)
# Telegram's limit is 4096 characters per message.
MESSAGE_LIMIT = 4000

# Shops known to block automated price checks (see docs/findings).
_UNSUPPORTED_HOSTS = ("shopee.com.my", "shopee.sg", "shopee.co.id", "shp.ee")
UNSUPPORTED = (
    "Sorry, Shopee can't be tracked: it blocks automated price checks, and this bot "
    "doesn't try to get around that. Links from shops that publish standard product "
    "information work, for example Machines or Senheng."
)

HELP = (
    "Send me a link to a product page and I'll tell you when its price drops.\n\n"
    "/list – what you're tracking\n"
    "/target 2 59.90 – alert me when item 2 is RM 59.90 or less\n"
    "/target 2 off – remove the target\n"
    "/remove 2 – stop tracking item 2\n"
    "/check 2 – check item 2 now (once an hour)\n"
    "/help – this message"
)


@dataclass(frozen=True)
class Reply:
    chat_id: int
    html: str


def handle_update(session: Session, update: dict, *, now: datetime, base_url: str) -> list[Reply]:
    message = update.get("message")
    if not isinstance(message, dict):
        return []
    chat = message.get("chat") or {}
    text = message.get("text")
    # Private chats only: in a group, anyone could add or remove products.
    if not text or chat.get("type") != "private" or "id" not in chat:
        return []

    chat_id = chat["id"]
    user = _upsert_user(session, chat_id)
    ctx = _Context(session, user, now, base_url.rstrip("/"))
    reply = _dispatch(ctx, text.strip())
    session.commit()
    return [Reply(chat_id, part) for part in _split(reply)]


def _split(html: str) -> list[str]:
    """Split a long reply at blank lines so each part fits in one message."""
    parts, current = [], ""
    for block in html.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > MESSAGE_LIMIT and current:
            parts.append(current)
            candidate = block
        current = candidate
    parts.append(current)
    return parts


@dataclass
class _Context:
    session: Session
    user: User
    now: datetime
    base_url: str

    def page(self, product: Product) -> str:
        return f"{self.base_url}/p/{product.public_id}"


def _dispatch(ctx: _Context, text: str) -> str:
    if text.startswith("/"):
        command, *args = text.split()
        command = command.split("@", 1)[0].lower()
        handler = _COMMANDS.get(command)
        return handler(ctx, args) if handler else HELP
    if url := extract_url(text):
        return _add(ctx, url)
    return HELP


def _upsert_user(session: Session, chat_id: int) -> User:
    session.execute(
        insert(User).values(telegram_chat_id=chat_id, is_blocked=False).on_conflict_do_nothing()
    )
    user = session.scalar(select(User).where(User.telegram_chat_id == chat_id))
    # Messaging the bot again means they've unblocked it.
    user.is_blocked = False
    return user


def _add(ctx: _Context, raw_url: str) -> str:
    try:
        url = normalize_url(raw_url)
    except InvalidUrl:
        return "That link doesn't look right. Send a normal http(s) product link."

    host = (urlsplit(url).hostname or "").removeprefix("www.")
    if host.endswith(_UNSUPPORTED_HOSTS):
        return UNSUPPORTED

    session, user = ctx.session, ctx.user
    product = session.scalar(select(Product).where(Product.url == url))
    if product and session.scalar(
        select(Watch).where(Watch.user_id == user.id, Watch.product_id == product.id)
    ):
        return f"You're already tracking this. See /list."

    total = session.scalar(select(func.count()).select_from(Watch).where(Watch.user_id == user.id))
    if total >= MAX_WATCHES:
        return f"You can track up to {MAX_WATCHES} products. Remove one with /remove first."
    recent = session.scalar(
        select(func.count())
        .select_from(WatchAddition)
        .where(WatchAddition.user_id == user.id, WatchAddition.added_at > ctx.now - timedelta(days=1))
    )
    if recent >= MAX_NEW_PER_DAY:
        return f"You've added {MAX_NEW_PER_DAY} products in the last 24 hours. Try again tomorrow."

    if product is None:
        # Two users can add the same new link at the same moment; the unique
        # URL constraint decides, and both end up with the same product.
        session.execute(
            insert(Product)
            .values(
                url=url,
                shop=plugin_for(url).name,
                public_id=new_public_id(),
                next_check_at=ctx.now,
                created_at=ctx.now,
            )
            .on_conflict_do_nothing(index_elements=["url"])
        )
        product = session.scalar(select(Product).where(Product.url == url))

    has_fresh_price = product.status != ProductStatus.DEAD and product.current_price_sen is not None
    if product.status == ProductStatus.DEAD:
        _revive(product, ctx.now)

    session.add(WatchAddition(user_id=user.id, added_at=ctx.now))
    session.add(
        Watch(
            user_id=user.id,
            product_id=product.id,
            created_at=ctx.now,
            baseline_price_sen=product.current_price_sen if has_fresh_price else None,
        )
    )
    if has_fresh_price:
        return (
            f"✅ Now tracking <b>{_title(product)}</b>. "
            f"Current price: {format_sen(product.current_price_sen)}.\n{ctx.page(product)}"
        )
    return "Got it! Checking the price now… I'll message you when it's done."


def _revive(product: Product, now: datetime) -> None:
    product.status = ProductStatus.ACTIVE
    product.consecutive_failures = 0
    product.last_error = None
    product.next_check_at = now


def _list(ctx: _Context, args: list[str]) -> str:
    rows = _watches(ctx)
    if not rows:
        return "You're not tracking anything yet. Send me a product link to start."
    lines = []
    for n, (watch, product) in enumerate(rows, start=1):
        price = format_sen(product.current_price_sen) if product.current_price_sen is not None else "checking…"
        notes = []
        if watch.target_price_sen is not None:
            notes.append(f"target {format_sen(watch.target_price_sen)}")
        if product.in_stock is False:
            notes.append("out of stock")
        if product.status == ProductStatus.FAILING:
            notes.append("⚠️ can't read price right now")
        if product.status == ProductStatus.DEAD:
            notes.append("❌ stopped; send the link again")
        extra = f" ({', '.join(notes)})" if notes else ""
        lines.append(f"{n}. <b>{_title(product)}</b> – {price}{extra}\n{ctx.page(product)}")
    return "\n\n".join(lines)


def _target(ctx: _Context, args: list[str]) -> str:
    usage = "Use /target 2 59.90 (or /target 2 off). Numbers are from /list."
    if len(args) != 2:
        return usage
    picked = _pick(ctx, args[0])
    if isinstance(picked, str):
        return picked
    watch, product = picked
    if args[1].lower() == "off":
        watch.target_price_sen = None
        return f"Target removed for <b>{_title(product)}</b>. You'll get alerts on any price drop."
    try:
        target = parse_ringgit(args[1])
    except ValueError:
        return usage
    if not 0 < target <= MAX_PRICE_SEN:
        return usage
    watch.target_price_sen = target
    watch.target_armed = True
    return f"🎯 I'll tell you when <b>{_title(product)}</b> is {format_sen(target)} or less."


def _remove(ctx: _Context, args: list[str]) -> str:
    if len(args) != 1:
        return "Use /remove 2. Numbers are from /list."
    picked = _pick(ctx, args[0])
    if isinstance(picked, str):
        return picked
    watch, product = picked
    ctx.session.delete(watch)
    return f"Stopped tracking <b>{_title(product)}</b>."


def _check(ctx: _Context, args: list[str]) -> str:
    if len(args) != 1:
        return "Use /check 2. Numbers are from /list."
    picked = _pick(ctx, args[0])
    if isinstance(picked, str):
        return picked
    _, product = picked
    last = product.last_manual_check_at
    if last is not None and ctx.now - last < CHECK_COOLDOWN:
        minutes = math.ceil((last + CHECK_COOLDOWN - ctx.now).total_seconds() / 60)
        return f"This was checked recently. You can ask again in {minutes} minute{'s' if minutes != 1 else ''}."
    if product.status == ProductStatus.DEAD:
        _revive(product, ctx.now)
    product.next_check_at = ctx.now
    product.last_manual_check_at = ctx.now
    return f"Checking <b>{_title(product)}</b> now. I'll message you if anything changed."


def _help(ctx: _Context, args: list[str]) -> str:
    return HELP


def _watches(ctx: _Context) -> list[tuple[Watch, Product]]:
    return ctx.session.execute(
        select(Watch, Product)
        .join(Product, Product.id == Watch.product_id)
        .where(Watch.user_id == ctx.user.id)
        .order_by(Watch.created_at, Watch.id)
    ).all()


def _pick(ctx: _Context, number: str) -> tuple[Watch, Product] | str:
    rows = _watches(ctx)
    if not number.isdigit() or not 1 <= int(number) <= len(rows):
        return f"There's no item {escape(number)}. Use /list to see your numbers."
    return tuple(rows[int(number) - 1])


def _title(product: Product) -> str:
    return escape(short_title(product.title or product.url), quote=False)


_COMMANDS = {
    "/start": _help,
    "/help": _help,
    "/list": _list,
    "/target": _target,
    "/remove": _remove,
    "/check": _check,
}
