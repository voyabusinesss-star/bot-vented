"""Diagnostic par marque : fréquence de passage et temps de traitement.

Logging only — ne modifie pas le scheduling. Permet de distinguer :
- marques peu revisitées (position / hotness scheduler)
- marques lentes à traiter (volume / pagination)
- marques avec erreurs répétées (403, timeout, etc.)
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

_SUMMARY_INTERVAL_S = 300.0
_MAX_SAMPLES = 40


@dataclass
class _BrandDiag:
    brand: str
    pass_count: int = 0
    error_count: int = 0
    forced_by_ceiling_count: int = 0
    interval_samples: deque[float] = field(default_factory=lambda: deque(maxlen=_MAX_SAMPLES))
    duration_samples: deque[float] = field(default_factory=lambda: deque(maxlen=_MAX_SAMPLES))
    last_started_ts: float | None = None
    last_started_iso: str | None = None
    last_finished_ts: float | None = None
    last_error: str | None = None
    last_error_at: float | None = None


_lock = threading.Lock()
_by_brand: dict[str, _BrandDiag] = {}
_last_summary_at = 0.0


def _iso(ts: float | None = None) -> str:
    t = ts if ts is not None else time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _stats(d: _BrandDiag, *, now: float) -> dict[str, Any]:
    intervals = list(d.interval_samples)
    durations = list(d.duration_samples)
    mean_interval = sum(intervals) / len(intervals) if intervals else None
    mean_duration = sum(durations) / len(durations) if durations else None
    p95_interval = None
    if intervals:
        sorted_i = sorted(intervals)
        p95_interval = sorted_i[min(len(sorted_i) - 1, int(len(sorted_i) * 0.95))]
    last_pass_ago = (
        round(now - d.last_finished_ts, 1) if d.last_finished_ts is not None else None
    )
    return {
        "brand": d.brand,
        "pass_count": d.pass_count,
        "error_count": d.error_count,
        "forced_by_ceiling_count": d.forced_by_ceiling_count,
        "mean_interval_s": round(mean_interval, 1) if mean_interval is not None else None,
        "p95_interval_s": round(p95_interval, 1) if p95_interval is not None else None,
        "mean_duration_s": round(mean_duration, 2) if mean_duration is not None else None,
        "last_pass_ago_s": last_pass_ago,
        "last_error": (d.last_error or "")[:80] or None,
    }


def _get(brand: str) -> _BrandDiag:
    key = brand.strip().lower()
    if key not in _by_brand:
        _by_brand[key] = _BrandDiag(brand=brand)
    return _by_brand[key]


def record_scrape_start(
    *,
    brand: str,
    worker_id: int,
    seconds_since_last: float | None,
    due_targets: int,
    overdue_seconds: float,
    activity_hotness: float,
    scheduled_poll_seconds: float | None = None,
    forced_by_ceiling: bool = False,
) -> None:
    now = time.time()
    iso = _iso(now)
    with _lock:
        diag = _get(brand)
        diag.last_started_ts = now
        diag.last_started_iso = iso
    log.info(
        "brand_scrape_start",
        worker_id=worker_id,
        brand=brand,
        started_iso=iso,
        seconds_since_last_scrape=(
            round(seconds_since_last, 1) if seconds_since_last is not None else None
        ),
        due_targets=due_targets,
        overdue_seconds=round(overdue_seconds, 1),
        activity_hotness=round(activity_hotness, 1),
        scheduled_poll_seconds=scheduled_poll_seconds,
        forced_by_ceiling=forced_by_ceiling,
    )


def record_scrape_success(
    *,
    brand: str,
    worker_id: int,
    duration_seconds: float,
    seconds_since_last: float | None,
    forced_by_ceiling: bool = False,
) -> None:
    now = time.time()
    with _lock:
        diag = _get(brand)
        diag.pass_count += 1
        if forced_by_ceiling:
            diag.forced_by_ceiling_count += 1
        diag.last_finished_ts = now
        if seconds_since_last is not None:
            diag.interval_samples.append(float(seconds_since_last))
        diag.duration_samples.append(float(duration_seconds))
        pass_count = diag.pass_count
    log.info(
        "brand_scrape_finish",
        worker_id=worker_id,
        brand=brand,
        duration_seconds=round(duration_seconds, 2),
        seconds_since_last_scrape=(
            round(seconds_since_last, 1) if seconds_since_last is not None else None
        ),
        pass_count=pass_count,
        forced_by_ceiling=forced_by_ceiling,
    )
    maybe_emit_summary(worker_id=worker_id)


def record_scrape_error(
    *,
    brand: str,
    worker_id: int,
    duration_seconds: float,
    error: str,
    seconds_since_last: float | None = None,
    forced_by_ceiling: bool = False,
) -> None:
    now = time.time()
    with _lock:
        diag = _get(brand)
        diag.error_count += 1
        diag.last_error = error[:200]
        diag.last_error_at = now
        if seconds_since_last is not None:
            diag.interval_samples.append(float(seconds_since_last))
        diag.duration_samples.append(float(duration_seconds))
        error_count = diag.error_count
    log.warning(
        "brand_scrape_error",
        worker_id=worker_id,
        brand=brand,
        duration_seconds=round(duration_seconds, 2),
        seconds_since_last_scrape=(
            round(seconds_since_last, 1) if seconds_since_last is not None else None
        ),
        error_count=error_count,
        error=error[:160],
        forced_by_ceiling=forced_by_ceiling,
    )
    maybe_emit_summary(worker_id=worker_id)


def maybe_emit_summary(*, worker_id: int, force: bool = False) -> None:
    global _last_summary_at
    now = time.time()
    with _lock:
        if not force and now - _last_summary_at < _SUMMARY_INTERVAL_S:
            return
        _last_summary_at = now
        rows = [_stats(d, now=now) for d in _by_brand.values()]
    if not rows:
        return
    rows.sort(
        key=lambda r: (
            -(r["mean_interval_s"] or 0.0),
            -(r["error_count"] or 0),
            r["brand"] or "",
        )
    )
    slowest = rows[:12]
    most_errors = sorted(rows, key=lambda r: -r["error_count"])[:8]
    ceiling_forced = sorted(
        rows,
        key=lambda r: -(r["forced_by_ceiling_count"] or 0),
    )[:8]
    total_ceiling = sum(r["forced_by_ceiling_count"] or 0 for r in rows)
    log.info(
        "scrape_brand_diagnostics_summary",
        worker_id=worker_id,
        brand_count=len(rows),
        forced_by_ceiling_total=total_ceiling,
        most_ceiling_forced=ceiling_forced,
        slowest_revisit=slowest,
        most_errors=most_errors,
        hint=(
            "mean_interval_s élevé → marque peu repassée (scheduler/hotness) ; "
            "forced_by_ceiling_count → scrape forcé par SCRAPE_MAX_REVISIT_SECONDS ; "
            "mean_duration_s élevé → traitement lent (volume/page) ; "
            "error_count → retries/backoff"
        ),
    )


def reset_for_tests() -> None:
    global _last_summary_at
    with _lock:
        _by_brand.clear()
        _last_summary_at = 0.0
