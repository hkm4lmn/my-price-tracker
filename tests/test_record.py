import random
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.fetching.errors import Blocked, ParseError
from app.fetching.snapshot import Snapshot
from app.models import CheckResult, Notification, PricePoint, Product, Watch
from app.tracking.record import record_failure, record_success
from tests.factories import NOW, make_product, make_user, make_watch

RNG = random.Random(1)


def snap(price, in_stock=True, title="Kettle"):
    return Snapshot(title=title, price_sen=price, in_stock=in_stock, currency="MYR", image_url=None)


def count(session, model, **filters):
    return session.scalar(select(func.count()).select_from(model).filter_by(**filters))


def notifications(session):
    return session.scalars(select(Notification).order_by(Notification.id)).all()


@pytest.fixture
def setup(session):
    user = make_user(session)
    product = make_product(session)
    watch = make_watch(session, user, product)
    return user, product, watch


def refresh(session, *objs):
    session.expire_all()
    return [session.get(type(o), o.id) for o in objs]


def test_record_success_first_check_writes_point_and_tracking_started(session, setup):
    user, product, watch = setup
    record_success(session, product.id, snap(8990), NOW, RNG)
    product, watch = refresh(session, product, watch)

    assert (product.current_price_sen, product.in_stock, product.title) == (8990, True, "Kettle")
    assert product.last_success_at == NOW
    assert NOW + timedelta(hours=4, minutes=48) <= product.next_check_at <= NOW + timedelta(hours=7, minutes=12)
    assert watch.baseline_price_sen == 8990
    assert count(session, PricePoint, product_id=product.id) == 1
    (n,) = notifications(session)
    assert (n.user_id, n.kind) == (user.id, "tracking_started")
    assert n.payload["product_title"] == "Kettle"
    assert n.payload["public_id"] == product.public_id
    assert n.next_attempt_at == NOW and n.sent_at is None
    assert count(session, CheckResult, ok=True) == 1


def test_record_success_unchanged_price_writes_no_point(session, setup):
    _, product, _ = setup
    record_success(session, product.id, snap(8990), NOW, RNG)
    record_success(session, product.id, snap(8990), NOW + timedelta(hours=6), RNG)
    assert count(session, PricePoint, product_id=product.id) == 1
    (product,) = refresh(session, product)
    assert product.last_success_at == NOW + timedelta(hours=6)


def test_record_success_stock_change_writes_point(session, setup):
    _, product, _ = setup
    record_success(session, product.id, snap(8990), NOW, RNG)
    record_success(session, product.id, snap(8990, in_stock=False), NOW + timedelta(hours=6), RNG)
    assert count(session, PricePoint, product_id=product.id) == 2


def test_record_success_drop_enqueues_price_drop(session, setup):
    _, product, _ = setup
    record_success(session, product.id, snap(10000), NOW, RNG)
    record_success(session, product.id, snap(9000), NOW + timedelta(hours=6), RNG)
    kinds = [n.kind for n in notifications(session)]
    assert kinds == ["tracking_started", "price_drop"]
    assert notifications(session)[1].payload["new_price_sen"] == 9000


def test_record_success_drop_enqueues_price_drop_in_same_transaction(session, setup):
    _, product, _ = setup
    record_success(session, product.id, snap(10000), NOW, RNG)

    with patch("app.tracking.record.enqueue", side_effect=RuntimeError("outbox down")):
        with pytest.raises(RuntimeError):
            record_success(session, product.id, snap(9000), NOW + timedelta(hours=6), RNG)

    (product,) = refresh(session, product)
    assert product.current_price_sen == 10000
    assert count(session, PricePoint, product_id=product.id) == 1


def test_record_success_skips_blocked_users(session):
    blocked = make_user(session, is_blocked=True)
    product = make_product(session)
    make_watch(session, blocked, product)
    record_success(session, product.id, snap(100), NOW, RNG)
    assert notifications(session) == []


def test_record_success_keeps_price_when_snapshot_price_missing(session, setup):
    _, product, _ = setup
    record_success(session, product.id, snap(10000), NOW, RNG)
    record_success(session, product.id, snap(None, in_stock=False), NOW + timedelta(hours=6), RNG)
    (product,) = refresh(session, product)
    assert (product.current_price_sen, product.in_stock) == (10000, False)
    latest = session.scalars(select(PricePoint).order_by(PricePoint.id.desc())).first()
    assert (latest.price_sen, latest.in_stock) == (10000, False)


def test_record_success_without_any_price_is_a_failure(session, setup):
    _, product, _ = setup
    record_success(session, product.id, snap(None, in_stock=False), NOW, RNG)
    (product,) = refresh(session, product)
    assert product.consecutive_failures == 1
    assert product.last_error.startswith("parse_error")
    assert count(session, PricePoint) == 0


def test_record_failure_backoff_and_failing_once(session, setup):
    _, product, _ = setup
    delays = []
    t = NOW
    for _ in range(4):
        record_failure(session, product.id, Blocked("HTTP 403"), t)
        (product,) = refresh(session, product)
        delays.append(product.next_check_at - t)
        t = product.next_check_at

    assert delays == [timedelta(minutes=30), timedelta(hours=2), timedelta(hours=6), timedelta(hours=24)]
    assert product.status == "failing"
    assert product.last_error == "blocked: HTTP 403"
    assert [n.kind for n in notifications(session)] == ["failing"]
    assert count(session, CheckResult, ok=False, error_kind="blocked") == 4


def test_record_failure_marks_dead_after_7_days(session, setup):
    _, product, _ = setup
    record_success(session, product.id, snap(100), NOW, RNG)
    t = NOW + timedelta(days=1)
    while True:
        record_failure(session, product.id, ParseError("layout changed"), t)
        (product,) = refresh(session, product)
        if product.status == "dead":
            break
        t = product.next_check_at
    assert t - NOW > timedelta(days=7)
    assert t - NOW <= timedelta(days=8)
    kinds = [n.kind for n in notifications(session)]
    assert kinds == ["tracking_started", "failing", "dead"]


def test_success_after_failing_resets_to_active(session, setup):
    _, product, _ = setup
    for _ in range(3):
        record_failure(session, product.id, Blocked("x"), NOW)
    record_success(session, product.id, snap(500), NOW + timedelta(hours=1), RNG)
    (product,) = refresh(session, product)
    assert (product.status, product.consecutive_failures, product.last_error) == ("active", 0, None)


def test_watch_state_saved(session, setup):
    _, product, watch = setup
    session.get(Watch, watch.id).target_price_sen = 5000
    session.commit()
    record_success(session, product.id, snap(10000), NOW, RNG)
    record_success(session, product.id, snap(4000), NOW + timedelta(hours=6), RNG)
    (watch,) = refresh(session, watch)
    assert watch.target_armed is False
    assert [n.kind for n in notifications(session)] == ["tracking_started", "target_reached"]
