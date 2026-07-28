"""Fiches produit — sélection / build après deep-dive."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from vinted_bot.services import niche_product_sheets as fps


def test_develop_meets_minimum_requires_rounds_and_duration():
    target = 3600.0
    assert fps._develop_meets_minimum(
        {"rounds": 5, "elapsed_s": 3000}, target_seconds=target, fast=False
    )
    assert not fps._develop_meets_minimum(
        {"rounds": 1, "elapsed_s": 3000}, target_seconds=target, fast=False
    )
    assert not fps._develop_meets_minimum(
        {"rounds": 5, "elapsed_s": 600}, target_seconds=target, fast=False
    )
    assert fps._develop_meets_minimum(
        {"rounds": 1, "elapsed_s": 90}, target_seconds=120, fast=True
    )


def test_channel_fiches_uses_fiches_produit_only():
    settings = type(
        "S",
        (),
        {
            "discord_channel_niches_vinted": "1529869768343949312",
            "discord_channel_fiches_produit": "1531040761964007545",
            "discord_channel_niches": "1530566641077981367",
        },
    )()
    assert fps._channel_fiches(settings) == "1531040761964007545"
    assert (
        fps._validate_fiches_post_channel(settings, "1531040761964007545") is None
    )
    assert (
        fps._validate_fiches_post_channel(settings, "1529869768343949312")
        == "fiches_channel_equals_niches_vinted"
    )


def test_effective_develop_seconds_caps_at_hourly_budget():
    capped = fps._effective_develop_seconds(7200.0)
    assert capped <= fps.FICHES_INTERVAL_SECONDS
    assert capped >= 300.0


def test_build_respects_min_score_floor():
    op = SimpleNamespace(
        niche_key="x",
        listing_count=20,
        unique_sellers=5,
        score=64.0,
        sample_size=20,
        name="X",
        depth_summary="",
        search_terms=(),
        model_slug=None,
        lifecycle_avoid=False,
        lifecycle="ok",
        lifecycle_label="",
        competition_score=10,
        confidence=80,
        weak_signal=False,
        margin_pct=50,
        explain_why="a" * 40,
        explain_signals="",
        explain_strategy="",
        why_short="",
        signals=(),
        strategy_buy="",
        action_detail="",
        angle_emerging=40,
        priority="interesting",
        niche_type="high_value",
    )
    with patch.object(fps, "_snapshot_for_key", return_value=None):
        assert fps.build_niche_product_sheet(op, snap=None) is None  # type: ignore[arg-type]


def test_select_keeps_original_op_when_refresh_score_drops():
    """Après deep-dive, un score trop bas au refresh ne doit pas faire échouer la fiche."""
    original = SimpleNamespace(
        niche_key="jellycat|bashful_bunny||",
        name="Jellycat",
        score=66.0,
        listing_count=31,
        unique_sellers=28,
        sample_size=31,
        priority="interesting",
        niche_type="high_value",
        model_slug="bashful_bunny",
        brand_slug="jellycat",
        category_slug=None,
        search_terms=("jellycat",),
        depth_summary="beige",
        explain_why="Niche validée détecteur avec demande confirmée.",
        explain_signals="Rotation vendeurs correcte.",
        explain_strategy="Acheter sous le prix médian observé.",
        why_short="",
        signals=("demande",),
        strategy_buy="",
        action_detail="",
        lifecycle="growth",
        lifecycle_label="Croissance",
        lifecycle_avoid=False,
        competition_score=40.0,
        confidence=70.0,
        weak_signal=False,
        margin_pct=80.0,
        angle_emerging=55.0,
    )
    refreshed = SimpleNamespace(**{**original.__dict__, "score": 50.0})
    snap = SimpleNamespace(
        niche_key=original.niche_key,
        listing_count=31,
        unique_sellers=28,
        brand_slug="jellycat",
        model_slug="bashful_bunny",
        category_slug=None,
    )
    mosaic = tuple(
        fps.MosaicItem(
            photo_url=f"https://example.com/{i}.jpg",
            listing_url="https://vinted.fr/x",
            seller_key=str(i),
            price_eur=20.0 + i,
            title=f"item {i}",
        )
        for i in range(8)
    )

    def fake_build(op, *, snap=None, min_score=None):
        floor = fps.MIN_FICHE_SCORE if min_score is None else min_score
        if op.score < floor:
            return None
        return fps.NicheProductSheet(
            opportunity=op,  # type: ignore[arg-type]
            mosaic=mosaic,
            look_for=("beige",),
            avoid=("contrefaçon",),
            ai_analysis="Analyse.",
            buy_article_eur=20.0,
            buy_landed_eur=25.0,
            resell_eur=45.0,
            shipping_estimate_eur=3.99,
            volume_analyzed=31,
            developed_minutes=0,
        )

    with (
        patch.object(
            fps,
            "pick_best_detector_opportunity",
            return_value=(original, snap),
        ),
        patch.object(fps, "develop_niche", return_value={"elapsed_s": 3600, "rounds": 8}),
        patch.object(fps, "_snapshot_for_key", return_value=snap),
        patch.object(fps, "snapshot_to_opportunity", return_value=refreshed),
        patch.object(fps, "_collect_mosaic", return_value=list(mosaic)),
        patch.object(fps, "build_niche_product_sheet", side_effect=fake_build),
    ):
        out = fps.select_next_fiche(force=True, develop=True, develop_seconds=1.0)
    assert out is not None
    assert out.opportunity.score == 66.0
    assert out.developed_minutes >= 1
