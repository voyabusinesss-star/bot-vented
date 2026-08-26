"""Suivi des blocages catalog Vinted (403) pour auto-redeploy Railway."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

_lock = threading.Lock()


@dataclass
class ScrapeBlockTracker:
    consecutive_403: int = 0
    last_403_at: float | None = None
    last_catalog_ok_at: float | None = None
    total_403: int = 0
    _recent_403_at: list[float] = field(default_factory=list)

    def record_catalog_success(self) -> None:
        now = time.time()
        with _lock:
            self.consecutive_403 = 0
            self.last_catalog_ok_at = now

    def record_catalog_blocked(self, *, status: int, proxy_exhausted: bool) -> None:
        if proxy_exhausted or status != 403:
            return
        from vinted_bot.config import get_settings

        if get_settings().scrape_proxy_urls:
            return
        now = time.time()
        with _lock:
            self.consecutive_403 += 1
            self.total_403 += 1
            self.last_403_at = now
            self._recent_403_at.append(now)
            cutoff = now - 600.0
            self._recent_403_at = [t for t in self._recent_403_at if t >= cutoff]

    def recent_403_count(self, *, window_seconds: float = 600.0) -> int:
        now = time.time()
        cutoff = now - window_seconds
        with _lock:
            return sum(1 for t in self._recent_403_at if t >= cutoff)

    def snapshot(self) -> dict[str, Any]:
        with _lock:
            return {
                "consecutive_403": self.consecutive_403,
                "total_403": self.total_403,
                "last_403_at": self.last_403_at,
                "last_catalog_ok_at": self.last_catalog_ok_at,
                "recent_403_10m": sum(
                    1 for t in self._recent_403_at if t >= time.time() - 600.0
                ),
            }


_tracker = ScrapeBlockTracker()


def record_catalog_success() -> None:
    _tracker.record_catalog_success()


def record_catalog_blocked(*, status: int, proxy_exhausted: bool = False) -> None:
    _tracker.record_catalog_blocked(status=status, proxy_exhausted=proxy_exhausted)


def tracker_snapshot() -> dict[str, Any]:
    return _tracker.snapshot()


def consecutive_403_count() -> int:
    return _tracker.consecutive_403


def recent_403_count(*, window_seconds: float = 600.0) -> int:
    return _tracker.recent_403_count(window_seconds=window_seconds)
