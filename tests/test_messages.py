import pytest

from app.notify.messages import render

BASE = "https://tracker.example.my"
PRODUCT = {"product_title": "Kettle", "public_id": "abc12345", "url": "https://shop.my/k"}
LINK = "https://tracker.example.my/p/abc12345"


def test_render_escapes_title():
    payload = {**PRODUCT, "product_title": 'Cable <USB-C> & "Fast" 🔌', "old_price_sen": 2000, "new_price_sen": 1500}
    text = render("price_drop", payload, base_url=BASE)
    assert 'Cable &lt;USB-C&gt; &amp; "Fast" 🔌' in text
    assert "<USB-C>" not in text
    assert LINK in text


@pytest.mark.parametrize(
    "kind, extra, expected",
    [
        ("tracking_started", {"new_price_sen": 8990, "in_stock": True}, ["Now tracking", "RM 89.90", LINK]),
        ("tracking_started", {"new_price_sen": 8990, "in_stock": False}, ["out of stock"]),
        ("price_drop", {"old_price_sen": 10000, "new_price_sen": 8990}, ["RM 100.00", "RM 89.90", LINK]),
        ("target_reached", {"old_price_sen": 10000, "new_price_sen": 4900, "target_price_sen": 5000}, ["RM 49.00", "RM 50.00", LINK]),
        ("back_in_stock", {"new_price_sen": 8990}, ["back in stock", "RM 89.90", LINK]),
        ("failing", {}, ["can't read the price", "keep trying"]),
        ("dead", {}, ["stopped tracking", "7 days"]),
    ],
)
def test_render_each_kind(kind, extra, expected):
    text = render(kind, {**PRODUCT, **extra}, base_url=BASE)
    assert "<b>Kettle</b>" in text
    for fragment in expected:
        assert fragment in text


def test_render_handles_base_url_trailing_slash():
    text = render("back_in_stock", {**PRODUCT, "new_price_sen": 1}, base_url=BASE + "/")
    assert LINK in text and "//p/" not in text


def test_render_unknown_kind_raises():
    with pytest.raises(ValueError):
        render("nope", PRODUCT, base_url=BASE)


def test_render_unreadable():
    text = render("unreadable", PRODUCT, base_url=BASE)
    assert "<b>Kettle</b>" in text and "couldn't read a price" in text


def test_render_truncates_long_titles():
    text = render("back_in_stock", {**PRODUCT, "product_title": "A" * 500, "new_price_sen": 1}, base_url=BASE)
    assert "A" * 81 not in text
    assert "…" in text
