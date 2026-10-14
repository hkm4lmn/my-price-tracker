"""Per-user command rate limit.

Kept in memory: the web app runs as a single process, so one process sees
every command. Restarting it resets the counts, which is harmless.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable

WINDOW_SECONDS = 60.0


class CommandLimiter:
    def __init__(self, per_minute: int = 20, clock: Callable[[], float] = time.monotonic):
        self._limit = per_minute
        self._clock = clock
        self._seen: dict[int, deque[float]] = defaultdict(deque)
        self._warned: set[int] = set()

    def allow(self, chat_id: int) -> bool:
        now = self._clock()
        seen = self._seen[chat_id]
        while seen and now - seen[0] >= WINDOW_SECONDS:
            seen.popleft()
        if len(seen) >= self._limit:
            return False
        seen.append(now)
        self._warned.discard(chat_id)
        return True

    def should_warn(self, chat_id: int) -> bool:
        """True the first time a user is refused, so we say "slow down" once."""
        if chat_id in self._warned:
            return False
        self._warned.add(chat_id)
        return True
