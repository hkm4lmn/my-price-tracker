"""When to check a product next."""

import random
from datetime import datetime, timedelta

CHECK_INTERVAL = timedelta(hours=6)
# ±20%, so checks spread out instead of all hitting shops at the same moment.
JITTER = 0.2

_BACKOFF = [timedelta(minutes=30), timedelta(hours=2), timedelta(hours=6)]
_MAX_BACKOFF = timedelta(hours=24)


def next_check_after_success(now: datetime, rng: random.Random) -> datetime:
    return now + CHECK_INTERVAL * rng.uniform(1 - JITTER, 1 + JITTER)


def backoff_delay(consecutive_failures: int) -> timedelta:
    """30 min, 2 h, 6 h, then 24 h: never hammer a shop that is refusing us."""
    if consecutive_failures <= len(_BACKOFF):
        return _BACKOFF[max(consecutive_failures, 1) - 1]
    return _MAX_BACKOFF
