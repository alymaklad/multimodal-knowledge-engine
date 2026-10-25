"""A fixed-window rate limiter (architecture section 47).

Deliberately in-process: it protects a single instance from a runaway client with no extra
infrastructure. It does **not** coordinate across workers — with N processes the effective limit is
N times the configured one — so a multi-instance deployment needs the same counter in Redis. That
tradeoff is stated here rather than discovered in production.

Disabled by default (`RATE_LIMIT_PER_MINUTE=0`).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict


class RateLimiter:
    def __init__(self, limit_per_minute: int, window_seconds: int = 60) -> None:
        self._limit = limit_per_minute
        self._window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = defaultdict(list)

    @property
    def enabled(self) -> bool:
        return self._limit > 0

    def check(self, key: str, *, now: float | None = None) -> tuple[bool, int, int]:
        """Return (allowed, remaining, retry_after_seconds)."""
        if not self.enabled:
            return True, 0, 0
        moment = time.monotonic() if now is None else now
        with self._lock:
            recent = [stamp for stamp in self._hits[key] if moment - stamp < self._window]
            if len(recent) >= self._limit:
                self._hits[key] = recent
                retry_after = max(1, int(self._window - (moment - recent[0])))
                return False, 0, retry_after
            recent.append(moment)
            self._hits[key] = recent
            return True, self._limit - len(recent), 0
