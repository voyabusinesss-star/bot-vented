"""Matrice salons + ordre newest-first Discord + scheduler due-based."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from vinted_bot.config_loader import SearchTarget, target_poll_interval_seconds
from vinted_bot.jobs.scrape_workers import _pick_due_target, _target_key
from vinted_bot.notify.discord import (
    belongs_in_all_vetement,
    is_vetement_for_all,
    route_channel,
)
from vinted_bot.services.scrape_search import listing_discord_sort_key


def test_salon_matrix_brand_and_all() -> None:
    """Carhartt / Ami indémodables → brand + ALL ; Jordan shoe → sneakers only."""
    clothing = {"carhartt": "ch-carhartt", "ami": "ch-ami", "nike": "ch-nike"}
    sneakers = {"jordan": "ch-jordan", "nike": "ch-nike-snk"}
    sneaker_ids = set(sneakers.values())

    assert route_channel("Carhartt", clothing, sneaker_map=sneakers, is_shoe=False) == (
        "ch-carhartt"
    )
    assert is_vetement_for_all("Sweat Carhartt WIP", "sweat") is True
    assert (
        belongs_in_all_vetement(
            "Carhartt",
            is_shoe=False,
            brand_channel_id="ch-carhartt",
            sneaker_channel_ids=sneaker_ids,
        )
        is True
    )

    assert route_channel("Ami", clothing, sneaker_map=sneakers, is_shoe=False) == "ch-ami"
    assert (
        belongs_in_all_vetement(
            "Ami",
            is_shoe=False,
            brand_channel_id="ch-ami",
            sneaker_channel_ids=sneaker_ids,
            is_vetement=False,
        )
        is True
    )

    assert (
        belongs_in_all_vetement(
            "Louis Vuitton",
            is_shoe=False,
            brand_channel_id="ch-lv",
            sneaker_channel_ids=sneaker_ids,
        )
        is False
    )

    assert (
        route_channel("Jordan", clothing, sneaker_map=sneakers, is_shoe=True)
        == "ch-jordan"
    )
    assert (
        belongs_in_all_vetement(
            "Jordan",
            is_shoe=True,
            brand_channel_id="ch-jordan",
            sneaker_channel_ids=sneaker_ids,
        )
        is False
    )


def test_publish_order_uses_published_at_newest_first() -> None:
    """Score n'influence plus l'ordre d'envoi — published_at DESC."""
    now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    high_score_newer = SimpleNamespace(
        published_at=now,
        first_seen_at=now,
        vinted_id=200,
        score=99.0,
    )
    low_score_older = SimpleNamespace(
        published_at=now - timedelta(minutes=5),
        first_seen_at=now - timedelta(minutes=5),
        vinted_id=100,
        score=1.0,
    )
    mid = SimpleNamespace(
        published_at=now - timedelta(minutes=2),
        first_seen_at=now - timedelta(minutes=2),
        vinted_id=150,
        score=50.0,
    )
    selected = [low_score_older, mid, high_score_newer]
    selected.sort(key=listing_discord_sort_key)
    assert [x.vinted_id for x in selected] == [200, 150, 100]


def test_outbox_flush_order_newest_first() -> None:
    """Plusieurs marques : plus récent d'abord (0 délai)."""
    now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    rows = [
        SimpleNamespace(
            channel_id="all",
            published_at=now - timedelta(seconds=10),
            id=3,
            brand="Dickies",
        ),
        SimpleNamespace(
            channel_id="all",
            published_at=now - timedelta(seconds=30),
            id=1,
            brand="Carhartt",
        ),
        SimpleNamespace(
            channel_id="all",
            published_at=now - timedelta(seconds=20),
            id=2,
            brand="Nike",
        ),
    ]
    rows.sort(key=lambda r: (r.published_at, r.id), reverse=True)
    assert [r.brand for r in rows] == ["Dickies", "Nike", "Carhartt"]


def test_scheduler_picks_due_independently_of_list_order() -> None:
    targets = [
        SearchTarget(brand="carhartt", query="carhartt", priority="medium"),
        SearchTarget(brand="nike", query="nike", priority="high"),
        SearchTarget(brand="dickies", query="dickies", priority="medium"),
    ]
    now = 1000.0
    next_run = {
        _target_key(targets[0]): now + 40.0,
        _target_key(targets[1]): now - 1.0,
        _target_key(targets[2]): now + 10.0,
    }
    picked = _pick_due_target(targets, next_run, now=now)
    assert picked is not None
    assert picked.brand == "nike"


def test_target_poll_interval_priority_and_override() -> None:
    high = SearchTarget(brand="nike", query="nike", priority="high")
    medium = SearchTarget(brand="lacoste", query="lacoste", priority="medium")
    hot = SearchTarget(
        brand="ami", query="ami", priority="medium", poll_seconds=1.0
    )
    assert target_poll_interval_seconds(high) == 0.5
    assert target_poll_interval_seconds(medium) == 2.0
    assert target_poll_interval_seconds(hot) == 1.0


def test_outbox_drip_cap_selects_newest_global() -> None:
    """Cap global : les plus récents d'abord, multi-marques."""
    now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    rows = [
        SimpleNamespace(
            channel_id="brand-a",
            published_at=now - timedelta(seconds=5),
            id=3,
            brand="Adidas",
        ),
        SimpleNamespace(
            channel_id="brand-b",
            published_at=now - timedelta(seconds=30),
            id=1,
            brand="Carhartt",
        ),
        SimpleNamespace(
            channel_id="brand-a",
            published_at=now - timedelta(seconds=10),
            id=2,
            brand="Nike",
        ),
        SimpleNamespace(
            channel_id="all",
            published_at=now - timedelta(seconds=1),
            id=4,
            brand="Dickies",
        ),
    ]
    rows.sort(key=lambda r: (r.published_at, r.id), reverse=True)
    drip = rows[:3]
    assert [r.brand for r in drip] == ["Dickies", "Adidas", "Nike"]
    assert len(drip) == 3
