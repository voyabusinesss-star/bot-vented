"""Tests scoring / embeds market-intel (sans DB)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from vinted_bot.db.repositories import extract_seller_id
from vinted_bot.services.market_intel import (
    PepiteAlert,
    _metrics_for_bucket,
    build_pepite_embed,
    build_rankings_embed,
    build_stats_embed,
    compute_niche_score,
    niche_key_for_listing,
)


def test_extract_seller_id() -> None:
    assert extract_seller_id({"user": {"id": 42, "login": "bob"}}) == 42
    assert extract_seller_id({"seller": {"user_id": "99"}}) == 99
    assert extract_seller_id({}) is None


def test_metrics_window_excludes_stale_actives() -> None:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    fresh = SimpleNamespace(
        first_seen_at=now - timedelta(days=2),
        last_seen_at=now - timedelta(days=1),
        scraped_at=now - timedelta(days=2),
        disappeared_at=None,
        is_active=True,
        price_cents=5000,
        seller_id=1,
        published_at=now - timedelta(days=2),
    )
    stale_active = SimpleNamespace(
        first_seen_at=now - timedelta(days=40),
        last_seen_at=now - timedelta(days=30),
        scraped_at=now - timedelta(days=40),
        disappeared_at=None,
        is_active=True,
        price_cents=6000,
        seller_id=2,
        published_at=now - timedelta(days=40),
    )
    metrics = _metrics_for_bucket(
        "carhartt|detroit_jacket|veste|",
        "7d",
        [fresh, stale_active],
        cutoff=cutoff,
        volume_7d=1,
        volume_30d=2,
    )
    assert metrics is not None
    assert metrics.listing_count == 1
    assert metrics.new_listings == 1


def test_compute_niche_score_requires_margin() -> None:
    assert (
        compute_niche_score(
            margin_proxy_pct=10.0,
            median_ttl_days=3.0,
            new_listings=20,
            unique_sellers=5,
            brand_slug="carhartt",
            model_slug="detroit_jacket",
            volume_7d=8,
            volume_30d=20,
            disappeared_count=5,
        )
        is None
    )


def test_compute_niche_score_is_out_of_100() -> None:
    score = compute_niche_score(
        margin_proxy_pct=50.0,
        median_ttl_days=4.0,
        new_listings=20,
        unique_sellers=5,
        brand_slug="carhartt",
        model_slug="detroit_jacket",
        volume_7d=10,
        volume_30d=20,
        disappeared_count=5,
        listing_count=20,
    )
    assert score is not None
    assert 0 < score <= 100


def test_margin_is_clipped_at_150() -> None:
    high = compute_niche_score(
        margin_proxy_pct=900.0,
        median_ttl_days=5.0,
        new_listings=20,
        unique_sellers=5,
        brand_slug="carhartt",
        model_slug="detroit_jacket",
        volume_7d=10,
        volume_30d=20,
        disappeared_count=5,
        listing_count=20,
    )
    capped = compute_niche_score(
        margin_proxy_pct=150.0,
        median_ttl_days=5.0,
        new_listings=20,
        unique_sellers=5,
        brand_slug="carhartt",
        model_slug="detroit_jacket",
        volume_7d=10,
        volume_30d=20,
        disappeared_count=5,
        listing_count=20,
    )
    assert high is not None and capped is not None
    assert high == capped
    assert high <= 100


def test_saturated_brand_without_model_scores_lower() -> None:
    base = dict(
        margin_proxy_pct=55.0,
        median_ttl_days=5.0,
        new_listings=30,
        unique_sellers=8,
        volume_7d=12,
        volume_30d=30,
        disappeared_count=6,
        listing_count=30,
    )
    generic = compute_niche_score(brand_slug="nike", model_slug=None, **base)
    specific = compute_niche_score(brand_slug="nike", model_slug="dunk_sb", **base)
    assert generic is not None and specific is not None
    assert specific > generic


def test_underexploited_mid_volume_beats_mega_trend() -> None:
    """Le détecteur privilégie les niches moyennes, pas les bestsellers volume."""
    common = dict(
        margin_proxy_pct=70.0,
        median_ttl_days=4.0,
        unique_sellers=5,
        brand_slug="dickies",
        model_slug="dickies_874",
        disappeared_count=4,
    )
    mid = compute_niche_score(
        new_listings=12,
        volume_7d=5,
        volume_30d=14,
        listing_count=14,
        **common,
    )
    mega = compute_niche_score(
        new_listings=120,
        volume_7d=40,
        volume_30d=140,
        listing_count=140,
        **common,
    )
    assert mid is not None and mega is not None
    assert mid > mega


def test_niche_key_for_listing() -> None:
    listing = type(
        "L",
        (),
        {
            "brand": "Nike",
            "model_slug": "dunk_sb",
            "category_slug": "chaussure",
            "keyword_slugs": ["og", "rare"],
        },
    )()
    assert niche_key_for_listing(listing) == "nike|dunk_sb|chaussure|og+rare"


def test_build_rankings_embed() -> None:
    embed = build_rankings_embed(title="Top", lines=["**1.** Test"])
    assert embed["title"] == "Top"
    assert "Test" in embed["description"]


def test_build_stats_embed() -> None:
    embed = build_stats_embed(
        {
            "listings_total": 100,
            "listings_active": 40,
            "listings_new_24h": 12,
            "brands": 20,
            "models": 8,
            "niches": 50,
            "niches_scored": 15,
            "top_brands": [("carhartt", 72.0, 30)],
            "top_categories": [("hoodie", 60.0, 10)],
            "emerging": [],
        }
    )
    assert "Tableau de bord" in embed["title"]
    assert "100" in embed["description"]


def test_build_pepite_embed() -> None:
    from unittest.mock import patch

    alert = PepiteAlert(
        vinted_id=1,
        title="Carhartt Detroit Jacket",
        url="https://www.vinted.fr/items/1",
        price_cents=2500,
        brand="Carhartt",
        model_slug="detroit_jacket",
        category_slug="veste",
        size="M",
        photo_url="https://example.com/p.jpg",
        niche_score=70.0,
        pepite_score=78.0,
        resell_cents=5500,
        margin_proxy_pct=120.0,
        niche_label="Carhartt · Detroit Jacket · Veste",
    )
    with patch(
        "vinted_bot.services.market_intel.load_niche_windows", return_value=()
    ):
        embed = build_pepite_embed(alert)
    assert "Pépite" in embed["title"]
    assert embed["thumbnail"]["url"].endswith("p.jpg")
    assert any("Niche source" in f["name"] for f in embed["fields"])
