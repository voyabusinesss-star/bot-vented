"""Tests UX embeds dashboard market-intel."""

from __future__ import annotations

from vinted_bot.services.market_embeds import (
    NicheCard,
    WindowPoint,
    build_analysis,
    build_badges,
    build_niche_dashboard_embed,
    build_recommendation,
    compute_gauges,
    gauge,
    score_color,
    sparkline,
)


def _sample_card(*, score: float = 82.0, listings: int = 24) -> NicheCard:
    windows = (
        WindowPoint("1d", 6, 12000, 3.0, 80.0, 4, 55.0),
        WindowPoint("7d", 18, 11500, 4.0, 78.0, 10, 50.0),
        WindowPoint("30d", listings, 11000, 5.0, score, 20, 48.0),
        WindowPoint("90d", 40, 10500, 6.0, 70.0, 35, 45.0),
    )
    return NicheCard(
        niche_key="salomon|xt_6|chaussure|",
        brand_slug="salomon",
        model_slug="xt_6",
        category_slug="chaussure",
        keyword_flags="",
        score=score,
        listing_count=listings,
        new_listings=10,
        disappeared_count=6,
        unique_sellers=5,
        price_min_cents=7000,
        price_max_cents=18000,
        price_mean_cents=12000,
        price_median_cents=11500,
        price_p25_cents=8500,
        median_ttl_days=4.0,
        margin_proxy_pct=55.0,
        volume_7d=10,
        volume_30d=20,
        rank=2,
        windows=windows,
        sample_size=listings,
    )


def test_gauge_and_sparkline() -> None:
    assert "92 %" in gauge(92)
    assert len(sparkline([1, 2, 3, 4, 5, 6, 7, 8])) == 8


def test_score_color_thresholds() -> None:
    assert score_color(95) == 0x2ECC71
    assert score_color(75) == 0xF1C40F
    assert score_color(55) == 0xE67E22
    assert score_color(40) == 0xE74C3C


def test_badges_and_analysis_are_data_driven() -> None:
    card = _sample_card()
    gauges = compute_gauges(card)
    badges = build_badges(card, gauges)
    analysis = build_analysis(card, gauges)
    assert badges
    assert analysis
    assert any("marge" in a.lower() or "demande" in a.lower() or "annonces" in a.lower() for a in analysis)


def test_recommendation_buy() -> None:
    card = _sample_card(score=85)
    gauges = compute_gauges(card)
    title, points = build_recommendation(card, gauges, "🟢 Faible")
    assert "Acheter" in title or "Surveiller" in title
    assert points


def test_dashboard_embed_structure() -> None:
    embed = build_niche_dashboard_embed(_sample_card())
    assert "Score IA" in embed["description"]
    assert embed["color"] == 0xF1C40F  # 82 → yellow
    names = {f["name"] for f in embed["fields"]}
    assert any("Jauges" in n for n in names)
    assert any("Analyse IA" in n for n in names)
    assert any("Recommandation" in n for n in names)
    assert any("Saturation" in n for n in names)
    assert "market-intel/" in embed["footer"]["text"]
