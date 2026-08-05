"""File d'attente DM filtres privés — n'bloque jamais la boucle scrape."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

_queue: queue.Queue[Any] = queue.Queue(maxsize=5000)
_worker_started = False
_worker_lock = threading.Lock()
# (filter_id, vinted_id) déjà en file ou en cours d'envoi
_inflight: set[tuple[int, int]] = set()
_inflight_lock = threading.Lock()


@dataclass(slots=True)
class QueuedPrivateAlert:
    """Payload sérialisable pour envoi DM hors scrape."""

    discord_user_id: int
    filter_id: int
    vinted_id: int
    listing_id: int
    title: str
    url: str
    payload: dict[str, Any]


def _claim(filter_id: int, vinted_id: int) -> bool:
    key = (int(filter_id), int(vinted_id))
    with _inflight_lock:
        if key in _inflight:
            return False
        _inflight.add(key)
        return True


def _release(filter_id: int, vinted_id: int) -> None:
    key = (int(filter_id), int(vinted_id))
    with _inflight_lock:
        _inflight.discard(key)


def enqueue_private_alert(alert: QueuedPrivateAlert) -> bool:
    """Ajoute une alerte à la file. False si doublon / file pleine."""
    if not _claim(alert.filter_id, alert.vinted_id):
        return False
    try:
        _queue.put_nowait(alert)
        return True
    except queue.Full:
        _release(alert.filter_id, alert.vinted_id)
        log.warning("private_alert_queue_full", vinted_id=alert.vinted_id)
        return False


def queue_size() -> int:
    return _queue.qsize()


def _worker_loop() -> None:
    from vinted_bot.config import get_settings
    from vinted_bot.db.session import session_scope
    from vinted_bot.db.user_filters import record_filter_alert
    from vinted_bot.notify.discord import DiscordNotifier

    log.info("private_alert_worker_start")
    settings = get_settings()
    # Délai court entre DM (rate-limit Discord) — défaut ~0.4s, jamais 60s
    delay = float(getattr(settings, "private_filter_dm_delay_seconds", 0.4) or 0.0)
    delay = max(0.0, min(delay, 5.0))

    with DiscordNotifier(settings) as notifier:
        while True:
            alert: QueuedPrivateAlert = _queue.get()
            try:
                title = (alert.title or "Annonce")[:80]
                content = (
                    f"🔔 [{title}]({alert.url})"
                    if alert.url
                    else f"🔔 **{title}**"
                )
                notifier.send_dm_payload(
                    alert.discord_user_id,
                    {**alert.payload, "content": content},
                )
                with session_scope() as session:
                    record_filter_alert(
                        session,
                        filter_id=alert.filter_id,
                        discord_user_id=alert.discord_user_id,
                        vinted_id=alert.vinted_id,
                    )
                log.info(
                    "private_filter_dm_sent",
                    discord_user_id=alert.discord_user_id,
                    filter_id=alert.filter_id,
                    vinted_id=alert.vinted_id,
                    queue_remaining=_queue.qsize(),
                )
                if delay > 0:
                    time.sleep(delay)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "private_filter_dm_failed",
                    discord_user_id=alert.discord_user_id,
                    filter_id=alert.filter_id,
                    vinted_id=alert.vinted_id,
                    error=str(exc)[:160],
                )
            finally:
                # Succès : already_alerted en DB → safe de libérer.
                # Échec : libère pour retry au prochain match.
                _release(alert.filter_id, alert.vinted_id)
                _queue.task_done()


def ensure_private_alert_worker() -> None:
    """Démarre le worker une seule fois (thread daemon)."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(
            target=_worker_loop,
            name="private-alert-dm-worker",
            daemon=True,
        )
        thread.start()
        _worker_started = True
        log.info("private_alert_worker_spawned")
