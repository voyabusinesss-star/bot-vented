"""Tests outbox retry + preview + métriques (stabilisation scrape)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from vinted_bot.db.models import DiscordOutbox, Listing
from vinted_bot.jobs.discord_outbox import (
    KIND_PREVIEW,
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    discord_outbox_stats,
    enqueue_bot_preview_from_candidates,
    requeue_retryable_failed_outbox,
)


def _listing(listing_id: int = 1, vinted_id: int = 100) -> Listing:
    now = datetime.now(timezone.utc)
    return Listing(
        id=listing_id,
        vinted_id=vinted_id,
        brand="nike",
        title="test",
        price_cents=5000,
        currency="EUR",
        url="https://vinted.fr/items/100",
        published_at=now,
        first_seen_at=now,
    )


@patch("vinted_bot.jobs.discord_outbox.session_scope")
def test_requeue_retryable_failed_outbox(mock_scope: MagicMock) -> None:
    session = MagicMock()
    mock_scope.return_value.__enter__.return_value = session
    row = MagicMock()
    row.status = OUTBOX_STATUS_FAILED
    row.listing_id = 1
    row.channel_id = "123"
    row.kind = "brand"
    row.id = 99
    session.scalars.return_value.all.return_value = [row]
    session.scalar.return_value = None

    count = requeue_retryable_failed_outbox(retry_after_seconds=60.0, limit=10)

    assert count == 1
    assert row.status == OUTBOX_STATUS_PENDING


@patch("vinted_bot.jobs.discord_outbox.session_scope")
def test_discord_outbox_stats(mock_scope: MagicMock) -> None:
    session = MagicMock()
    mock_scope.return_value.__enter__.return_value = session
    session.scalar.side_effect = [3, 1, datetime.now(timezone.utc) - timedelta(seconds=45)]

    stats = discord_outbox_stats()

    assert stats["outbox_pending"] == 3
    assert stats["outbox_failed"] == 1
    assert stats["outbox_lag_seconds"] >= 44.0


@patch("vinted_bot.jobs.discord_outbox.pick_diverse_preview_listing")
@patch("vinted_bot.jobs.discord_outbox.session_scope")
@patch("vinted_bot.jobs.discord_outbox.get_settings")
def test_enqueue_bot_preview_from_candidates(
    mock_settings: MagicMock,
    mock_scope: MagicMock,
    mock_pick: MagicMock,
) -> None:
    cfg = MagicMock()
    cfg.bot_preview_via_outbox = True
    cfg.discord_channel_bot_preview = "1531568901652086794"
    cfg.bot_preview_interval_seconds = 150.0
    mock_settings.return_value = cfg

    session = MagicMock()
    mock_scope.return_value.__enter__.return_value = session
    session.scalar.side_effect = [None, None]  # no pending preview, no duplicate
    listing = _listing()
    mock_pick.return_value = listing

    with patch("vinted_bot.notify.discord._last_bot_preview_post_at", 0.0):
        ok = enqueue_bot_preview_from_candidates([listing])

    assert ok is True
    session.add.assert_called_once()
    added = session.add.call_args[0][0]
    assert isinstance(added, DiscordOutbox)
    assert added.kind == KIND_PREVIEW
    assert added.status == OUTBOX_STATUS_PENDING
