"""Heartbeat scrape partagé (fichier) pour le healthcheck Railway."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

HEARTBEAT_PATH = Path(
    os.environ.get("SCRAPE_HEARTBEAT_PATH", "/tmp/vinted_scrape_heartbeat.json")
)


def write_scrape_heartbeat(**fields: Any) -> None:
    payload = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **{k: v for k, v in fields.items() if v is not None},
    }
    try:
        HEARTBEAT_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def read_scrape_heartbeat() -> dict[str, Any] | None:
    try:
        raw = HEARTBEAT_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def scrape_health_line() -> str:
    data = read_scrape_heartbeat()
    if not data:
        return "scrape=unknown"
    ts = data.get("ts")
    try:
        age = max(0, int(time.time() - float(ts)))
    except (TypeError, ValueError):
        age = -1
    posted = data.get("posted")
    cycle = data.get("cycle")
    outbox_pending = data.get("outbox_pending")
    outbox_lag = data.get("outbox_lag_seconds")
    extra = ""
    if outbox_pending is not None:
        extra += f" outbox_pending={outbox_pending}"
    if outbox_lag is not None:
        extra += f" outbox_lag_s={outbox_lag}"
    return f"scrape=ok age_s={age} cycle={cycle} posted={posted}{extra}"
