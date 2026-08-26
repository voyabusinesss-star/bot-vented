"""Métriques processus Chromium / threads Python (diagnostic fuites Railway)."""

from __future__ import annotations

import subprocess
import threading
from typing import Any

from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

_CHROME_PGREP_PATTERN = r"chrome|chromium|playwright"


def count_chromium_processes() -> int:
    """Nombre de process OS liés à Chrome/Chromium/Playwright (-1 si indisponible)."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", _CHROME_PGREP_PATTERN],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if result.returncode not in (0, 1):
            return -1
        lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
        return len(lines)
    except Exception:  # noqa: BLE001
        return -1


def chromium_process_snapshot() -> dict[str, Any]:
    return {
        "chromium_processes": count_chromium_processes(),
        "python_threads": threading.active_count(),
    }


def log_chromium_health(*, event: str = "periodic", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = chromium_process_snapshot()
    payload = {"event": event, **snap}
    if extra:
        payload.update(extra)
    log.info("chromium_health", **payload)
    return snap
