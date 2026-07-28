"""Tests insights 12–18 (cycle, profondeur, signaux faibles, etc.)."""

from __future__ import annotations

from vinted_bot.services.niche_insights import (
    build_mandatory_explanation,
    classify_lifecycle,
    compute_confidence_insight,
    detect_international,
    detect_weak_signals,
    extract_depth_profile,
    learning_score_adjustment,
)
from vinted_bot.services.niche_insights import (
    ConfidenceInsight,
    DepthProfile,
    InternationalInsight,
    LifecycleInsight,
    WeakSignalInsight,
)


def test_lifecycle_saturated_avoided() -> None:
    life = classify_lifecycle(
        listing_count=60,
        sellers=20,
        demand_delta=5,
        demand_score=40,
        competition_score=70,
        price_delta=0,
        volume_delta=0,
        famous=True,
        obscure=False,
    )
    assert life.stage == "saturated"
    assert life.avoid


def test_lifecycle_emerging() -> None:
    life = classify_lifecycle(
        listing_count=10,
        sellers=3,
        demand_delta=50,
        demand_score=65,
        competition_score=25,
        price_delta=5,
        volume_delta=40,
        famous=False,
        obscure=True,
    )
    assert life.stage == "emerging"
    assert not life.avoid


def test_depth_extracts_variants() -> None:
    depth = extract_depth_profile(
        [
            "Carhartt Detroit Jacket marron vintage 2004 taille M collab",
            "Detroit Jacket brown L limited edition",
        ]
    )
    assert "marron" in depth.colors or "brown" in " ".join(depth.colors)
    assert depth.sizes
    assert depth.has_edition or depth.has_collab or depth.years
    assert depth.variant_count >= 1


def test_weak_signal_priority() -> None:
    weak = detect_weak_signals(
        demand_delta=45,
        competition_score=28,
        listing_count=12,
        emerging_score=60,
        obscure=True,
        lifecycle="emerging",
    )
    assert weak.is_weak_signal
    assert weak.score >= 45


def test_confidence_labels() -> None:
    hi = compute_confidence_insight(
        listing_count=20,
        sellers=6,
        disappeared=5,
        has_ttl=True,
        has_engagement=True,
        titles_sampled=10,
        confidence_raw=80,
    )
    assert hi.label in {"bonne", "élevée"}
    lo = compute_confidence_insight(
        listing_count=4,
        sellers=0,
        disappeared=0,
        has_ttl=False,
        has_engagement=False,
        titles_sampled=1,
        confidence_raw=40,
    )
    assert lo.label in {"faible", "moyenne"}


def test_international_detection() -> None:
    intl = detect_international(
        ["Jellycat Bashful Bunny Japan exclusive deadstock"],
        brand="jellycat",
    )
    assert intl.is_international
    assert intl.markets


def test_mandatory_explanation_has_three_parts() -> None:
    expl = build_mandatory_explanation(
        name="Test",
        lifecycle=LifecycleInsight("growth", "📈 Croissance", False, "ok"),
        why_core="forte demande",
        signals=("📈 demande en hausse",),
        weak=WeakSignalInsight(50, True, "précoce", ("précoce",)),
        depth=DepthProfile((), (), (), False, False, 0, "peu de variantes"),
        confidence=ConfidenceInsight(70, "bonne", ("n=12",)),
        intl=InternationalInsight(0, False, (), "—"),
        strategy_buy="acheter ≤ 50€",
        strategy_sell="revendre 90€",
        action_detail="alerter",
        avoid_saturated=False,
    )
    assert "Pourquoi" in expl.why
    assert "Signaux" in expl.signals
    assert "Stratégie" in expl.strategy


def test_learning_adjustment() -> None:
    assert learning_score_adjustment([60, 62, 61], current_score=75) > 0
    assert learning_score_adjustment([80, 78, 79], current_score=60) < 0
