"""Converting between ringgit text and integer sen.

Prices are kept as whole sen inside the app so totals never drift the way
floats do; these two functions are the only places that convert.
"""

import re
from decimal import Decimal, InvalidOperation

# RM 10 million. Anything above is a parsing mistake or a hostile page, and
# would overflow the integer price columns.
MAX_PRICE_SEN = 1_000_000_000

_NUMBER = re.compile(r"^\d{1,3}(,\d{3})*(\.\d{1,2})?$|^\d+(\.\d{1,2})?$")


def parse_ringgit(text: str) -> int:
    """Parse "RM1,299.00", "72.9" or "1299" into sen. Raises ValueError."""
    cleaned = text.strip()
    if cleaned[:2].upper() == "RM":
        cleaned = cleaned[2:].strip()
    if not _NUMBER.match(cleaned):
        raise ValueError(f"not a ringgit amount: {text!r}")
    try:
        amount = Decimal(cleaned.replace(",", ""))
    except InvalidOperation as e:
        raise ValueError(f"not a ringgit amount: {text!r}") from e
    return int(amount * 100)


def format_sen(sen: int) -> str:
    """Format sen for people: 129900 -> "RM 1,299.00"."""
    return f"RM {sen // 100:,}.{sen % 100:02d}"
