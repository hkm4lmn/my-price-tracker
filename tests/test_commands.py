from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.bot.commands import Reply, handle_update
from app.models import Product, User, Watch
from tests.factories import NOW, make_product, make_user, make_watch

BASE = "https://tracker.example.my"
CHAT = 4242


def msg(text, chat_id=CHAT, chat_type="private"):
    return {"update_id": 1, "message": {"message_id": 1, "chat": {"id": chat_id, "type": chat_type}, "text": text}}


def send(session, text, now=NOW, chat_id=CHAT):
    replies = handle_update(session, msg(text, chat_id=chat_id), now=now, base_url=BASE)
    assert all(isinstance(r, Reply) and r.chat_id == chat_id for r in replies)
    return "\n".join(r.html for r in replies)


def watches(session, chat_id=CHAT):
    return session.scalars(
        select(Watch).join(User, User.id == Watch.user_id).where(User.telegram_chat_id == chat_id).order_by(Watch.id)
    ).all()


def user(session, chat_id=CHAT):
    return session.scalar(select(User).where(User.telegram_chat_id == chat_id))


def test_start_registers_user(session):
    text = send(session, "/start")
    assert user(session) is not None
    assert "send me a link" in text.lower()


def test_start_unblocks_returning_user(session):
    make_user(session, chat_id=CHAT, is_blocked=True)
    send(session, "/start")
    session.expire_all()
    assert user(session).is_blocked is False


def test_link_in_sentence_creates_product_and_watch(session):
    text = send(session, "check this https://shop.my/kettle?utm_source=fb thanks")
    product = session.scalar(select(Product))
    assert product.url == "https://shop.my/kettle"
    assert product.shop == "schema_org"
    assert product.next_check_at == NOW
    (watch,) = watches(session)
    assert watch.product_id == product.id
    assert watch.baseline_price_sen is None
    assert "checking the price" in text.lower()


def test_same_product_different_url_forms_dedupes(session):
    send(session, "https://shop.my/kettle?utm_source=fb")
    send(session, "https://SHOP.my/kettle/", chat_id=CHAT + 1)
    assert session.scalar(select(func.count()).select_from(Product)) == 1
    assert session.scalar(select(func.count()).select_from(Watch)) == 2


def test_existing_priced_product_replies_with_price_and_sets_baseline(session):
    product = make_product(
        session, url="https://shop.my/fan", title="Desk Fan", current_price_sen=12900, in_stock=True, last_success_at=NOW
    )
    text = send(session, "https://shop.my/fan")
    (watch,) = watches(session)
    assert watch.baseline_price_sen == 12900
    assert "Desk Fan" in text and "RM 129.00" in text
    assert f"{BASE}/p/{product.public_id}" in text


def test_already_watching(session):
    send(session, "https://shop.my/a")
    text = send(session, "https://shop.my/a")
    assert "already" in text.lower()
    assert len(watches(session)) == 1


def test_invalid_link(session):
    text = send(session, "https://shop.my:8080/a")
    assert "link" in text.lower()
    assert session.scalar(select(func.count()).select_from(Product)) == 0


def test_watch_limit_20(session):
    u = make_user(session, chat_id=CHAT)
    for i in range(20):
        make_watch(session, u, make_product(session), created_at=NOW - timedelta(days=2))
    text = send(session, "https://shop.my/one-more")
    assert "20" in text
    assert len(watches(session)) == 20


def test_daily_new_watch_limit_10(session):
    for i in range(10):
        send(session, f"https://shop.my/p{i}", now=NOW - timedelta(hours=23))
    text = send(session, "https://shop.my/eleventh")
    assert "10" in text
    assert len(watches(session)) == 10
    # A day after those adds it's allowed again.
    send(session, "https://shop.my/eleventh", now=NOW + timedelta(hours=1, minutes=1))
    assert len(watches(session)) == 11


def test_daily_limit_not_reset_by_removing(session):
    for i in range(10):
        send(session, f"https://shop.my/p{i}")
        send(session, "/remove 1")
    assert watches(session) == []
    text = send(session, "https://shop.my/p10")
    assert "10" in text
    assert watches(session) == []


def test_shopee_links_refused_with_reason(session):
    for link in ("https://shopee.com.my/Some-Item-i.1.2", "https://shp.ee/abc123"):
        text = send(session, link)
        assert "shopee" in text.lower()
    assert session.scalar(select(func.count()).select_from(Product)) == 0


def test_list_split_under_telegram_limit(session):
    u = make_user(session, chat_id=CHAT)
    for i in range(20):
        make_watch(session, u, make_product(session, title=f"{i:02d} " + "Long product name " * 9, current_price_sen=100))
    replies = handle_update(session, msg("/list"), now=NOW, base_url=BASE)
    assert all(len(r.html) <= 4096 for r in replies)
    joined = "\n".join(r.html for r in replies)
    assert all(f"{n}. <b>" in joined for n in range(1, 21))


