import json

import httpx
import pytest

from app.notify.telegram import TelegramBlocked, TelegramClient, TelegramError


def client_with(handler):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    return TelegramClient("123:ABC", httpx.Client(transport=httpx.MockTransport(wrapped))), seen


def test_telegram_client_request_shape():
    client, seen = client_with(lambda r: httpx.Response(200, json={"ok": True, "result": {}}))
    client.send_message(42, "<b>hi</b>")
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert json.loads(request.content) == {
        "chat_id": 42,
        "text": "<b>hi</b>",
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }


def test_set_webhook_request_shape():
    client, seen = client_with(lambda r: httpx.Response(200, json={"ok": True, "result": True}))
    client.set_webhook("https://t.example.my/telegram/webhook", "s3cret")
    body = json.loads(seen[0].content)
    assert str(seen[0].url).endswith("/setWebhook")
    assert body["url"] == "https://t.example.my/telegram/webhook"
    assert body["secret_token"] == "s3cret"
    assert body["allowed_updates"] == ["message"]


def test_telegram_client_maps_403_to_blocked():
    client, _ = client_with(
        lambda r: httpx.Response(403, json={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"})
    )
    with pytest.raises(TelegramBlocked):
        client.send_message(42, "x")


def test_chat_not_found_is_blocked():
    client, _ = client_with(
        lambda r: httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
    )
    with pytest.raises(TelegramBlocked):
        client.send_message(42, "x")


def test_other_errors_are_telegram_error():
    client, _ = client_with(lambda r: httpx.Response(502, text="Bad Gateway"))
    with pytest.raises(TelegramError) as info:
        client.send_message(42, "x")
    assert not isinstance(info.value, TelegramBlocked)


def test_network_error_is_telegram_error():
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    client, _ = client_with(handler)
    with pytest.raises(TelegramError):
        client.send_message(42, "x")


def test_token_not_in_error_message():
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    client, _ = client_with(handler)
    with pytest.raises(TelegramError) as info:
        client.send_message(42, "x")
    assert "123:ABC" not in str(info.value)


def test_log_only_client_logs_instead_of_sending(caplog):
    from app.notify.telegram import LogOnlyTelegram

    with caplog.at_level("INFO"):
        LogOnlyTelegram().send_message(42, "<b>hi</b>")
    assert "42" in caplog.text and "<b>hi</b>" in caplog.text
