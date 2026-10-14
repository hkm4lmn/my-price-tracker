import pytest

from app.money import format_sen, parse_ringgit


@pytest.mark.parametrize(
    "text, sen",
    [
        ("RM1,299.00", 129900),
        ("RM 72.90", 7290),
        ("1299", 129900),
        ("1,299.5", 129950),
        ("72.9", 7290),
        (" rm12 ", 1200),
        # JSON-LD often gives prices as numbers; callers pass str(value)
        (str(72.9), 7290),
    ],
)
def test_parse_ringgit_formats(text, sen):
    assert parse_ringgit(text) == sen


@pytest.mark.parametrize("text", ["", "free", "-5", "1.234", "RM", "1,2,3.00.0"])
def test_parse_ringgit_rejects(text):
    with pytest.raises(ValueError):
        parse_ringgit(text)


def test_format_sen():
    assert format_sen(129900) == "RM 1,299.00"
    assert format_sen(5) == "RM 0.05"
    assert format_sen(0) == "RM 0.00"
