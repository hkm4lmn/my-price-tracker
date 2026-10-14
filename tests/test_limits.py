from app.bot.limits import CommandLimiter


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_limiter_blocks_21st_in_a_minute_then_recovers():
    clock = Clock()
    limiter = CommandLimiter(20, clock=clock)
    assert all(limiter.allow(1) for _ in range(20))
    assert limiter.allow(1) is False
    # Other users are unaffected.
    assert limiter.allow(2) is True
    clock.t += 61
    assert limiter.allow(1) is True


def test_warn_once_per_refusal_streak():
    clock = Clock()
    limiter = CommandLimiter(1, clock=clock)
    assert limiter.allow(1)
    assert not limiter.allow(1)
    assert limiter.should_warn(1) is True
    assert not limiter.allow(1)
    assert limiter.should_warn(1) is False
    clock.t += 61
    assert limiter.allow(1)
    assert not limiter.allow(1)
    assert limiter.should_warn(1) is True
