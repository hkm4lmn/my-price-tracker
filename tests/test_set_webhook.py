import pytest

from app.config import Settings
from scripts.set_webhook import register_webhook
from tests.fakes import FakeTelegram


def test_registers_https_webhook_with_secret():
    tg = FakeTelegram()
    url = register_webhook(tg, Settings(public_base_url="https://price.example.my/", telegram_webhook_secret="s3"))
    assert url == "https://price.example.my/telegram/webhook"
    assert tg.webhook == ("https://price.example.my/telegram/webhook", "s3")


@pytest.mark.parametrize(
    "base, secret",
    [("http://price.example.my", "s3"), ("https://price.example.my", "")],
)
def test_refuses_insecure_settings(base, secret):
    tg = FakeTelegram()
    with pytest.raises(SystemExit):
        register_webhook(tg, Settings(public_base_url=base, telegram_webhook_secret=secret))
    assert tg.webhook is None
