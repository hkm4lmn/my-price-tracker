"""Read-only queries and formatting for the public pages."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import CheckResult, PricePoint, Product, ProductStatus


@dataclass(frozen=True)
class PluginHealth:
    shop: str
    ok_24h: int
    total_24h: int
    ok_7d: int
    total_7d: int


def plugin_health(session: Session, now: datetime) -> list[PluginHealth]:
    day = now - timedelta(days=1)
    rows = session.execute(
        select(
            CheckResult.shop,
            func.count().filter(and_(CheckResult.ok, CheckResult.checked_at >= day)),
            func.count().filter(CheckResult.checked_at >= day),
            func.count().filter(CheckResult.ok),
            func.count(),
        )
        .where(CheckResult.checked_at >= now - timedelta(days=7))
        .group_by(CheckResult.shop)
        .order_by(CheckResult.shop)
    ).all()
    return [PluginHealth(*row) for row in rows]


def product_counts(session: Session) -> dict[str, int]:
    counts = dict(session.execute(select(Product.status, func.count()).group_by(Product.status)).all())
    return {s: counts.get(s, 0) for s in (ProductStatus.ACTIVE, ProductStatus.FAILING, ProductStatus.DEAD)}


def price_history(session: Session, product: Product) -> list[dict]:
    """Chart points, oldest first, with the last price carried to the latest check."""
    points = session.scalars(
        select(PricePoint).where(PricePoint.product_id == product.id).order_by(PricePoint.observed_at)
    ).all()
    history = [_point(p.observed_at, p.price_sen, p.in_stock) for p in points]
    if points and product.last_success_at and product.last_success_at > points[-1].observed_at:
        history.append(_point(product.last_success_at, points[-1].price_sen, points[-1].in_stock))
    return history


def _point(when: datetime, price_sen: int, in_stock: bool) -> dict:
    return {"t": when.astimezone(UTC).isoformat(), "price_sen": price_sen, "in_stock": in_stock}


def time_ago(then: datetime, now: datetime) -> str:
    seconds = (now - then).total_seconds()
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = int(seconds // size)
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"
