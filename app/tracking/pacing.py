"""Being polite to shops: at most one request per host every few seconds."""

import time
from collections.abc import Callable


class HostPacer:
    def __init__(
        self,
        min_gap_seconds: float = 3.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._gap = min_gap_seconds
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            remaining = self._gap - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last[host] = self._clock()
