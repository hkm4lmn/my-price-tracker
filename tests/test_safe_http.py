import httpx
import pytest

from app.fetching.errors import Blocked, FetchError, FetchTimeout, NotFound, ParseError, UnsafeUrl
from app.fetching.safe_http import fetch, is_public_ip

UA = "test-agent"
PUBLIC = "93.184.215.14"


def resolver_for(mapping):
    def resolve(host):
        return mapping[host]

    return resolve


def transport(handler):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    t = httpx.MockTransport(wrapped)
    t.calls = calls
    return t


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.1",
        "172.16.5.4",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "100.64.0.1",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
    ],
)
def test_is_public_ip_false(ip):
    assert is_public_ip(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", "2606:4700:4700::1111", PUBLIC])
def test_is_public_ip_true(ip):
    assert is_public_ip(ip) is True


def test_fetch_refuses_private_resolution():
    t = transport(lambda r: httpx.Response(200, text="hi"))
    with pytest.raises(UnsafeUrl):
        fetch(
            "https://shop.my/x",
            user_agent=UA,
            resolver=resolver_for({"shop.my": ["127.0.0.1"]}),
            transport=t,
        )
    assert t.calls == []


def test_fetch_refuses_if_any_address_private():
    t = transport(lambda r: httpx.Response(200, text="hi"))
    with pytest.raises(UnsafeUrl):
        fetch(
            "https://shop.my/x",
            user_agent=UA,
            resolver=resolver_for({"shop.my": ["8.8.8.8", "10.0.0.1"]}),
            transport=t,
        )
    assert t.calls == []


def test_fetch_refuses_bad_scheme_and_port():
    with pytest.raises(UnsafeUrl):
        fetch("file:///etc/passwd", user_agent=UA, resolver=resolver_for({}))
    with pytest.raises(UnsafeUrl):
        fetch("http://shop.my:8080/x", user_agent=UA, resolver=resolver_for({}))


def test_fetch_refuses_redirect_to_private():
    def handler(request):
        if request.headers["host"] == "shop.my":
            return httpx.Response(302, headers={"location": "http://internal.test/admin"})
        return httpx.Response(200, text="secret")

    t = transport(handler)
    with pytest.raises(UnsafeUrl):
        fetch(
            "https://shop.my/x",
            user_agent=UA,
            resolver=resolver_for({"shop.my": [PUBLIC], "internal.test": ["10.1.2.3"]}),
            transport=t,
        )
    assert len(t.calls) == 1


def test_fetch_follows_safe_relative_redirect():
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, text="<html>ok</html>")

    page = fetch(
        "https://shop.my/old",
        user_agent=UA,
        resolver=resolver_for({"shop.my": [PUBLIC]}),
        transport=transport(handler),
    )
    assert page.url == "https://shop.my/new"
    assert page.status == 200
    assert page.text == "<html>ok</html>"


def test_fetch_follows_redirect_to_trailing_slash():
    # Stored URLs have the trailing slash removed for de-duplication; shops
    # that redirect back to the slash form must not cause a redirect loop.
    def handler(request):
        if request.url.path == "/product/x":
            return httpx.Response(301, headers={"location": "https://shop.my/product/x/"})
        return httpx.Response(200, text="ok")

    t = transport(handler)
    page = fetch(
        "https://shop.my/product/x",
        user_agent=UA,
        resolver=resolver_for({"shop.my": [PUBLIC]}),
        transport=t,
    )
    assert page.text == "ok"
    assert [r.url.path for r in t.calls] == ["/product/x", "/product/x/"]


def test_fetch_too_many_redirects():
    def handler(request):
        n = int(request.url.path.strip("/") or 0)
        return httpx.Response(302, headers={"location": f"/{n + 1}"})

    t = transport(handler)
    with pytest.raises(Blocked):
        fetch(
            "https://shop.my/0",
            user_agent=UA,
            resolver=resolver_for({"shop.my": [PUBLIC]}),
            transport=t,
        )
    assert len(t.calls) == 6


def test_fetch_connects_to_checked_ip():
    t = transport(lambda r: httpx.Response(200, text="ok"))
    fetch(
        "https://shop.my/x?a=1",
        user_agent=UA,
        resolver=resolver_for({"shop.my": [PUBLIC]}),
        transport=t,
    )
    (request,) = t.calls
    assert request.url.host == PUBLIC
    assert request.url.path == "/x"
    assert request.url.query == b"a=1"
    assert request.headers["host"] == "shop.my"
    assert request.headers["user-agent"] == UA
    assert request.extensions["sni_hostname"] == "shop.my"


def test_fetch_connects_to_checked_ipv6():
    t = transport(lambda r: httpx.Response(200, text="ok"))
    fetch(
        "http://shop.my/x",
        user_agent=UA,
        resolver=resolver_for({"shop.my": ["2606:4700:4700::1111"]}),
        transport=t,
    )
    (request,) = t.calls
    assert request.url.host == "2606:4700:4700::1111"
    assert request.headers["host"] == "shop.my"


@pytest.mark.parametrize(
    "status, error",
    [(403, Blocked), (429, Blocked), (404, NotFound), (410, NotFound)],
)
def test_fetch_status_mapping(status, error):
    with pytest.raises(error):
        fetch(
            "https://shop.my/x",
            user_agent=UA,
            resolver=resolver_for({"shop.my": [PUBLIC]}),
            transport=transport(lambda r: httpx.Response(status)),
        )


def test_fetch_server_error_is_http_error():
    with pytest.raises(FetchError) as info:
        fetch(
            "https://shop.my/x",
            user_agent=UA,
            resolver=resolver_for({"shop.my": [PUBLIC]}),
            transport=transport(lambda r: httpx.Response(500)),
        )
    assert info.value.kind == "http_error"


def test_fetch_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(FetchTimeout):
        fetch(
            "https://shop.my/x",
            user_agent=UA,
            resolver=resolver_for({"shop.my": [PUBLIC]}),
            transport=transport(handler),
        )


def test_fetch_dns_failure_is_not_found():
    def resolve(host):
        raise OSError("no such host")

    with pytest.raises(NotFound):
        fetch("https://nope.my/x", user_agent=UA, resolver=resolve)


def test_fetch_truncates_large_body():
    big = "a" * 3_000_000
    with pytest.raises(ParseError, match="too large"):
        fetch(
            "https://shop.my/x",
            user_agent=UA,
            resolver=resolver_for({"shop.my": [PUBLIC]}),
            transport=transport(lambda r: httpx.Response(200, text=big)),
        )
