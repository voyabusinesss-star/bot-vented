"""Tests analyse multi-angle."""

from __future__ import annotations

from types import SimpleNamespace

from vinted_bot.services.multi_angle import (
    aggregate_engagement,
    compute_multi_angle,
    extract_engagement,
)


def test_extract_engagement_from_raw() -> None:
    listing = SimpleNamespace(
        raw_json={"favourite_count": 12, "view_count": 40}
    )
    fav, views = extract_engagement(listing)
    assert fav == 12
    assert views == 40


def test_aggregate_engagement() -> None:
    listings = [
        SimpleNamespace(raw_json={"favourite_count": 10, "view_count": 20}),
        SimpleNamespace(raw_json={"favourite_count": 0, "view_count": 10}),
    ]
    agg = aggregate_engagement(listings)
    assert agg["favourite_avg"] == 5.0
    assert agg["view_avg"] == 15.0


def test_multi_angle_emits_distinct_scores_and_signals() -> None:
    snap = SimpleNamespace(
        listing_count=12,
        disappeared_count=6,
        unique_sellers=3,
        new_listings=8,
        margin_proxy_pct=70.0,
        median_ttl_days=2.0,
        price_median_cents=12000,
        price_p25_cents=7000,
        price_mean_cents=12500,
        metrics={"favourite_avg": 4.0, "view_avg": 25.0, "active_count": 8},
    )
    w7 = SimpleNamespace(
        new_listings=8,
        listing_count=10,
        price_median_cents=11000,
        disappeared_count=5,
    )
    w30 = SimpleNamespace(
        new_listings=12,
        listing_count=20,
        price_median_cents=13000,
        disappeared_count=6,
    )
    report = compute_multi_angle(
        snap,
        windows={"7d": w7, "30d": w30, "1d": w7},
        obscure_brand=True,
    )
    assert report.demand.score != report.supply.score
    assert report.profitability.score > 40
    assert report.composite > 0
    assert report.signals  # au moins un signal sur ce profil fort
    block = report.embed_block()
    assert "Demande" in block
    assert "Anomalies" in block
