import json
from pathlib import Path

import pytest

from app.fetching.errors import ParseError
from app.fetching.plugins import fetch_snapshot, plugin_for
from app.fetching.plugins.schema_org import SchemaOrgPlugin
from app.fetching.safe_http import FetchedPage
from app.fetching.snapshot import Snapshot

FIXTURES = Path(__file__).parent / "fixtures"
plugin = SchemaOrgPlugin()


def page_from_file(name, url="https://shop.my/p"):
    return FetchedPage(url=url, status=200, text=(FIXTURES / name).read_text(encoding="utf-8"))


def page_with_jsonld(data, extra_head=""):
    html = (
        "<html><head>"
        f'<script type="application/ld+json">{json.dumps(data)}</script>'
        f"{extra_head}</head><body></body></html>"
    )
    return FetchedPage(url="https://shop.my/p", status=200, text=html)


def product(offers, **kw):
    return {"@context": "https://schema.org", "@type": "Product", "name": "Cable", "offers": offers, **kw}


def test_parses_real_fixture_machines():
    # Shopify shop: offers is a list; price is a JSON number; out of stock.
    snap = plugin.parse(page_from_file("machines_iphone.html"))
    assert snap.title == "iPhone Duo 256GB Star White"
    assert snap.price_sen == 949900
    assert snap.in_stock is False
    assert snap.currency == "MYR"


def test_parses_real_fixture_senheng():
    # WooCommerce shop: a ProductGroup inside @graph with 12 variants, some
    # without a price. Tracks the cheapest variant that is in stock.
    snap = plugin.parse(page_from_file("senheng_honor.html"))
    assert snap.title == "X9e Pro 5G ETA 16 Oct 2026 Onwards (Price after rebate*)"
    assert snap.price_sen == 179900
    assert snap.in_stock is True
    assert snap.currency == "MYR"


def test_jsonld_in_graph_and_list():
    offer = {"@type": "Offer", "price": "12.50", "priceCurrency": "MYR"}
    in_graph = {"@context": "https://schema.org", "@graph": [{"@type": "WebPage"}, product(offer)]}
    assert plugin.parse(page_with_jsonld(in_graph)).price_sen == 1250
    as_list = [{"@type": "Organization"}, product(offer)]
    assert plugin.parse(page_with_jsonld(as_list)).price_sen == 1250


def test_type_as_list():
    data = product({"price": "5", "priceCurrency": "MYR"})
    data["@type"] = ["Product", "Thing"]
    assert plugin.parse(page_with_jsonld(data)).price_sen == 500


def test_aggregate_offer_uses_low_price():
    data = product({"@type": "AggregateOffer", "lowPrice": "59.90", "highPrice": "89.90", "priceCurrency": "MYR"})
    assert plugin.parse(page_with_jsonld(data)).price_sen == 5990


def test_offer_list_prefers_cheapest_in_stock():
    data = product(
        [
            {"price": "10.00", "availability": "https://schema.org/OutOfStock"},
            {"price": "15.00", "availability": "https://schema.org/InStock"},
            {"price": "12.00", "availability": "https://schema.org/InStock"},
        ]
    )
    snap = plugin.parse(page_with_jsonld(data))
    assert (snap.price_sen, snap.in_stock) == (1200, True)


def test_price_in_price_specification():
    data = product({"priceSpecification": {"price": 20.5, "priceCurrency": "MYR"}})
    assert plugin.parse(page_with_jsonld(data)).price_sen == 2050


@pytest.mark.parametrize(
    "availability, expected",
    [
        ("https://schema.org/OutOfStock", False),
        ("http://schema.org/SoldOut", False),
        ("OutOfStock", False),
        ("https://schema.org/Discontinued", False),
        ("https://schema.org/InStock", True),
        (None, True),
    ],
)
def test_availability_mapping(availability, expected):
    offer = {"price": "1.00"}
    if availability:
        offer["availability"] = availability
    assert plugin.parse(page_with_jsonld(product(offer))).in_stock is expected


def test_out_of_stock_without_price():
    data = product({"availability": "https://schema.org/OutOfStock"})
    snap = plugin.parse(page_with_jsonld(data))
    assert snap.price_sen is None
    assert snap.in_stock is False


def test_missing_price_in_stock_is_parse_error():
    with pytest.raises(ParseError):
        plugin.parse(page_with_jsonld(product({"availability": "https://schema.org/InStock"})))


def test_non_myr_is_parse_error():
    with pytest.raises(ParseError):
        plugin.parse(page_with_jsonld(product({"price": "10", "priceCurrency": "SGD"})))


def test_missing_currency_treated_as_myr():
    snap = plugin.parse(page_with_jsonld(product({"price": "10"})))
    assert snap.currency == "MYR"


def test_image_first_of_list_or_object():
    data = product({"price": "1"}, image=["https://cdn.shop.my/a.jpg", "https://cdn.shop.my/b.jpg"])
    assert plugin.parse(page_with_jsonld(data)).image_url == "https://cdn.shop.my/a.jpg"
    data = product({"price": "1"}, image={"@type": "ImageObject", "url": "https://cdn.shop.my/c.jpg"})
    assert plugin.parse(page_with_jsonld(data)).image_url == "https://cdn.shop.my/c.jpg"


def test_invalid_jsonld_block_ignored():
    good = product({"price": "3", "priceCurrency": "MYR"})
    html = (
        '<html><head><script type="application/ld+json">{not json</script>'
        f'<script type="application/ld+json">{json.dumps(good)}</script></head></html>'
    )
    snap = plugin.parse(FetchedPage(url="https://shop.my/p", status=200, text=html))
    assert snap.price_sen == 300


def test_opengraph_fallback():
    html = (
        "<html><head>"
        '<meta property="og:title" content="Kettle 1.7L">'
        '<meta property="og:image" content="https://cdn.shop.my/k.jpg">'
        '<meta property="product:price:amount" content="89.00">'
        '<meta property="product:price:currency" content="MYR">'
        "</head></html>"
    )
    snap = plugin.parse(FetchedPage(url="https://shop.my/p", status=200, text=html))
    assert snap == Snapshot(
        title="Kettle 1.7L",
        price_sen=8900,
        in_stock=True,
        currency="MYR",
        image_url="https://cdn.shop.my/k.jpg",
    )


def test_no_product_data_is_parse_error():
    with pytest.raises(ParseError):
        plugin.parse(FetchedPage(url="https://shop.my/p", status=200, text="<html><title>Hi</title></html>"))


def test_plugin_for_falls_back_to_schema_org():
    assert plugin_for("https://anything.my/p").name == "schema_org"


def test_fetch_snapshot_uses_fetcher():
    seen = {}

    def fake_fetch(url, *, user_agent):
        seen["args"] = (url, user_agent)
        return page_with_jsonld(product({"price": "7"}))

    snap = fetch_snapshot("https://shop.my/p", user_agent="ua", fetcher=fake_fetch)
    assert snap.price_sen == 700
    assert seen["args"] == ("https://shop.my/p", "ua")


def test_image_falls_back_to_og_image():
    # Senheng's JSON-LD only references images by @id.
    snap = plugin.parse(page_from_file("senheng_honor.html"))
    assert snap.image_url == "https://www.senheng.com.my/wp-content/uploads/2026/09/PURPLE.jpg"
