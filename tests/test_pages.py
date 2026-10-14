import json
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.models import PricePoint
from app.web.main import create_app
from tests.factories import NOW, make_product, make_user, make_watch
from tests.fakes import FakeClock, FakeTelegram


@pytest.fixture
def client(session_factory):
    settings = Settings(telegram_webhook_secret="s", bot_username="MYPriceTrackerBot")
    return TestClient(
        create_app(session_factory=session_factory, telegram=FakeTelegram(), settings=settings, clock=FakeClock(NOW))
    )


def priced_product(session, **overrides):
    fields = dict(
        title="Desk Fan 16in",
        url="https://shop.my/fan",
        current_price_sen=12900,
        in_stock=True,
        last_success_at=NOW - timedelta(hours=2),
        last_checked_at=NOW - timedelta(hours=2),
    )
    fields.update(overrides)
    product = make_product(session, **fields)
    session.add_all(
        [
            PricePoint(product_id=product.id, price_sen=15900, in_stock=True, observed_at=NOW - timedelta(days=3)),
            PricePoint(product_id=product.id, price_sen=12900, in_stock=True, observed_at=NOW - timedelta(days=1)),
        ]
    )
    session.commit()
    return product


def chart_data(html):
    match = re.search(r'<script type="application/json" id="history">(.*?)</script>', html, re.S)
    return json.loads(match.group(1))


def test_product_page_shows_price_and_chart_data(client, session):
    product = priced_product(session)
    r = client.get(f"/p/{product.public_id}")
    assert r.status_code == 200
    assert "Desk Fan 16in" in r.text
    assert "RM 129.00" in r.text
    assert 'href="https://shop.my/fan"' in r.text
    assert "2 hours ago" in r.text
    points = chart_data(r.text)
    assert [p["price_sen"] for p in points] == [15900, 12900, 12900]
    times = [p["t"] for p in points]
    assert times == sorted(times)
    # The last point extends the line to the latest successful check.
    assert points[-1]["t"] == (NOW - timedelta(hours=2)).isoformat()


def test_product_page_escapes_title(client, session):
    product = priced_product(session, title="<script>alert(1)</script> Fan")
    r = client.get(f"/p/{product.public_id}")
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_product_page_unknown_id_404(client):
    r = client.get("/p/doesnotx")
    assert r.status_code == 404
    assert "isn't being tracked" in r.text


def test_product_page_failing_warning(client, session):
    product = priced_product(session, status="failing")
    r = client.get(f"/p/{product.public_id}")
    assert "can't read this price right now" in r.text


def test_product_page_never_checked(client, session):
    product = make_product(session, title=None, url="https://shop.my/new")
    r = client.get(f"/p/{product.public_id}")
    assert r.status_code == 200
    assert "first price check" in r.text.lower()


def test_product_page_has_no_user_data(client, session):
    product = priced_product(session)
    make_watch(session, make_user(session, chat_id=987654321), product)
    r = client.get(f"/p/{product.public_id}")
    assert "987654321" not in r.text
    assert "watch" not in r.text.lower()


def test_status_page_counts(client, session):
    priced_product(session)
    make_product(session, status="dead")
    r = client.get("/status")
    assert r.status_code == 200
    assert "1 active" in r.text and "1 stopped" in r.text


def test_home_links_bot(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "https://t.me/MYPriceTrackerBot" in r.text


def test_pages_have_viewport_and_lang(client):
    html = client.get("/").text
    assert '<meta name="viewport"' in html
    assert '<html lang="en">' in html
