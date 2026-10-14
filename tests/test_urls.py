import pytest

from app.urls import InvalidUrl, extract_url, normalize_url


def test_normalize_url_variants_match():
    expected = "https://shop.my/x"
    assert normalize_url("https://SHOP.my/x/") == expected
    assert normalize_url("https://shop.my/x?utm_source=fb&utm_medium=x") == expected
    assert normalize_url("https://shop.my/x#reviews") == expected
    assert normalize_url("https://shop.my:443/x") == expected
    assert normalize_url("HTTPS://shop.my/x?fbclid=abc&gclid=def") == expected


def test_normalize_url_keeps_and_sorts_other_params():
    assert normalize_url("https://shop.my/x?b=2&a=1") == "https://shop.my/x?a=1&b=2"
    assert normalize_url("https://shop.my/x?b=2&utm_campaign=z&a=1") == "https://shop.my/x?a=1&b=2"


def test_normalize_url_keeps_root_path_and_http():
    assert normalize_url("http://shop.my") == "http://shop.my/"
    assert normalize_url("http://shop.my:80/") == "http://shop.my/"


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://x.my/a",
        "javascript:alert(1)",
        "https://x.my:8443/a",
        "https://user:pw@x.my/a",
        "not a url",
        "https:///nohost",
    ],
)
def test_normalize_url_rejects(raw):
    with pytest.raises(InvalidUrl):
        normalize_url(raw)


def test_extract_url_from_sentence():
    assert (
        extract_url("check this https://shop.my/x?utm_source=fb thanks")
        == "https://shop.my/x?utm_source=fb"
    )


def test_extract_url_strips_trailing_punctuation():
    assert extract_url("(see https://shop.my/x).") == "https://shop.my/x"
    assert extract_url("https://shop.my/x, and more") == "https://shop.my/x"


def test_extract_url_none_and_first_of_two():
    assert extract_url("no link here") is None
    assert extract_url("https://a.my/1 https://b.my/2") == "https://a.my/1"
