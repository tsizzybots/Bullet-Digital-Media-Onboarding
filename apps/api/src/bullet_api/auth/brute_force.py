"""In-memory brute-force tracker for the login endpoint (S1-13).

Per the PRD / S1-13 spec: 5 failed login attempts in a 15-minute window
from the same IP triggers a 15-minute lockout. The 6th attempt returns
429 even if the credentials would otherwise be valid.

This implementation is in-memory by design for the first staging deploy:
Render's starter plan runs a single replica, restarts only on deploy,
and we want the dependency surface narrow while Sprint 1 lands. When
the API scales out (S2+), this is the file that gets swapped for a
Redis- or Postgres-backed equivalent.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_WINDOW = timedelta(minutes=15)


class BruteForceTracker:
    """Sliding-window per-IP failure counter.

    `clock` is injectable so tests can simulate the lockout window
    elapsing without `time.sleep(15 * 60)`.
    """

    def __init__(
        self,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        window: timedelta = DEFAULT_WINDOW,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._attempts: dict[str, deque[datetime]] = {}
        self._lock = Lock()
        self.max_attempts = max_attempts
        self.window = window
        self._clock = clock

    def _purge(self, ip: str, now: datetime) -> None:
        """Drop entries older than the window. Caller must hold the lock."""
        queue = self._attempts.get(ip)
        if not queue:
            return
        cutoff = now - self.window
        while queue and queue[0] < cutoff:
            queue.popleft()
        if not queue:
            self._attempts.pop(ip, None)

    def is_locked(self, ip: str) -> bool:
        now = self._clock()
        with self._lock:
            self._purge(ip, now)
            queue = self._attempts.get(ip)
            return queue is not None and len(queue) >= self.max_attempts

    def record_failure(self, ip: str) -> None:
        now = self._clock()
        with self._lock:
            self._purge(ip, now)
            queue = self._attempts.setdefault(ip, deque())
            queue.append(now)

    def clear(self, ip: str | None = None) -> None:
        """Reset state. Use `ip=None` to wipe everything (tests)."""
        with self._lock:
            if ip is None:
                self._attempts.clear()
            else:
                self._attempts.pop(ip, None)


# Module-level singleton consumed by the login route in production.
# Tests build their own instance and override the FastAPI dependency.
_default_tracker = BruteForceTracker()


def get_tracker() -> BruteForceTracker:
    """FastAPI dependency. Tests `app.dependency_overrides[get_tracker]`
    with a function returning a test-local tracker so each test starts
    from a clean state and can drive the clock forward."""
    return _default_tracker
