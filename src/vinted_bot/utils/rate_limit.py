"""Rate limiting avec jitter (anti-ban Vinted)."""

from __future__ import annotations

import random
import time


class RateLimiter:
    def __init__(
        self,
        delay_seconds: float,
        *,
        jitter_ratio: float = 0.35,
    ) -> None:
        self.delay_seconds = max(0.0, delay_seconds)
        self.jitter_ratio = max(0.0, min(1.0, jitter_ratio))
        self._last_at: float | None = None
        self._cooldown_until: float = 0.0

    def penalize(self, seconds: float) -> None:
        """Backoff après 429 / ban soft — pause obligatoire."""
        self._cooldown_until = max(
            self._cooldown_until,
            time.monotonic() + max(0.0, seconds),
        )

    def wait(self) -> None:
        now = time.monotonic()
        if now < self._cooldown_until:
            time.sleep(self._cooldown_until - now)
            now = time.monotonic()

        base = self.delay_seconds
        if self.jitter_ratio > 0 and base > 0:
            spread = base * self.jitter_ratio
            target = base + random.uniform(-spread, spread)
            target = max(0.15, target)
        else:
            target = base

        if self._last_at is not None:
            elapsed = now - self._last_at
            remaining = target - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_at = time.monotonic()
