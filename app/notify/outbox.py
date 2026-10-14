"""Transactional outbox for Telegram alerts.

Alerts are written to the notifications table in the same transaction as the
price change that caused them, so an alert can't be lost (price saved, alert
not) or invented (alert saved, price rolled back). A sender delivers them later.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Notification


def enqueue(session: Session, *, user_id: int, kind: str, payload: dict, now: datetime) -> None:
    """Add an alert to the outbox. The caller's transaction commits it."""
    session.add(
        Notification(user_id=user_id, kind=kind, payload=payload, created_at=now, next_attempt_at=now)
    )
