"""Matrice salons + ordre chrono Discord + scheduler due-based."""

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
    """Carhartt sweat → brand + ALL ; Ami sac → brand only ; Jordan shoe → sneakers."""
    clothing = {"carhartt": "ch-carhartt", "ami": "ch-ami", "nike": "ch-nike"}
    sneakers = {"jordan": "ch-jordan", "nike": "ch-nike-snk"}
    sneaker_ids = set(sneakers.values())

    # Carhartt sweat → salon marque + ALL
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
            is_vetement=True,
        )
        is True
    )

    # Ami sac → salon marque OK, pas ALL (non-vêtement)
    assert route_channel("Ami", clothing, sneaker_map=sneakers, is_shoe=False) == "ch-ami"
    assert is_vetement_for_all("Sac bandoulière Ami Paris", None) is False
    assert (
        belongs_in_all_vetement(
            "Ami",
            is_shoe=False,
            brand_channel_id="ch-ami",
            sneaker_channel_ids=sneaker_ids,
            is_vetement=False,
        )
        is False
    )

    # Jordan shoe → sneakers, jamais ALL
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
            is_vetement=False,
        )
        is False
    )


def test_publish_order_uses_published_at_not_score() -> None:
    """Score n'influence plus l'ordre d'envoi — published_at ASC."""
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
    # Sélection simulée triée par score DESC puis réordonnée pour envoi
    selected = [high_score_newer, mid, low_score_older]
    selected.sort(key=listing_discord_sort_key)
    assert [x.vinted_id for x in selected] == [100, 150, 200]


def test_outbox_channel_flush_order_by_published_at() -> None:
    """Plusieurs marques dans le même salon : ordre chronologique published_at."""
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
    rows.sort(key=lambda r: (r.channel_id, r.published_at, r.id))
    assert [r.brand for r in rows] == ["Carhartt", "Nike", "Dickies"]


def test_scheduler_picks_due_independently_of_list_order() -> None:
    """Une marque high due n'attend pas le tour séquentiel des autres."""
    targets = [
        SearchTarget(brand="carhartt", query="carhartt", priority="medium"),
        SearchTarget(brand="nike", query="nike", priority="high"),
        SearchTarget(brand="dickies", query="dickies", priority="medium"),
    ]
    now = 1000.0
    next_run = {
        _target_key(targets[0]): now + 40.0,  # carhartt not due
        _target_key(targets[1]): now - 1.0,  # nike due
        _target_key(targets[2]): now + 10.0,  # dickies not due
    }
    picked = _pick_due_target(targets, next_run, now=now)
    assert picked is not None
    assert picked.brand == "nike"


def test_target_poll_interval_priority_and_override() -> None:
    high = SearchTarget(brand="nike", query="nike", priority="high")
    medium = SearchTarget(brand="lacoste", query="lacoste", priority="medium")
    hot = SearchTarget(
        brand="ami", query="ami", priority="medium", poll_seconds=12.0
    )
    assert target_poll_interval_seconds(high) == 20.0
    assert target_poll_interval_seconds(medium) == 45.0
    assert target_poll_interval_seconds(hot) == 12.0
