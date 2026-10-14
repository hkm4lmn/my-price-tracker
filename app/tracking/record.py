"""Saving the result of one price check.

Each function is one transaction: the product update, its price history,
every watcher's alert state and the alerts themselves commit together or not
at all.
"""

import random
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fetching.errors import FetchError, ParseError
from app.fetching.snapshot import Snapshot
from app.models import CheckResult, NotificationKind, PricePoint, Product, ProductStatus, User, Watch
from app.notify.outbox import enqueue
from app.tracking.alerts import WatchState, evaluate_watch
from app.tracking.schedule import backoff_delay, next_check_after_success

FAILING_AFTER = 3
DEAD_AFTER = timedelta(days=7)


def record_success(
    session: Session, product_id: int, snapshot: Snapshot, now: datetime, rng: random.Random
) -> None:
    try:
        product = session.get(Product, product_id, with_for_update=True)
        price = snapshot.price_sen if snapshot.price_sen is not None else product.current_price_sen
        if price is None:
            # Out of stock with no price shown, and we've never seen one.
            session.rollback()
            record_failure(session, product_id, ParseError("no price shown yet"), now)
            return

        prev_price, prev_in_stock = product.current_price_sen, product.in_stock
        if product.last_success_at is None or (price, snapshot.in_stock) != (prev_price, prev_in_stock):
            session.add(
                PricePoint(product_id=product.id, price_sen=price, in_stock=snapshot.in_stock, observed_at=now)
            )

        product.title = snapshot.title
        product.image_url = snapshot.image_url or product.image_url
        product.current_price_sen = price
        product.in_stock = snapshot.in_stock
        product.last_checked_at = now
        product.last_success_at = now
        product.status = ProductStatus.ACTIVE
        product.consecutive_failures = 0
        product.last_error = None
        product.next_check_at = next_check_after_success(now, rng)

        for watch in _active_watches(session, product.id):
            state, drafts = evaluate_watch(
                WatchState(
                    target_price_sen=watch.target_price_sen,
                    baseline_price_sen=watch.baseline_price_sen,
                    last_alerted_price_sen=watch.last_alerted_price_sen,
                    target_armed=watch.target_armed,
                ),
                prev_price_sen=prev_price,
                prev_in_stock=prev_in_stock,
                price_sen=price,
                in_stock=snapshot.in_stock,
            )
            watch.baseline_price_sen = state.baseline_price_sen
            watch.last_alerted_price_sen = state.last_alerted_price_sen
            watch.target_armed = state.target_armed
            for draft in drafts:
                enqueue(
                    session,
                    user_id=watch.user_id,
                    kind=draft.kind,
                    payload={**draft.payload, **_product_payload(product)},
                    now=now,
                )

        session.add(CheckResult(shop=product.shop, ok=True, checked_at=now))
        session.commit()
    except Exception:
        session.rollback()
        raise


def record_failure(session: Session, product_id: int, error: FetchError, now: datetime) -> None:
    try:
        product = session.get(Product, product_id, with_for_update=True)
        product.consecutive_failures += 1
        product.last_checked_at = now
        product.last_error = f"{error.kind}: {str(error)[:200]}"
        product.next_check_at = now + backoff_delay(product.consecutive_failures)

        if product.status == ProductStatus.ACTIVE and product.consecutive_failures >= FAILING_AFTER:
            product.status = ProductStatus.FAILING
            _notify_watchers(session, product, NotificationKind.FAILING, now)

        last_good = product.last_success_at or product.created_at
        if product.status == ProductStatus.FAILING and now - last_good > DEAD_AFTER:
            product.status = ProductStatus.DEAD
            _notify_watchers(session, product, NotificationKind.DEAD, now)

        session.add(CheckResult(shop=product.shop, ok=False, error_kind=error.kind, checked_at=now))
        session.commit()
    except Exception:
        session.rollback()
        raise


def _active_watches(session: Session, product_id: int) -> list[Watch]:
    """Watches on this product, skipping users who blocked the bot."""
    return session.scalars(
        select(Watch)
        .join(User, User.id == Watch.user_id)
        .where(Watch.product_id == product_id, User.is_blocked.is_(False))
        .order_by(Watch.id)
        .with_for_update(of=Watch)
    ).all()


def _notify_watchers(session: Session, product: Product, kind: str, now: datetime) -> None:
    for watch in _active_watches(session, product.id):
        enqueue(session, user_id=watch.user_id, kind=kind, payload=_product_payload(product), now=now)


def _product_payload(product: Product) -> dict:
    return {
        "product_title": product.title or product.url,
        "public_id": product.public_id,
        "url": product.url,
    }
