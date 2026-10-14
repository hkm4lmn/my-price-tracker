"""Fetching shop pages without letting users point the server at itself.

Anyone can send the bot a link, so a link to http://169.254.169.254/ or
http://localhost:5432/ must never be fetched (server-side request forgery).
Every hop, including each redirect, is resolved and checked, and the request
is sent to the exact address that passed the check, so DNS can't change the
answer between checking and connecting.
"""

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.fetching.errors import Blocked, FetchError, FetchTimeout, NotFound, ParseError, UnsafeUrl
from app.urls import InvalidUrl, normalize_url

Resolver = Callable[[str], list[str]]

_REDIRECTS = {301, 302, 303, 307, 308}


@dataclass(frozen=True)
class FetchedPage:
    url: str
    status: int
    text: str


def is_public_ip(ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return list(dict.fromkeys(info[4][0] for info in infos))


def fetch(
    url: str,
    *,
    user_agent: str,
    resolver: Resolver = default_resolver,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 10.0,
    max_bytes: int = 2_000_000,
    max_redirects: int = 5,
) -> FetchedPage:
    with httpx.Client(transport=transport, timeout=timeout, follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            target = _checked_url(url, resolver)
            response_status, location, text = _get(client, target, url, user_agent, max_bytes)
            if response_status in _REDIRECTS and location:
                url = str(httpx.URL(url).join(location))
                continue
            return _page_or_error(url, response_status, text)
    raise Blocked(f"more than {max_redirects} redirects")


def _checked_url(url: str, resolver: Resolver) -> httpx.URL:
    """Validate a URL and return it rewritten to point at a checked public IP."""
    # normalize_url only validates here (scheme, port, no credentials). The
    # request uses the URL as given: stripping a trailing slash that a shop
    # redirects back to would loop forever.
    try:
        normalize_url(url)
    except InvalidUrl as e:
        raise UnsafeUrl(str(e)) from e
    parsed = httpx.URL(url).copy_with(fragment=None)
    host = parsed.host
    try:
        addresses = resolver(host)
    except OSError as e:
        raise NotFound(f"could not resolve {host}") from e
    if not addresses:
        raise NotFound(f"could not resolve {host}")
    # Refuse if *any* address is private: otherwise a host with one public and
    # one private record could be used to reach internal services.
    for address in addresses:
        if not is_public_ip(address):
            raise UnsafeUrl(f"{host} resolves to a non-public address")
    return parsed.copy_with(host=addresses[0])


def _get(
    client: httpx.Client, target: httpx.URL, original: str, user_agent: str, max_bytes: int
) -> tuple[int, str | None, str]:
    host = httpx.URL(original).host
    request = client.build_request(
        "GET",
        target,
        headers={
            "Host": host,
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-MY,en;q=0.9",
        },
        # The connection goes to an IP; TLS must still verify the real host name.
        extensions={"sni_hostname": host},
    )
    try:
        response = client.send(request, stream=True)
        try:
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ParseError("response too large")
            encoding = response.encoding or "utf-8"
            return response.status_code, response.headers.get("location"), body.decode(
                encoding, errors="replace"
            )
        finally:
            response.close()
    except httpx.TimeoutException as e:
        raise FetchTimeout(f"timed out fetching {host}") from e
    except httpx.TransportError as e:
        raise FetchError(f"network error fetching {host}: {e}", kind="network") from e


def _page_or_error(url: str, status: int, text: str) -> FetchedPage:
    if status in (403, 429):
        raise Blocked(f"HTTP {status}")
    if status in (404, 410):
        raise NotFound(f"HTTP {status}")
    if not 200 <= status < 300:
        raise FetchError(f"HTTP {status}", kind="http_error")
    return FetchedPage(url=url, status=status, text=text)
