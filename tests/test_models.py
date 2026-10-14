import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.models import PricePoint, Product, User, Watch, new_public_id

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def product(url="https://shop.my/a", **kw):
    return Product(url=url, shop="schema_org", next_check_at=NOW, **kw)


def test_migration_creates_tables(engine):
    names = set(inspect(engine).get_table_names())
    assert {
        "users",
        "products",
        "watches",
        "price_points",
        "notifications",
        "check_results",
    } <= names


def test_product_url_unique(session):
    session.add_all([product(), product()])
    with pytest.raises(IntegrityError):
        session.commit()


def test_watch_unique_per_user_and_product(session):
    user = User(telegram_chat_id=1)
    p = product()
    session.add_all([user, p])
    session.flush()
    session.add_all(
        [Watch(user_id=user.id, product_id=p.id), Watch(user_id=user.id, product_id=p.id)]
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_negative_price_rejected(session):
    p = product()
    session.add(p)
    session.flush()
    session.add(PricePoint(product_id=p.id, price_sen=-1, in_stock=True, observed_at=NOW))
    with pytest.raises(IntegrityError):
        session.commit()


def test_product_gets_public_id_and_defaults(session):
    p = product()
    session.add(p)
    session.commit()
    assert re.fullmatch(r"[a-z0-9]{8}", p.public_id)
    assert p.status == "active"
    assert p.consecutive_failures == 0


def test_public_id_shape():
    ids = {new_public_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(re.fullmatch(r"[a-z0-9]{8}", i) for i in ids)
