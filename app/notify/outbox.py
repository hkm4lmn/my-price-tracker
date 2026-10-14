"""Transactional outbox for Telegram alerts.

Alerts are written to the notifications table in the same transaction as the
price change that caused them, so an alert can't be lost (price saved, alert
not) or invented (alert saved, price rolled back). A sender delivers them later.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Notification, User
from app.notify.messages import render
from app.notify.telegram import TelegramBlocked, TelegramClient, TelegramError


def enqueue(session: Session, *, user_id: int, kind: str, payload: dict, now: datetime) -> None:
    """Add an alert to the outbox. The caller's transaction commits it."""
    session.add(
        Notification(user_id=user_id, kind=kind, payload=payload, created_at=now, next_attempt_at=now)
    )


MAX_ATTEMPTS = 10
_MAX_WAIT = timedelta(hours=2)


def send_pending(
    session_factory: sessionmaker[Session],
    client: TelegramClient,
    now: datetime,
    *,
    base_url: str,
    limit: int = 50,
) -> int:
    """Deliver due notifications, one transaction each. Returns how many were sent.

    The row stays locked while Telegram is called, so two senders never send
    the same alert. If the process dies after Telegram accepts a message but
    before the commit, that one message is sent again on the next run.
    """
    sent = 0
    for _ in range(limit):
        with session_factory() as session:
            notification = session.scalars(
                select(Notification)
                .where(Notification.sent_at.is_(None), Notification.next_attempt_at <= now)
                .order_by(Notification.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if notification is None:
                break
            user = session.get(User, notification.user_id)
            if user.is_blocked:
                session.delete(notification)
                session.commit()
                continue
            try:
                client.send_message(
                    user.telegram_chat_id,
                    render(notification.kind, notification.payload, base_url=base_url),
                )
            except TelegramBlocked:
                user.is_blocked = True
                session.execute(
                    delete(Notification).where(
                        Notification.user_id == user.id, Notification.sent_at.is_(None)
                    )
                )
            except TelegramError as e:
                notification.attempts += 1
                notification.last_error = str(e)[:500]
                if notification.attempts >= MAX_ATTEMPTS:
                    notification.next_attempt_at = None  # abandoned; kept for inspection
                else:
                    wait = min(timedelta(minutes=2**notification.attempts), _MAX_WAIT)
                    notification.next_attempt_at = now + wait
            else:
                notification.sent_at = now
                sent += 1
            session.commit()
    return sent
