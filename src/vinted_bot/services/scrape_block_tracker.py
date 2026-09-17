"""Suivi blocages scrape (403 Vinted, thread limit) pour auto-redeploy Railway."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

_lock = threading.Lock()
_BLOCK_TRACKER_CHECKPOINT = "scrape_ops:block_tracker"
_RECENT_WINDOW_SECONDS = 600.0


def is_thread_limit_error(exc: BaseException | str) -> bool:
    msg = str(exc).lower()
    return "can't start new thread" in msg or "thread limit" in msg


@dataclass
class ScrapeBlockTracker:
    consecutive_403: int = 0
    last_403_at: float | None = None
    last_catalog_ok_at: float | None = None
    total_403: int = 0
    _recent_403_at: list[float] = field(default_factory=list)

    consecutive_thread_limit: int = 0
    total_thread_limit: int = 0
    last_thread_limit_at: float | None = None
    last_thread_limit_chrome_processes: int | None = None
    last_thread_limit_python_threads: int | None = None
    _recent_thread_limit_at: list[float] = field(default_factory=list)

    def record_catalog_success(self) -> None:
        now = time.time()
        with _lock:
            self.consecutive_403 = 0
            self.last_catalog_ok_at = now
            _persist_block_tracker_locked()

    def record_scrape_cycle_success(self) -> None:
        """Reset compteur thread limit après un scrape marque réussi."""
        with _lock:
            self.consecutive_thread_limit = 0

    def record_catalog_blocked(self, *, status: int, proxy_exhausted: bool) -> None:
        if proxy_exhausted:
            return
        # 403/404 Vinted + 401 session morte → même recovery (redeploy IP)
        if status not in {401, 403, 404}:
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
            cutoff = now - _RECENT_WINDOW_SECONDS
            self._recent_403_at = [t for t in self._recent_403_at if t >= cutoff]
            _persist_block_tracker_locked()

    def record_thread_limit_error(
        self,
        *,
        error: str,
        chrome_processes: int | None = None,
        python_threads: int | None = None,
    ) -> None:
        if not is_thread_limit_error(error):
            return
        now = time.time()
        with _lock:
            self.consecutive_thread_limit += 1
            self.total_thread_limit += 1
            self.last_thread_limit_at = now
            self.last_thread_limit_chrome_processes = chrome_processes
            self.last_thread_limit_python_threads = python_threads
            self._recent_thread_limit_at.append(now)
            cutoff = now - 600.0
            self._recent_thread_limit_at = [
                t for t in self._recent_thread_limit_at if t >= cutoff
            ]

    def recent_403_count(self, *, window_seconds: float = 600.0) -> int:
        now = time.time()
        cutoff = now - window_seconds
        with _lock:
            return sum(1 for t in self._recent_403_at if t >= cutoff)

    def recent_thread_limit_count(self, *, window_seconds: float = 600.0) -> int:
        now = time.time()
        cutoff = now - window_seconds
        with _lock:
            return sum(
                1 for t in self._recent_thread_limit_at if t >= cutoff
            )

    def snapshot(self) -> dict[str, Any]:
        with _lock:
            now = time.time()
            return {
                "consecutive_403": self.consecutive_403,
                "total_403": self.total_403,
                "last_403_at": self.last_403_at,
                "last_catalog_ok_at": self.last_catalog_ok_at,
                "recent_403_10m": sum(
                    1 for t in self._recent_403_at if t >= now - 600.0
                ),
                "consecutive_thread_limit": self.consecutive_thread_limit,
                "total_thread_limit": self.total_thread_limit,
                "last_thread_limit_at": self.last_thread_limit_at,
                "last_thread_limit_chrome_processes": (
                    self.last_thread_limit_chrome_processes
                ),
                "last_thread_limit_python_threads": (
                    self.last_thread_limit_python_threads
                ),
                "recent_thread_limit_10m": sum(
                    1 for t in self._recent_thread_limit_at if t >= now - 600.0
                ),
            }


_tracker = ScrapeBlockTracker()


def _persist_block_tracker_locked() -> None:
    """Persiste le compteur blocages — survit aux redeploy Railway (Postgres)."""
    try:
        from vinted_bot.db.repositories import set_checkpoint
        from vinted_bot.db.session import session_scope

        with session_scope() as session:
            set_checkpoint(
                session,
                _BLOCK_TRACKER_CHECKPOINT,
                {
                    "consecutive_403": _tracker.consecutive_403,
                    "total_403": _tracker.total_403,
                    "last_403_at": _tracker.last_403_at,
                    "recent_403_at": list(_tracker._recent_403_at),
                },
            )
    except Exception:  # noqa: BLE001
        pass


def restore_block_tracker_from_checkpoint() -> None:
    """Recharge le compteur après redeploy (sinon reset mémoire = jamais le seuil)."""
    try:
        from vinted_bot.db.repositories import get_checkpoint
        from vinted_bot.db.session import session_scope

        with session_scope() as session:
            data = get_checkpoint(session, _BLOCK_TRACKER_CHECKPOINT)
        if not data:
            return
        now = time.time()
        cutoff = now - _RECENT_WINDOW_SECONDS
        raw_recent = data.get("recent_403_at") or []
        recent = [float(t) for t in raw_recent if float(t) >= cutoff]
        with _lock:
            _tracker.consecutive_403 = int(data.get("consecutive_403") or 0)
            _tracker.total_403 = int(data.get("total_403") or 0)
            last = data.get("last_403_at")
            _tracker.last_403_at = float(last) if last is not None else None
            _tracker._recent_403_at = recent
    except Exception:  # noqa: BLE001
        pass


def record_catalog_success() -> None:
    _tracker.record_catalog_success()


def record_scrape_cycle_success() -> None:
    _tracker.record_scrape_cycle_success()


def record_catalog_blocked(*, status: int, proxy_exhausted: bool = False) -> None:
    _tracker.record_catalog_blocked(status=status, proxy_exhausted=proxy_exhausted)


def record_thread_limit_error(
    *,
    error: str,
    chrome_processes: int | None = None,
    python_threads: int | None = None,
) -> None:
    _tracker.record_thread_limit_error(
        error=error,
        chrome_processes=chrome_processes,
        python_threads=python_threads,
    )


def tracker_snapshot() -> dict[str, Any]:
    return _tracker.snapshot()


def consecutive_403_count() -> int:
    return _tracker.consecutive_403


def consecutive_thread_limit_count() -> int:
    return _tracker.consecutive_thread_limit


def recent_403_count(*, window_seconds: float = 600.0) -> int:
    return _tracker.recent_403_count(window_seconds=window_seconds)


def recent_thread_limit_count(*, window_seconds: float = 600.0) -> int:
    return _tracker.recent_thread_limit_count(window_seconds=window_seconds)
