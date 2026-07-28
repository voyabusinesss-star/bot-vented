"""Tests radar tendances = mouvements de marché (pas produits)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from vinted_bot.services.market_embeds import build_daily_trend_card_embed
from vinted_bot.services.daily_trends_report import DailyTrendItem
from vinted_bot.services.market_trends import (
    MarketTrend,
    TrendThresholds,
    TrendTrigger,
    _EntityBucket,
    _LiteListing,
    build_macro_trend_title,
    evaluate_entity,
    is_vague_alone,
    load_trend_config,
    match_topics,
)


def test_load_macro_topics() -> None:
    load_trend_config.cache_clear()
    thr, topics = load_trend_config()
    by_slug = {t.slug: t for t in topics}
    assert "y2k" in by_slug
    assert by_slug["y2k"].standalone_ok
    assert by_slug["sac"].standalone_ok is False
    assert "sac" in thr.vague_alone_slugs


def test_vague_alone_rejected() -> None:
    load_trend_config.cache_clear()
    thr, topics = load_trend_config()
    by_slug = {t.slug: t for t in topics}
    assert is_vague_alone(by_slug["sac"], thr)
    assert is_vague_alone(by_slug["vintage_obj"], thr)
    assert not is_vague_alone(by_slug["y2k"], thr)


def test_match_y2k_and_sac_combo_context() -> None:
    load_trend_config.cache_clear()
    _, topics = load_trend_config()
    found = match_topics("Sac Diesel vintage Y2K années 2000", topics)
    slugs = {t.slug for t in found}
    assert "y2k" in slugs
    assert "sac" in slugs


def test_macro_title() -> None:
    title = build_macro_trend_title(
        "Style Y2K",
        popularity_change_pct=85.0,
        price_change_pct=12.0,
        lifecycle="growth",
        codes={"volume_surge"},
    )
    assert title.startswith("Explosion")


def test_evaluate_entity_volume_surge() -> None:
    now = datetime.now(timezone.utc)
    thr = TrendThresholds(min_samples_short=3, min_samples_long=3, volume_surge_ratio=1.5)
    listings = []
    for i in range(12):
        listings.append(
            _LiteListing(
                title=f"Sac Y2K {i}",
                brand="Diesel",
                price_cents=2500 + i * 100,
                size=None,
                is_active=True,
                first_seen_at=now - timedelta(days=1 + (i % 5)),
                published_at=now - timedelta(days=1 + (i % 5)),
                scraped_at=now,
                last_seen_at=now,
                disappeared_at=None,
            )
        )
    bucket = _EntityBucket("macro", "y2k", "Style Y2K", listings)
    trend = evaluate_entity(
        bucket,
        now=now,
        thr=thr,
        related=("Diesel · Sacs", "Sac baguette vintage"),
    )
    assert trend is not None
    assert trend.title
    assert trend.associated_niches
    assert trend.entity_type == "macro"


def test_daily_card_shows_niches_not_product() -> None:
    trend = MarketTrend(
        entity_type="macro",
        entity_key="y2k",
        display_name="Style Y2K",
        title="Explosion — Style Y2K",
        strength=88.0,
        direction="up",
        lifecycle="growth",
        importance="high",
        triggers=(
            TrendTrigger("volume_surge", "Hausse de demande estimée", "Volume ×2"),
        ),
        count_1d=3,
        count_7d=20,
        count_30d=30,
        count_90d=40,
        active_count=12,
        price_median_7d=45.0,
        price_median_30d=38.0,
        price_change_pct=18.0,
        disappeared_7d=8,
        median_ttl_7d_hours=3.0,
        median_ttl_30d_hours=8.0,
        rotation_change_pct=-50.0,
        stock_change_pct=-25.0,
        popularity_change_pct=42.0,
        gauge_growth=80.0,
        gauge_rentabilite=75.0,
        gauge_rarity=60.0,
        gauge_demand=85.0,
        gauge_saturation=30.0,
        continuation_pct=78.0,
        confidence_label="Confiance élevée",
        sample_titles=(),
        ai_analysis=("Hausse de la demande",),
        associated_niches=("Sac Diesel années 2000", "Lunettes Oakley archive"),
        related=("Sac Diesel années 2000", "Lunettes Oakley archive"),
        opportunity="Potentiel fort",
        why_it_matters="Y2K accélère",
        recommendation="buy",
        recommendation_detail="Rechercher maintenant",
        badges=("📈 Croissance",),
    )
    item = DailyTrendItem(
        trend=trend,
        rank=1,
        medal="🥇",
        headline=trend.title,
        ai_narrative="Le style Y2K progresse avec stock en baisse.",
        event_badges=("📈 Hausse de demande",),
    )
    embed = build_daily_trend_card_embed(item)
    names = {f["name"] for f in embed["fields"]}
    assert "🔎 Niches associées" in names
    assert "📊 Évolution du marché" in names
    assert "Diesel" in embed["fields"][2]["value"]
    assert "Mouvement" in embed["description"]
