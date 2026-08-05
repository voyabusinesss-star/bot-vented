"""Plafond ~10 détections Discord / heure."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from vinted_bot.services import opportunity_engine as oe


def test_detector_hourly_posts_remaining_empty():
    with patch.object(oe, "session_scope"), patch.object(
        oe, "get_checkpoint", return_value={}
    ):
        assert oe.detector_hourly_posts_remaining(max_per_hour=10) == 10


def test_detector_never_reposts_same_key():
    from vinted_bot.services.opportunity_engine import _is_recently_posted_key

    assert _is_recently_posted_key(
        "old-niche", {"old-niche": "2020-01-01T00:00:00+00:00"}
    )
    assert not _is_recently_posted_key("fresh", {})
