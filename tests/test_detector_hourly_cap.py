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


def test_detector_hourly_posts_remaining_counts_last_hour():
    now = datetime.now(timezone.utc)
    ats = [(now - timedelta(minutes=m)).isoformat() for m in (5, 15, 70)]
    with patch.object(oe, "session_scope"), patch.object(
        oe, "get_checkpoint", return_value={"ats": ats}
    ):
        # 70 min ago hors fenêtre → 2 posts comptés → 8 restants
        assert oe.detector_hourly_posts_remaining(max_per_hour=10) == 8
