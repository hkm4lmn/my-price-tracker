"""Shop plug-ins: each turns a fetched page into a Snapshot.

To support a shop whose pages lack standard product data, add a plug-in whose
matches() recognises its host and put it before SchemaOrgPlugin, which is the
catch-all.
"""

from typing import Protocol
from urllib.parse import urlsplit

from app.fetching.plugins.schema_org import SchemaOrgPlugin
from app.fetching.safe_http import FetchedPage, fetch
from app.fetching.snapshot import Snapshot


class Plugin(Protocol):
    name: str

    def matches(self, host: str) -> bool: ...

    def parse(self, page: FetchedPage) -> Snapshot: ...


PLUGINS: list[Plugin] = [SchemaOrgPlugin()]


def plugin_for(url: str) -> Plugin:
    host = (urlsplit(url).hostname or "").lower()
    return next(p for p in PLUGINS if p.matches(host))


def fetch_snapshot(url: str, *, user_agent: str, fetcher=fetch) -> Snapshot:
    page = fetcher(url, user_agent=user_agent)
    return plugin_for(url).parse(page)
