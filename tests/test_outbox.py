import threading
from datetime import timedelta

from sqlalchemy import select

from app.models import Notification, User
from app.notify.outbox import enqueue, send_pending
from tests.factories import NOW, make_user
from tests.fakes import FakeClock, FakeTelegram

BASE = "https://tracker.example.my"
PAYLOAD = {"product_title": "Kettle", "public_id": "abc12345", "url": "https://shop.my/k", "new_price_sen": 100}


def add(session, user, n=1, title="Kettle"):
    for i in range(n):
        enqueue(
            session,
            user_id=user.id,
            kind="back_in_stock",
            payload={**PAYLOAD, "product_title": f"{title}{i}" if n > 1 else title},
            now=NOW,
        )
    session.commit()


def all_notifications(session):
    session.expire_all()
    return session.scalars(select(Notification).order_by(Notification.id)).all()


def test_send_pending_marks_sent(session, session_factory):
    user = make_user(session, chat_id=77)
    add(session, user)
    tg = FakeTelegram()
    assert send_pending(session_factory, tg, NOW, base_url=BASE) == 1
    assert len(tg.sent) == 1 and tg.sent[0][0] == 77
    assert "Kettle" in tg.sent[0][1]
    (n,) = all_notifications(session)
    assert n.sent_at == NOW
    # Nothing left to send.
    assert send_pending(session_factory, tg, NOW, base_url=BASE) == 0


def test_not_due_yet_is_not_sent(session, session_factory):
    user = make_user(session)
    add(session, user)
    tg = FakeTelegram()
    assert send_pending(session_factory, tg, NOW - timedelta(seconds=1), base_url=BASE) == 0
    assert tg.sent == []


def test_outbox_retries_then_delivers_exactly_once(session, session_factory):
    user = make_user(session)
    add(session, user)
    tg = FakeTelegram()
    tg.fail_next(3)
    clock = FakeClock(NOW)
    expected_waits = [timedelta(minutes=2), timedelta(minutes=4), timedelta(minutes=8)]

    for wait in expected_waits:
        assert send_pending(session_factory, tg, clock.now(), base_url=BASE) == 0
        (n,) = all_notifications(session)
        assert n.next_attempt_at == clock.now() + wait
        assert n.last_error
        # Retrying early does nothing.
        assert send_pending(session_factory, tg, clock.now(), base_url=BASE) == 0
        clock.advance(wait)

    assert send_pending(session_factory, tg, clock.now(), base_url=BASE) == 1
    assert len(tg.sent) == 1
    (n,) = all_notifications(session)
    assert n.sent_at == clock.now() and n.attempts == 3


def test_retry_wait_capped_at_2_hours(session, session_factory):
    user = make_user(session)
    add(session, user)
    n = all_notifications(session)[0]
    n.attempts = 8
    session.commit()
    tg = FakeTelegram()
    tg.fail_next(1)
    send_pending(session_factory, tg, NOW, base_url=BASE)
    (n,) = all_notifications(session)
    assert n.next_attempt_at == NOW + timedelta(hours=2)


def test_abandons_after_10_attempts(session, session_factory):
    user = make_user(session)
    add(session, user)
    n = all_notifications(session)[0]
    n.attempts = 9
    session.commit()
    tg = FakeTelegram()
    tg.fail_next(1)
    send_pending(session_factory, tg, NOW, base_url=BASE)
    (n,) = all_notifications(session)
    assert n.attempts == 10
    assert n.next_attempt_at is None and n.sent_at is None
    assert send_pending(session_factory, tg, NOW + timedelta(days=1), base_url=BASE) == 0


def test_blocked_user_flagged_and_queue_dropped(session, session_factory):
    blocked = make_user(session, chat_id=1)
    fine = make_user(session, chat_id=2)
    add(session, blocked, n=3)
    add(session, fine)
    tg = FakeTelegram()
    tg.block(1)

    assert send_pending(session_factory, tg, NOW, base_url=BASE) == 1
    assert [chat for chat, _ in tg.sent] == [2]
    session.expire_all()
    assert session.get(User, blocked.id).is_blocked is True
    assert [n.user_id for n in all_notifications(session)] == [fine.id]


def test_never_sends_to_already_blocked_user(session, session_factory):
    user = make_user(session, is_blocked=True)
    add(session, user)
    tg = FakeTelegram()
    assert send_pending(session_factory, tg, NOW, base_url=BASE) == 0
    assert tg.sent == []
    assert all_notifications(session) == []


def test_limit_respected(session, session_factory):
    user = make_user(session)
    add(session, user, n=5)
    tg = FakeTelegram()
    assert send_pending(session_factory, tg, NOW, base_url=BASE, limit=2) == 2
    assert send_pending(session_factory, tg, NOW, base_url=BASE) == 3


def test_two_senders_do_not_double_send(session, session_factory):
    user = make_user(session)
    add(session, user, n=30, title="Item")
    tg = FakeTelegram()
    barrier = threading.Barrier(2)

    def sender():
        barrier.wait()
        send_pending(session_factory, tg, NOW, base_url=BASE)

    threads = [threading.Thread(target=sender) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive()

    titles = [html.split("<b>")[1].split("</b>")[0] for _, html in tg.sent]
    assert sorted(titles) == sorted(f"Item{i}" for i in range(30))
