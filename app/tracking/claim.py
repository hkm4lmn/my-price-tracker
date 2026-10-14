"""Handing out due price checks to workers.

Several workers can run this at once. FOR UPDATE SKIP LOCKED makes each one
skip rows another worker is claiming, and pushing next_check_at forward acts
as a lease: the transaction commits immediately, so no lock is held while a
slow shop responds, and if a worker crashes the product becomes due again
once the lease runs out.
"""

from datetime import datetime, timedelta

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from app.models import Product, ProductStatus, Watch


def claim_due(
    session: Session,
    now: datetime,
    *,
    limit: int = 10,
    lease: timedelta = timedelta(minutes=10),
) -> list[int]:
    ids = session.scalars(
        select(Product.id)
        .where(
            Product.status != ProductStatus.DEAD,
            Product.next_check_at <= now,
            # Nobody watching it any more: stop spending requests on it.
            exists().where(Watch.product_id == Product.id),
        )
        .order_by(Product.next_check_at, Product.id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=Product)
    ).all()
    if ids:
        session.execute(
            update(Product).where(Product.id.in_(ids)).values(next_check_at=now + lease)
        )
    session.commit()
    return list(ids)
