"""Where "now" comes from.

Logic takes a Clock instead of calling datetime.now(), so tests can control
time (backoff, cooldowns, the 7-day dead rule) without sleeping.
"""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
