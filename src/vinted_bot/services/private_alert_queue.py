"""File d'attente DM filtres privés — n'bloque jamais la boucle scrape.

File mémoire rapide + spill Postgres si saturation (~80 %).
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

_QUEUE_MAX = 5000
_SPILL_THRESHOLD = int(_QUEUE_MAX * 0.8)  # spill dès 80 % plein
_queue: queue.Queue[Any] = queue.Queue(maxsize=_QUEUE_MAX)
_worker_started = False
_worker_lock = threading.Lock()
# (filter_id, vinted_id) déjà en file, spill, ou en cours d'envoi
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


def queue_size() -> int:
    return _queue.qsize()


def spill_pending_count() -> int:
    try:
        from sqlalchemy import func, select

        from vinted_bot.db.models import PrivateAlertOutbox
        from vinted_bot.db.session import session_scope

        with session_scope() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(PrivateAlertOutbox)
                    .where(PrivateAlertOutbox.status == "pending")
                )
                or 0
            )
    except Exception:  # noqa: BLE001
        return 0


def _spill_to_db(alert: QueuedPrivateAlert) -> bool:
    """Persiste l'alerte en outbox Postgres (anti-perte sous burst)."""
    try:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from vinted_bot.db.models import PrivateAlertOutbox
        from vinted_bot.db.session import session_scope

        with session_scope() as session:
            stmt = (
                pg_insert(PrivateAlertOutbox)
                .values(
                    filter_id=int(alert.filter_id),
                    discord_user_id=int(alert.discord_user_id),
                    vinted_id=int(alert.vinted_id),
                    listing_id=int(alert.listing_id or 0),
                    title=str(alert.title or "")[:255],
                    url=str(alert.url or ""),
                    payload_json=dict(alert.payload or {}),
                    status="pending",
                )
                .on_conflict_do_nothing(
                    constraint="uq_private_alert_outbox_filter_vinted"
                )
            )
            session.execute(stmt)
        log.info(
            "private_alert_spilled",
            filter_id=alert.filter_id,
            vinted_id=alert.vinted_id,
            queue_depth=_queue.qsize(),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "private_alert_spill_failed",
            filter_id=alert.filter_id,
            vinted_id=alert.vinted_id,
            error=str(exc)[:160],
        )
        return False


def enqueue_private_alert(alert: QueuedPrivateAlert) -> bool:
    """Ajoute une alerte à la file. Spill DB si file saturée. False si doublon."""
    if not _claim(alert.filter_id, alert.vinted_id):
        return False
    if _queue.qsize() >= _SPILL_THRESHOLD:
        ok = _spill_to_db(alert)
        if ok:
            # Garde le claim jusqu'à l'envoi depuis le spill (anti double DM).
            return True
    try:
        _queue.put_nowait(alert)
        return True
    except queue.Full:
        ok = _spill_to_db(alert)
        if not ok:
            _release(alert.filter_id, alert.vinted_id)
            log.warning("private_alert_queue_full", vinted_id=alert.vinted_id)
        return ok


def _alert_from_spill_row(row: Any) -> QueuedPrivateAlert:
    payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    return QueuedPrivateAlert(
        discord_user_id=int(row.discord_user_id),
        filter_id=int(row.filter_id),
        vinted_id=int(row.vinted_id),
        listing_id=int(row.listing_id or 0),
        title=str(row.title or "Annonce"),
        url=str(row.url or ""),
        payload=dict(payload),
    )


def _claim_next_spill() -> tuple[int, QueuedPrivateAlert] | None:
    """Prend 1 ligne pending (SKIP LOCKED) et passe en sending."""
    from sqlalchemy import select

    from vinted_bot.db.models import PrivateAlertOutbox
    from vinted_bot.db.session import session_scope

    with session_scope() as session:
        row = session.scalar(
            select(PrivateAlertOutbox)
            .where(PrivateAlertOutbox.status == "pending")
            .order_by(PrivateAlertOutbox.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        row.status = "sending"
        key = (int(row.filter_id), int(row.vinted_id))
        with _inflight_lock:
            _inflight.add(key)
        return int(row.id), _alert_from_spill_row(row)


def _mark_spill(row_id: int, *, status: str) -> None:
    from sqlalchemy import select

    from vinted_bot.db.models import PrivateAlertOutbox
    from vinted_bot.db.session import session_scope

    with session_scope() as session:
        row = session.scalar(
            select(PrivateAlertOutbox).where(PrivateAlertOutbox.id == int(row_id))
        )
        if row is not None:
            row.status = status


def _send_one(alert: QueuedPrivateAlert, notifier: Any, *, delay: float) -> None:
    from vinted_bot.db.session import session_scope
    from vinted_bot.db.user_filters import record_filter_alert

    title = (alert.title or "Annonce")[:80]
    content = f"🔔 [{title}]({alert.url})" if alert.url else f"🔔 **{title}**"
    notifier.send_dm_payload(
        alert.discord_user_id,
        {**alert.payload, "content": content},
    )
    try:
        with session_scope() as session:
            record_filter_alert(
                session,
                filter_id=alert.filter_id,
                discord_user_id=alert.discord_user_id,
                vinted_id=alert.vinted_id,
            )
    except Exception as exc:  # noqa: BLE001
        # Unique déjà présent = OK (retry / race)
        if "uq_user_filter_alert" not in str(exc).lower() and "unique" not in str(
            exc
        ).lower():
            raise
    log.info(
        "private_filter_dm_sent",
        discord_user_id=alert.discord_user_id,
        filter_id=alert.filter_id,
        vinted_id=alert.vinted_id,
        queue_remaining=_queue.qsize(),
        spill_pending=spill_pending_count(),
    )
    if delay > 0:
        time.sleep(delay)


def _worker_loop() -> None:
    from vinted_bot.config import get_settings
    from vinted_bot.notify.discord import DiscordNotifier

    log.info("private_alert_worker_start")
    settings = get_settings()
    delay = float(getattr(settings, "private_filter_dm_delay_seconds", 0.4) or 0.0)
    delay = max(0.0, min(delay, 5.0))

    with DiscordNotifier(settings) as notifier:
        while True:
            alert: QueuedPrivateAlert | None = None
            spill_id: int | None = None
            from_memory = False
            try:
                try:
                    alert = _queue.get(timeout=1.0)
                    from_memory = True
                except queue.Empty:
                    claimed = _claim_next_spill()
                    if claimed is None:
                        continue
                    spill_id, alert = claimed

                assert alert is not None
                try:
                    _send_one(alert, notifier, delay=delay)
                    if spill_id is not None:
                        _mark_spill(spill_id, status="sent")
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "private_filter_dm_failed",
                        discord_user_id=alert.discord_user_id,
                        filter_id=alert.filter_id,
                        vinted_id=alert.vinted_id,
                        error=str(exc)[:160],
                    )
                    if spill_id is not None:
                        _mark_spill(spill_id, status="pending")
                finally:
                    _release(alert.filter_id, alert.vinted_id)
                    if from_memory:
                        _queue.task_done()
            except Exception as exc:  # noqa: BLE001
                log.warning("private_alert_worker_loop_error", error=str(exc)[:160])
                time.sleep(1.0)


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
