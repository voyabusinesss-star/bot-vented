"""Tests métriques Chromium."""

from __future__ import annotations

from unittest.mock import patch

from vinted_bot.services.chromium_health import (
    chromium_process_snapshot,
    count_chromium_processes,
)


@patch("vinted_bot.services.chromium_health.subprocess.run")
def test_count_chromium_processes(mock_run) -> None:
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "101\n102\n103\n"
    assert count_chromium_processes() == 3


@patch("vinted_bot.services.chromium_health.count_chromium_processes", return_value=2)
def test_chromium_process_snapshot(mock_count) -> None:
    snap = chromium_process_snapshot()
    assert snap["chromium_processes"] == 2
    assert snap["python_threads"] >= 1
