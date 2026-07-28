"""Tests pipeline permanent + filtre publication."""

from __future__ import annotations

from vinted_bot.services.opportunity_engine import (
    PUBLISH_MIN_SCORE,
    Opportunity,
    filter_publishable_opportunities,
)


def _op(score: float, *, priority: str = "interesting", key: str = "k") -> Opportunity:
    return Opportunity(
        niche_key=key,
        name="Test Niche",
        score=score,
        niche_type="hidden",
        niche_type_label="💎",
        priority=priority,
        priority_label="test",
        badges=(),
        price_buy_avg_eur=50.0,
        price_resell_avg_eur=90.0,
        price_max_eur=120.0,
        price_buy_max_eur=50.0,
        price_resell_target_eur=95.0,
        margin_eur=40.0,
        margin_pct=80.0,
        demand_score=70.0,
        rarity_score=60.0,
        competition_score=30.0,
        rotation_score=50.0,
        supply_ease_score=60.0,
        price_stability_score=60.0,
        confidence=60.0,
        unique_sellers=4,
        disappeared_pct=20.0,
        median_ttl_days=3.0,
        price_p75_eur=100.0,
        facts_line="n=10",
        multi_angle_composite=70.0,
        multi_angle_block="test",
        signals=("📈 demande en hausse",),
        angle_demand=70.0,
        angle_supply=60.0,
        angle_price=65.0,
        angle_behavioral=50.0,
        angle_emerging=55.0,
        angle_profitability=72.0,
        angle_anomaly=40.0,
        lifecycle="emerging",
        lifecycle_label="🌱 Émergence",
        lifecycle_avoid=False,
        depth_summary="test",
        weak_signal=True,
        weak_signal_summary="test",
        confidence_label="bonne",
        international=False,
        international_summary="—",
        explain_why="**Pourquoi** : test",
        explain_signals="**Signaux** : test",
        explain_strategy="**Stratégie** : test",
        why_short="test",
        ai_analysis="test",
        strategy_where="x",
        strategy_buy="y",
        strategy_sell="z",
        action="watch",
        action_detail="d",
        photo_url=None,
        brand_slug="test",
        model_slug="model",
        category_slug="veste",
        keyword_flags="",
        search_terms=("test",),
        sample_size=10,
        listing_count=10,
        disappeared_count=2,
    )


def test_publish_min_score_constant() -> None:
    assert PUBLISH_MIN_SCORE >= 55.0


def test_filter_publishable_keeps_interesting_only() -> None:
    ops = [
        _op(50.0, priority="weak", key="a"),
        _op(64.0, priority="interesting", key="b"),
        _op(70.0, priority="strong", key="c"),
        _op(90.0, priority="exceptional", key="d"),
    ]
    kept = filter_publishable_opportunities(ops)
    keys = {o.niche_key for o in kept}
    assert "a" not in keys
    assert "b" not in keys  # sous le seuil 65
    assert "c" in keys
    assert "d" in keys


def test_detector_module_imports() -> None:
    from vinted_bot.services.detector_pipeline import (
        run_detector_cycle,
        run_detector_loop,
    )

    assert callable(run_detector_cycle)
    assert callable(run_detector_loop)
