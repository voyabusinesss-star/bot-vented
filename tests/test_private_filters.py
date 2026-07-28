"""Tests filtres privés (matching, sans Discord)."""

from types import SimpleNamespace

from vinted_bot.db.user_filters import PLAN_LIMITS, normalize_plan, plan_filter_limit
from vinted_bot.services.private_filters import (
    build_private_alert_embed,
    listing_matches_filter,
)


def test_plan_limits() -> None:
    assert plan_filter_limit("starter") == 5
    assert plan_filter_limit("premium") == 20
    assert plan_filter_limit("elite") is None
    assert normalize_plan("PREMIUM") == "premium"
    assert set(PLAN_LIMITS) == {"starter", "premium", "elite"}


def test_listing_matches_brand_model_price() -> None:
    filt = SimpleNamespace(
        brand="Nike",
        model="TN",
        category=None,
        keyword=None,
        max_price_eur=50.0,
        min_price_eur=None,
    )
    listing = SimpleNamespace(
        title="Nike Air Max Plus TN size 42",
        brand="Nike",
        model_slug="air_max_plus_tn",
        category_slug="chaussure",
        price_cents=3500,
    )
    assert listing_matches_filter(listing, filt) is True

    listing.price_cents = 8000
    assert listing_matches_filter(listing, filt) is False


def test_listing_matches_category_plural() -> None:
    filt = SimpleNamespace(
        brand="Nike",
        model="TN",
        category="chaussures",
        keyword="tn",
        max_price_eur=50.0,
        min_price_eur=None,
    )
    ok = SimpleNamespace(
        title="Nike TN 42",
        brand="Nike",
        model_slug="tn",
        category_slug="chaussures",
        price_cents=4000,
    )
    bad_price = SimpleNamespace(
        title="Nike TN 42",
        brand="Nike",
        model_slug="tn",
        category_slug="chaussures",
        price_cents=8000,
    )
    bad_brand = SimpleNamespace(
        title="Adidas TN fake",
        brand="Adidas",
        model_slug="tn",
        category_slug="chaussures",
        price_cents=4000,
    )
    assert listing_matches_filter(ok, filt) is True
    assert listing_matches_filter(bad_price, filt) is False
    assert listing_matches_filter(bad_brand, filt) is False


def test_listing_matches_keyword_only() -> None:
    filt = SimpleNamespace(
        brand=None,
        model=None,
        category=None,
        keyword="Jellycat",
        max_price_eur=None,
        min_price_eur=None,
    )
    listing = SimpleNamespace(
        title="Peluche Jellycat bunny",
        brand="",
        model_slug=None,
        category_slug="jouet",
        price_cents=2000,
    )
    assert listing_matches_filter(listing, filt) is True


def test_private_alert_embed_is_dm_shaped() -> None:
    from datetime import datetime, timezone

    listing = SimpleNamespace(
        title="Nike Air Max Plus TN",
        brand="Nike",
        price_cents=3500,
        url="https://www.vinted.fr/items/1",
        photos=[],
        raw_json={"created_at_ts": 1_720_000_000},
        vinted_id=1,
        published_at=datetime(2024, 7, 3, 12, 0, tzinfo=timezone.utc),
        first_seen_at=None,
    )
    match = SimpleNamespace(
        filter_id=3,
        discord_user_id=123,
        listing=listing,
        signal="Sous-évalué · Publié récemment",
        market_eur=90.0,
        display_number=1,
    )
    embed = build_private_alert_embed(match)
    assert embed["title"] == "Nike Air Max Plus TN"
    assert embed["url"] == "https://www.vinted.fr/items/1"
    assert "35 €" in embed["description"]
    assert "Ajoutée" in embed["description"]
    assert "03/07/2024" in embed["description"]
    assert "Nike" in embed["description"]
    assert "Filtre #1" in embed["footer"]["text"]
    assert "90 €" in embed["description"]

    from vinted_bot.services.private_filters import build_private_alert_payload

    payload = build_private_alert_payload(match)
    labels = [c["label"] for c in payload["components"][0]["components"]]
    assert labels == ["📄 Détails", "🤝 Négocier", "💳 Acheter"]


def test_is_fresh_listing_recent_only() -> None:
    from datetime import datetime, timedelta, timezone

    from vinted_bot.services.private_filters import is_fresh_listing

    now = datetime.now(timezone.utc)
    fresh = SimpleNamespace(
        published_at=now - timedelta(seconds=30),
        first_seen_at=now,
        raw_json={},
    )
    stale = SimpleNamespace(
        published_at=now - timedelta(hours=2),
        first_seen_at=now - timedelta(hours=2),
        raw_json={},
    )
    assert is_fresh_listing(fresh, max_age_seconds=180) is True
    assert is_fresh_listing(stale, max_age_seconds=180) is False
    # Annonce Vinted ancienne, même si le bot la voit maintenant → pas de backfill
    rediscovered = SimpleNamespace(
        published_at=now - timedelta(hours=2),
        first_seen_at=now - timedelta(seconds=10),
        raw_json={},
    )
    assert is_fresh_listing(rediscovered, max_age_seconds=180) is False
    # Sans date Vinted : first_seen récent OK
    no_pub = SimpleNamespace(
        published_at=None,
        first_seen_at=now - timedelta(seconds=10),
        raw_json={},
    )
    assert is_fresh_listing(no_pub, max_age_seconds=180) is True
