"""Database tables.

Money is stored as integer sen. Times are timezone-aware UTC.
"""

import secrets
import string
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

_PUBLIC_ID_ALPHABET = string.ascii_lowercase + string.digits


def new_public_id() -> str:
    """Random slug for /p/{public_id}, so pages can't be found by counting ids."""
    return "".join(secrets.choice(_PUBLIC_ID_ALPHABET) for _ in range(8))


class ProductStatus:
    ACTIVE = "active"
    FAILING = "failing"
    DEAD = "dead"


class NotificationKind:
    PRICE_DROP = "price_drop"
    TARGET_REACHED = "target_reached"
    BACK_IN_STOCK = "back_in_stock"
    FAILING = "failing"
    DEAD = "dead"
    TRACKING_STARTED = "tracking_started"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set when Telegram says the user blocked the bot; we stop messaging them.
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Product(Base):
    """One row per normalised product URL, shared by everyone watching it."""

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("current_price_sen >= 0", name="ck_products_price_nonneg"),
        CheckConstraint(
            "status IN ('active', 'failing', 'dead')", name="ck_products_status"
        ),
        Index("ix_products_next_check_at", "next_check_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    shop: Mapped[str] = mapped_column(String(50))
    public_id: Mapped[str] = mapped_column(String(8), unique=True, default=new_public_id)
    title: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    current_price_sen: Mapped[int | None] = mapped_column(Integer)
    in_stock: Mapped[bool | None] = mapped_column(Boolean)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_manual_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(10), default=ProductStatus.ACTIVE, server_default=ProductStatus.ACTIVE
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Watch(Base):
    __tablename__ = "watches"
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_watches_user_product"),
        CheckConstraint("target_price_sen > 0", name="ck_watches_target_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    target_price_sen: Mapped[int | None] = mapped_column(Integer)
    # Price when the watch started; null until the product's first good check.
    baseline_price_sen: Mapped[int | None] = mapped_column(Integer)
    last_alerted_price_sen: Mapped[int | None] = mapped_column(Integer)
    # The target alert fires once, then re-arms when the price goes back above.
    target_armed: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PricePoint(Base):
    """Written only when price or stock changes, so the table stays small."""

    __tablename__ = "price_points"
    __table_args__ = (
        CheckConstraint("price_sen >= 0", name="ck_price_points_price_nonneg"),
        Index("ix_price_points_product_observed", "product_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    price_sen: Mapped[int] = mapped_column(Integer)
    in_stock: Mapped[bool] = mapped_column(Boolean)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    """Transactional outbox: alerts are written with the price change, sent later."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index(
            "ix_notifications_pending",
            "next_attempt_at",
            postgresql_where=text("sent_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Null once delivery has been abandoned (kept for inspection).
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class CheckResult(Base):
    """One row per fetch attempt, for the /status page. Pruned after 8 days."""

    __tablename__ = "check_results"
    __table_args__ = (Index("ix_check_results_checked_at", "checked_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop: Mapped[str] = mapped_column(String(50))
    ok: Mapped[bool] = mapped_column(Boolean)
    error_kind: Mapped[str | None] = mapped_column(String(30))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
