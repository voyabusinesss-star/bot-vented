"""Tests reconcile / backfill présence."""

from __future__ import annotations

from vinted_bot.db.repositories import backfill_listing_presence_signals, extract_seller_id


def test_extract_seller_from_raw_for_backfill() -> None:
    assert extract_seller_id({"user": {"id": 7}}) == 7


def test_backfill_presence_is_importable() -> None:
    assert callable(backfill_listing_presence_signals)
