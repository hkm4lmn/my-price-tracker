"""Reads schema.org product data, which many shops embed for search engines.

Looks for a JSON-LD Product (or ProductGroup with variants) first, then falls
back to OpenGraph product meta tags. When a page lists several offers or
variants, the cheapest one that is in stock is tracked.
"""

import html
import json
from dataclasses import replace
from typing import Any

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.fetching.errors import ParseError
from app.fetching.safe_http import FetchedPage
from app.fetching.snapshot import Snapshot
from app.money import MAX_PRICE_SEN, parse_ringgit

MAX_TITLE_LENGTH = 300
_OUT_OF_STOCK = {"outofstock", "soldout", "discontinued"}
_PRODUCT_TYPES = {"Product", "ProductGroup"}


class SchemaOrgPlugin:
    name = "schema_org"

    def matches(self, host: str) -> bool:
        return True

    def parse(self, page: FetchedPage) -> Snapshot:
        tree = HTMLParser(page.text)
        node = _find_product(tree)
        if node is None:
            return _from_opengraph(tree)
        snapshot = _from_jsonld(node)
        if snapshot.image_url is None:
            # Some shops only reference images by @id in JSON-LD.
            snapshot = replace(snapshot, image_url=_image(_og_meta(tree).get("og:image")))
        return snapshot


def _find_product(tree: HTMLParser) -> dict | None:
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text(), strict=False)
        except ValueError:
            continue  # one broken block shouldn't hide a good one
        node = _first_product(data)
        if node is not None:
            return node
    return None


def _first_product(data: Any) -> dict | None:
    if isinstance(data, list):
        for item in data:
            if (found := _first_product(item)) is not None:
                return found
    elif isinstance(data, dict):
        types = data.get("@type")
        types = set(types) if isinstance(types, list) else {types}
        if types & _PRODUCT_TYPES:
            return data
        if "@graph" in data:
            return _first_product(data["@graph"])
    return None


def _from_jsonld(node: dict) -> Snapshot:
    title = _text(node.get("name"))
    if not title:
        raise ParseError("product has no name")

    offers: list[dict] = []
    variants = node.get("hasVariant")
    if isinstance(variants, list):
        for variant in variants:
            if isinstance(variant, dict):
                offers.extend(_offers(variant.get("offers")))
    offers.extend(_offers(node.get("offers")))

    priced: list[tuple[int, bool]] = []
    any_in_stock = not offers
    foreign_currency = None
    for offer in offers:
        in_stock = _in_stock(offer.get("availability"))
        any_in_stock = any_in_stock or in_stock
        price, currency = _offer_price(offer)
        if price is None:
            continue
        if currency not in (None, "MYR"):
            foreign_currency = currency
            continue
        priced.append((price, in_stock))

    if not priced and foreign_currency:
        raise ParseError(f"price is in {foreign_currency}, not MYR")
    price = _cheapest(priced)
    if price is None and any_in_stock:
        raise ParseError("product has no price")
    return Snapshot(
        title=title,
        price_sen=price,
        in_stock=any_in_stock,
        currency="MYR",
        image_url=_image(node.get("image")),
    )


def _offers(value: Any) -> list[dict]:
    if isinstance(value, dict):
        nested = value.get("offers")
        if value.get("@type") == "AggregateOffer" and "lowPrice" not in value and nested:
            return _offers(nested)
        return [value]
    if isinstance(value, list):
        return [o for o in value if isinstance(o, dict)]
    return []


def _offer_price(offer: dict) -> tuple[int | None, str | None]:
    raw = offer.get("lowPrice", offer.get("price"))
    currency = offer.get("priceCurrency")
    spec = offer.get("priceSpecification")
    if raw is None and spec:
        specs = spec if isinstance(spec, list) else [spec]
        for s in specs:
            if isinstance(s, dict) and s.get("price") is not None:
                raw, currency = s["price"], currency or s.get("priceCurrency")
                break
    if raw is None or raw == "":
        return None, currency
    try:
        price = parse_ringgit(str(raw))
    except ValueError as e:
        raise ParseError(f"unreadable price {raw!r}") from e
    if price > MAX_PRICE_SEN:
        raise ParseError(f"implausible price {raw!r}")
    return price, currency


def _cheapest(priced: list[tuple[int, bool]]) -> int | None:
    in_stock = [p for p, ok in priced if ok]
    pool = in_stock or [p for p, _ in priced]
    return min(pool) if pool else None


def _in_stock(availability: Any) -> bool:
    if not isinstance(availability, str) or not availability:
        return True
    return availability.rstrip("/").rsplit("/", 1)[-1].lower() not in _OUT_OF_STOCK


def _image(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    return value if isinstance(value, str) and value.startswith("http") else None


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    # PostgreSQL text can't hold NUL characters; page data is untrusted.
    return html.unescape(value).replace("\x00", "").strip()[:MAX_TITLE_LENGTH]


def _from_opengraph(tree: HTMLParser) -> Snapshot:
    meta = _og_meta(tree)
    amount = meta.get("product:price:amount")
    title = _text(meta.get("og:title"))
    if not amount or not title:
        raise ParseError("no product data found on the page")
    currency = (meta.get("product:price:currency") or "MYR").upper()
    if currency != "MYR":
        raise ParseError(f"price is in {currency}, not MYR")
    try:
        price = parse_ringgit(amount)
    except ValueError as e:
        raise ParseError(f"unreadable price {amount!r}") from e
    if price > MAX_PRICE_SEN:
        raise ParseError(f"implausible price {amount!r}")
    availability = (meta.get("product:availability") or "").replace(" ", "").lower()
    return Snapshot(
        title=title,
        price_sen=price,
        in_stock=availability not in {"oos", "outofstock"},
        currency="MYR",
        image_url=_image(meta.get("og:image")),
    )


def _og_meta(tree: HTMLParser) -> dict[str, str]:
    """OpenGraph meta tags; the first wins when a property repeats (og:image)."""
    meta: dict[str, str] = {}
    for node in tree.css("meta[property]"):
        meta.setdefault(node.attributes.get("property"), node.attributes.get("content") or "")
    return meta
