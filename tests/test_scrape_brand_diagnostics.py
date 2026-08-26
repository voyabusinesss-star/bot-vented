"""Tests diagnostic par marque (logging only)."""

from __future__ import annotations

from vinted_bot.services.scrape_brand_diagnostics import (
    maybe_emit_summary,
    record_scrape_error,
    record_scrape_start,
    record_scrape_success,
    reset_for_tests,
)


def setup_function() -> None:
    reset_for_tests()


def test_summary_ranks_slow_revisit() -> None:
    record_scrape_start(
        brand="nike",
        worker_id=0,
        seconds_since_last=10.0,
        due_targets=3,
        overdue_seconds=1.0,
        activity_hotness=200.0,
    )
    record_scrape_success(
        brand="nike",
        worker_id=0,
        duration_seconds=0.5,
        seconds_since_last=10.0,
    )
    record_scrape_start(
        brand="givenchy",
        worker_id=0,
        seconds_since_last=400.0,
        due_targets=1,
        overdue_seconds=50.0,
        activity_hotness=20.0,
    )
    record_scrape_success(
        brand="givenchy",
        worker_id=0,
        duration_seconds=0.6,
        seconds_since_last=400.0,
    )
    record_scrape_error(
        brand="givenchy",
        worker_id=0,
        duration_seconds=1.0,
        error="catalog blocked (403)",
        seconds_since_last=400.0,
    )
    maybe_emit_summary(worker_id=0, force=True)
