"""Rate limiting simple (délai entre requêtes)."""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = max(0.0, delay_seconds)
        self._last_at: float | None = None

    def wait(self) -> None:
        if self._last_at is None:
            self._last_at = time.monotonic()
            return
        elapsed = time.monotonic() - self._last_at
        remaining = self.delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_at = time.monotonic()
