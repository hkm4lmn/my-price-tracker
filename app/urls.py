"""Cleaning up product links so the same product is stored once."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that only track where a click came from. Removing them
# lets "?utm_source=fb" and "?utm_source=wa" links count as one product.
_TRACKING_PARAMS = {"fbclid", "gclid", "msclkid", "srsltid", "spm", "scm"}
_DEFAULT_PORTS = {"http": 80, "https": 443}

_URL_IN_TEXT = re.compile(r"https?://\S+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?)]}'\""


class InvalidUrl(ValueError):
    pass


def normalize_url(raw: str) -> str:
    """Return a canonical form of an http(s) product URL, or raise InvalidUrl."""
    try:
        parts = urlsplit(raw.strip())
        port = parts.port
    except ValueError as e:
        raise InvalidUrl(f"not a valid link: {raw!r}") from e

    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise InvalidUrl("only http and https links are supported")
    if not parts.hostname:
        raise InvalidUrl(f"not a valid link: {raw!r}")
    if parts.username or parts.password:
        raise InvalidUrl("links with a username or password are not supported")
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        raise InvalidUrl("links must use the standard port")

    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    params = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(sorted(params))

    return urlunsplit((scheme, parts.hostname.lower(), path, query, ""))


def extract_url(text: str) -> str | None:
    """Find the first http(s) link in a message, without trailing punctuation."""
    match = _URL_IN_TEXT.search(text)
    if not match:
        return None
    return match.group(0).rstrip(_TRAILING_PUNCTUATION)
