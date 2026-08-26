"""Tests du tracker 403 catalog (auto-redeploy Railway)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from vinted_bot.config import Settings
from vinted_bot.services import scrape_block_tracker as sbt
from vinted_bot.services.scrape_block_tracker import (
    consecutive_403_count,
    record_catalog_blocked,
    record_catalog_success,
    tracker_snapshot,
)


@pytest.fixture(autouse=True)
def _reset_tracker() -> None:
    with sbt._lock:
        sbt._tracker.consecutive_403 = 0
        sbt._tracker.last_403_at = None
        sbt._tracker.last_catalog_ok_at = None
        sbt._tracker.total_403 = 0
        sbt._tracker._recent_403_at.clear()
        sbt._tracker.consecutive_thread_limit = 0
        sbt._tracker.total_thread_limit = 0
        sbt._tracker.last_thread_limit_at = None
        sbt._tracker.last_thread_limit_chrome_processes = None
        sbt._tracker.last_thread_limit_python_threads = None
        sbt._tracker._recent_thread_limit_at.clear()
    yield


def test_record_thread_limit_and_reset_on_scrape_success() -> None:
    from vinted_bot.services.scrape_block_tracker import (
        consecutive_thread_limit_count,
        record_scrape_cycle_success,
        record_thread_limit_error,
    )

    record_thread_limit_error(
        error="can't start new thread",
        chrome_processes=12,
        python_threads=48,
    )
    assert consecutive_thread_limit_count() == 1
    snap = tracker_snapshot()
    assert snap["last_thread_limit_chrome_processes"] == 12
    record_scrape_cycle_success()
    assert consecutive_thread_limit_count() == 0


def test_record_catalog_success_resets_consecutive_403() -> None:
    record_catalog_blocked(status=403)
    assert consecutive_403_count() == 1
    record_catalog_success()
    assert consecutive_403_count() == 0
    snap = tracker_snapshot()
    assert snap["last_catalog_ok_at"] is not None


def test_record_catalog_blocked_ignores_non_403() -> None:
    record_catalog_blocked(status=429)
    record_catalog_blocked(status=403, proxy_exhausted=True)
    assert consecutive_403_count() == 0


@patch("vinted_bot.config.get_settings")
def test_record_catalog_blocked_skips_when_proxy_configured(mock_settings) -> None:
    mock_settings.return_value = Settings(scrape_proxy_urls=["http://u:p@proxy:80"])
    record_catalog_blocked(status=403)
    assert consecutive_403_count() == 0


def test_recent_403_window() -> None:
    for _ in range(3):
        record_catalog_blocked(status=403)
    snap = tracker_snapshot()
    assert snap["consecutive_403"] == 3
    assert snap["recent_403_10m"] == 3
    assert snap["total_403"] == 3