def test_huge_target_gets_usage_reply(session):
    send(session, "https://shop.my/a")
    text = send(session, "/target 1 99999999999")
    assert "/target" in text
    session.expire_all()
    assert watches(session)[0].target_price_sen is None


def test_list_numbering_and_prices(session):
    u = make_user(session, chat_id=CHAT)
    a = make_product(session, title="Kettle <2L>", current_price_sen=8990, in_stock=True)
    b = make_product(session, title="Fan", current_price_sen=12900, in_stock=False, status="failing")
    make_watch(session, u, a, created_at=NOW - timedelta(hours=2), target_price_sen=7000)
    make_watch(session, u, b, created_at=NOW - timedelta(hours=1))
    text = send(session, "/list")
    assert text.index("1. <b>Kettle &lt;2L&gt;</b>") < text.index("2. <b>Fan</b>")
    assert "RM 89.90" in text and "RM 70.00" in text
    assert "out of stock" in text.lower()
    assert f"/p/{a.public_id}" in text


def test_list_empty(session):
    assert "not tracking" in send(session, "/list").lower()


def test_target_set_and_off(session):
    send(session, "https://shop.my/a")
    w = watches(session)[0]
    w.target_armed = False
    session.commit()
    text = send(session, "/target 1 59.90")
    session.expire_all()
    w = watches(session)[0]
    assert (w.target_price_sen, w.target_armed) == (5990, True)
    assert "RM 59.90" in text
    send(session, "/target 1 off")
    session.expire_all()
    assert watches(session)[0].target_price_sen is None


@pytest.mark.parametrize("args", ["1 abc", "1 0", "1", "9 10", "x 10"])
def test_target_bad_input(session, args):
    send(session, "https://shop.my/a")
    text = send(session, f"/target {args}")
    assert "/list" in text or "/target" in text
    session.expire_all()
    assert watches(session)[0].target_price_sen is None


def test_remove(session):
    send(session, "https://shop.my/a")
    send(session, "https://shop.my/b")
    text = send(session, "/remove 1")
    assert "stopped" in text.lower()
    remaining = watches(session)
    assert len(remaining) == 1
    assert session.get(Product, remaining[0].product_id).url == "https://shop.my/b"


def test_check_cooldown_60_minutes(session):
    send(session, "https://shop.my/a")
    product = session.scalar(select(Product))
    product.next_check_at = NOW + timedelta(hours=5)
    session.commit()

    send(session, "/check 1", now=NOW)
    session.expire_all()
    product = session.get(Product, product.id)
    assert product.next_check_at == NOW
    assert product.last_manual_check_at == NOW

    product.next_check_at = NOW + timedelta(hours=5)
    session.commit()
    text = send(session, "/check 1", now=NOW + timedelta(minutes=59))
    assert "minute" in text
    session.expire_all()
    assert session.get(Product, product.id).next_check_at == NOW + timedelta(hours=5)

    send(session, "/check 1", now=NOW + timedelta(minutes=60))
    session.expire_all()
    assert session.get(Product, product.id).next_check_at == NOW + timedelta(minutes=60)


def test_dead_product_revived_on_readd(session):
    product = make_product(
        session, url="https://shop.my/old", status="dead", consecutive_failures=30, current_price_sen=500,
        last_success_at=NOW - timedelta(days=10), next_check_at=NOW - timedelta(days=3),
    )
    text = send(session, "https://shop.my/old")
    session.expire_all()
    product = session.get(Product, product.id)
    assert (product.status, product.consecutive_failures, product.next_check_at) == ("active", 0, NOW)
    # The old price is stale, so the baseline waits for a fresh check.
    assert watches(session)[0].baseline_price_sen is None
    assert "checking the price" in text.lower()


def test_unknown_command_shows_help(session):
    assert "/list" in send(session, "/whatever")


def test_command_with_bot_suffix(session):
    assert "not tracking" in send(session, "/list@MYPriceBot").lower()


def test_plain_text_without_link_shows_help(session):
    assert "/list" in send(session, "hello")


def test_non_text_update_ignored(session):
    sticker = {"update_id": 2, "message": {"message_id": 2, "chat": {"id": CHAT, "type": "private"}, "sticker": {}}}
    assert handle_update(session, sticker, now=NOW, base_url=BASE) == []
    assert handle_update(session, {"update_id": 3, "edited_message": msg("/list")["message"]}, now=NOW, base_url=BASE) == []
    assert user(session) is None


def test_group_chats_ignored(session):
    assert handle_update(session, msg("/start", chat_type="group"), now=NOW, base_url=BASE) == []
    assert user(session) is None


def test_lazada_links_refused_with_reason(session):
    for link in ("https://www.lazada.com.my/products/pdp-i1-s2.html", "https://s.lazada.com.my/s.abc"):
        text = send(session, link)
        assert "lazada" in text.lower()
    assert session.scalar(select(func.count()).select_from(Product)) == 0
