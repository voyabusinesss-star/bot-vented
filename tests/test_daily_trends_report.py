"""Tests rapport quotidien tendances (mouvements macro)."""

from __future__ import annotations

from vinted_bot.services.daily_trends_report import (
    DailyTrendItem,
    HistoryPoint,
    _event_badges,
    _headline_for,
    _priority_score,
    build_ai_narrative,
)
from vinted_bot.services.market_embeds import (
    build_daily_trend_card_embed,
    build_daily_trends_board_embed,
)
from vinted_bot.services.market_trends import MarketTrend, TrendTrigger


def _sample_trend(**overrides: object) -> MarketTrend:
    base = dict(
        entity_type="macro",
        entity_key="y2k",
        display_name="Style Y2K",
        title="Explosion — Style Y2K",
        strength=91.0,
        direction="up",
        lifecycle="growth",
        importance="critical",
        triggers=(
            TrendTrigger("volume_surge", "Hausse de demande estimée", "Volume up"),
            TrendTrigger("scarcity", "Rareté / stock bas", "Stock bas"),
        ),
        count_1d=2,
        count_7d=10,
        count_30d=12,
        count_90d=12,
        active_count=4,
        price_median_7d=35.0,
        price_median_30d=28.0,
        price_change_pct=25.0,
        disappeared_7d=5,
        median_ttl_7d_hours=2.0,
        median_ttl_30d_hours=8.0,
        rotation_change_pct=-60.0,
        stock_change_pct=-30.0,
        popularity_change_pct=90.0,
        gauge_growth=80.0,
        gauge_rentabilite=85.0,
        gauge_rarity=90.0,
        gauge_demand=88.0,
        gauge_saturation=20.0,
        continuation_pct=84.0,
        confidence_label="Confiance élevée",
        sample_titles=("Sac Y2K",),
        ai_analysis=("Hausse de la demande estimée", "Stock bas"),
        associated_niches=("Sac Diesel années 2000", "Lunettes Oakley archive"),
        related=("Sac Diesel années 2000", "Lunettes Oakley archive"),
        opportunity="Anticipation",
        why_it_matters="Y2K accélère",
        recommendation="buy",
        recommendation_detail="Acheter sous médiane",
        badges=("📈 Croissance", "🚨 Critique"),
    )
    base.update(overrides)
    return MarketTrend(**base)  # type: ignore[arg-type]


def test_headline_uses_macro_title() -> None:
    trend = _sample_trend()
    events = _event_badges(trend)
    assert _headline_for(trend, events) == "Explosion — Style Y2K"


def test_priority_prefers_high_edge() -> None:
    strong = _sample_trend(strength=95.0, gauge_saturation=10.0)
    saturated = _sample_trend(
        strength=90.0,
        gauge_saturation=90.0,
        lifecycle="saturation",
        importance="high",
    )
    assert _priority_score(strong) > _priority_score(saturated)


def test_ai_narrative_without_history() -> None:
    text = build_ai_narrative(_sample_trend(), [])
    assert "91" in text or "Y2K" in text
    assert len(text) > 40


def test_ai_narrative_with_history_points() -> None:
    text = build_ai_narrative(
        _sample_trend(),
        [HistoryPoint(strength=70.0, price_median_7d=28.0)],
    )
    assert "progression" in text or "signal suivi" in text
    assert "35" in text


def test_daily_board_and_card_embeds() -> None:
    trend = _sample_trend()
    item = DailyTrendItem(
        trend=trend,
        rank=1,
        medal="🥇",
        headline=trend.title,
        ai_narrative="Progression nette avec stock bas.",
        event_badges=("📈 Hausse de demande", "💎 Rareté importante"),
    )
    board = build_daily_trends_board_embed([item], day="2026-07-26")
    assert "TOP TENDANCES DU JOUR" in board["title"]
    assert "Y2K" in board["description"]
    assert "produits unitaires" in board["description"].lower() or "Pépites" in board["description"]

    card = build_daily_trend_card_embed(item)
    names = {f["name"] for f in card["fields"]}
    assert "🧠 Analyse IA" in names
    assert "🔎 Niches associées" in names
    assert "📊 Évolution du marché" in names
    assert "Diesel" in card["fields"][2]["value"]
