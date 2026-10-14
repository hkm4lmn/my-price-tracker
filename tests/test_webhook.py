import pytest
from fastapi.testclient import TestClient

from app.bot.limits import CommandLimiter
from app.config import Settings
from app.web.main import create_app
from tests.factories import NOW
from tests.fakes import FakeClock, FakeTelegram

SECRET = "webhook-secret"
HEADERS = {"X-Telegram-Bot-Api-Secret-Token": SECRET}


def msg(text, chat_id=7):
    return {"update_id": 1, "message": {"message_id": 1, "chat": {"id": chat_id, "type": "private"}, "text": text}}


@pytest.fixture
def telegram():
    return FakeTelegram()


@pytest.fixture
def client(session_factory, telegram):
    settings = Settings(telegram_webhook_secret=SECRET, public_base_url="https://tracker.example.my")
    app = create_app(
        session_factory=session_factory,
        telegram=telegram,
        settings=settings,
        limiter=CommandLimiter(20),
        clock=FakeClock(NOW),
    )
    return TestClient(app)


def test_webhook_rejects_bad_secret(client, telegram):
    assert client.post("/telegram/webhook", json=msg("/start")).status_code == 403
    bad = {"X-Telegram-Bot-Api-Secret-Token": "nope"}
    assert client.post("/telegram/webhook", json=msg("/start"), headers=bad).status_code == 403
    assert telegram.sent == []


def test_webhook_rejects_when_secret_not_configured(session_factory, telegram):
    app = create_app(session_factory=session_factory, telegram=telegram, settings=Settings(telegram_webhook_secret=""))
    r = TestClient(app).post("/telegram/webhook", json=msg("/start"), headers={"X-Telegram-Bot-Api-Secret-Token": ""})
    assert r.status_code == 403


def test_webhook_sends_replies(client, telegram):
    r = client.post("/telegram/webhook", json=msg("/start"), headers=HEADERS)
    assert r.status_code == 200
    assert len(telegram.sent) == 1 and telegram.sent[0][0] == 7


def test_webhook_returns_200_for_unhandled_update(client, telegram):
    r = client.post("/telegram/webhook", json={"update_id": 5, "channel_post": {}}, headers=HEADERS)
    assert r.status_code == 200
    assert telegram.sent == []


def test_webhook_rate_limited_reply_sent_once(client, telegram):
    for _ in range(20):
        client.post("/telegram/webhook", json=msg("/help"), headers=HEADERS)
    assert len(telegram.sent) == 20
    for _ in range(3):
        assert client.post("/telegram/webhook", json=msg("/help"), headers=HEADERS).status_code == 200
    assert len(telegram.sent) == 21
    assert "slow down" in telegram.sent[-1][1].lower()


def test_webhook_blocked_user_marked(client, telegram, session):
    from sqlalchemy import select

    from app.models import User

    telegram.block(7)
    assert client.post("/telegram/webhook", json=msg("/start"), headers=HEADERS).status_code == 200
    session.expire_all()
    assert session.scalar(select(User).where(User.telegram_chat_id == 7)).is_blocked is True


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
