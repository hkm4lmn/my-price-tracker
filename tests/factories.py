"""Small helpers that insert rows for tests. Each one commits."""

import itertools
from datetime import UTC, datetime

from app.models import Product, User, Watch

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
_seq = itertools.count(1)


def make_user(session, chat_id=None, **overrides) -> User:
    user = User(telegram_chat_id=chat_id if chat_id is not None else 1000 + next(_seq), **overrides)
    session.add(user)
    session.commit()
    return user


def make_product(session, **overrides) -> Product:
    n = next(_seq)
    fields = dict(
        url=f"https://shop.my/p{n}",
        shop="schema_org",
        title=f"Product {n}",
        next_check_at=NOW,
        created_at=NOW,
    )
    fields.update(overrides)
    product = Product(**fields)
    session.add(product)
    session.commit()
    return product


def make_watch(session, user, product, **overrides) -> Watch:
    fields = dict(created_at=NOW)
    fields.update(overrides)
    watch = Watch(user_id=user.id, product_id=product.id, **fields)
    session.add(watch)
    session.commit()
    return watch
