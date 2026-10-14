import random
from datetime import UTC, datetime, timedelta

from app.tracking.schedule import backoff_delay, next_check_after_success

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def test_backoff_steps():
    assert backoff_delay(1) == timedelta(minutes=30)
    assert backoff_delay(2) == timedelta(hours=2)
    assert backoff_delay(3) == timedelta(hours=6)
    assert backoff_delay(4) == timedelta(hours=24)
    assert backoff_delay(50) == timedelta(hours=24)


def test_next_check_jitter_bounds():
    rng = random.Random(42)
    delays = [next_check_after_success(NOW, rng) - NOW for _ in range(1000)]
    assert min(delays) >= timedelta(hours=4, minutes=48)
    assert max(delays) <= timedelta(hours=7, minutes=12)
    # Actually spread out, not a constant 6 hours.
    assert max(delays) - min(delays) > timedelta(hours=2)
