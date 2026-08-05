"""Tests file d'attente DM filtres privés."""

from vinted_bot.services.private_alert_queue import (
    QueuedPrivateAlert,
    enqueue_private_alert,
    queue_size,
    _inflight,
    _inflight_lock,
    _queue,
    _release,
)


def _reset_queue() -> None:
    while True:
        try:
            _queue.get_nowait()
            _queue.task_done()
        except Exception:
            break
    with _inflight_lock:
        _inflight.clear()


def test_enqueue_dedupes_same_filter_listing() -> None:
    _reset_queue()
    a = QueuedPrivateAlert(
        discord_user_id=1,
        filter_id=10,
        vinted_id=99,
        listing_id=1,
        title="Test",
        url="https://example.com",
        payload={},
    )
    assert enqueue_private_alert(a) is True
    assert enqueue_private_alert(a) is False
    assert queue_size() == 1
    _release(10, 99)
    # Après release (échec send), on peut requeue
    assert enqueue_private_alert(a) is True
    _reset_queue()
