"""Tests détecteur de niches — études de marché."""

from __future__ import annotations

import json
from types import SimpleNamespace

from vinted_bot.services.opportunity_engine import (
    Opportunity,
    _ai_analysis,
    _badges_for,
    _classify_niche,
    _compute_gauges,
    _enrich_name_from_titles,
    _label,
    _why_one_liner,
    build_opportunities_board_embed,
    build_opportunity_embed,
    build_pepite_from_opportunity_embed,
    filter_publishable_opportunities,
    is_granular_niche,
    opportunity_priority,
    snapshot_to_opportunity,
)


def test_rejects_broad_accepts_precise() -> None:
    assert not is_granular_niche(
        SimpleNamespace(
            brand_slug="nike",
            model_slug=None,
            keyword_flags="",
            category_slug="hoodie",
            listing_count=40,
        )
    )
    assert is_granular_niche(
        SimpleNamespace(
            brand_slug="nike",
            model_slug="shox_tl",
            keyword_flags="y2k",
            category_slug="chaussure",
            listing_count=10,
        )
    )
    assert is_granular_niche(
        SimpleNamespace(
            brand_slug="carhartt",
            model_slug="detroit_jacket",
            keyword_flags="vintage",
            category_slug="veste",
            listing_count=10,
        )
    )


def test_accepts_brand_category_mid_tier() -> None:
    assert is_granular_niche(
        SimpleNamespace(
            brand_slug="carhartt",
            model_slug=None,
            keyword_flags="",
            category_slug="veste",
            listing_count=12,
        )
    )
    assert not is_granular_niche(
        SimpleNamespace(
            brand_slug="carhartt",
            model_slug=None,
            keyword_flags="",
            category_slug="veste",
            listing_count=3,
        )
    )
    assert not is_granular_niche(
        SimpleNamespace(
            brand_slug="nike",
            model_slug=None,
            keyword_flags="",
            category_slug="hoodie",
            listing_count=50,
        )
    )


def test_rejects_single_listing_and_single_seller() -> None:
    """Une niche ne se base jamais sur 1 annonce ou 1 seul vendeur."""
    assert not is_granular_niche(
        SimpleNamespace(
            brand_slug="nike",
            model_slug="shox_tl",
            keyword_flags="",
            category_slug="chaussure",
            listing_count=1,
            unique_sellers=1,
        )
    )
    assert not is_granular_niche(
        SimpleNamespace(
            brand_slug="carhartt",
            model_slug="detroit_jacket",
            keyword_flags="vintage",
            category_slug="veste",
            listing_count=12,
            unique_sellers=1,
        )
    )
    assert is_granular_niche(
        SimpleNamespace(
            brand_slug="carhartt",
            model_slug="detroit_jacket",
            keyword_flags="vintage",
            category_slug="veste",
            listing_count=12,
            unique_sellers=3,
        )
    )


def test_accepts_product_license_and_object_niches() -> None:
    assert is_granular_niche(
        SimpleNamespace(
            brand_slug="inconnu",
            model_slug="pokemon_etb",
            keyword_flags="pokemon",
            category_slug="collection",
            listing_count=6,
        )
    )
    assert is_granular_niche(
        SimpleNamespace(
            brand_slug="jellycat",
            model_slug="bashful_bunny",
            keyword_flags="",
            category_slug="peluche",
            listing_count=5,
        )
    )
    assert is_granular_niche(
        SimpleNamespace(
            brand_slug="inconnu",
            model_slug=None,
            keyword_flags="pokemon+collection",
            category_slug="collection",
            listing_count=8,
        )
    )
    assert is_granular_niche(
        SimpleNamespace(
            brand_slug="inconnu",
            model_slug=None,
            keyword_flags="",
            category_slug="appareil_photo",
            listing_count=7,
        )
    )


def test_label_prefers_product_over_unknown_brand() -> None:
    name = _label(
        SimpleNamespace(
            brand_slug="inconnu",
            model_slug="bashful_bunny",
            keyword_flags="",
            category_slug="peluche",
        ),
        titles=[],
    )
    assert "Bashful" in name
    assert "Inconnu" not in name


def test_classify_hidden_value_for_obscure_brand() -> None:
    gauges = {
        "demand": 62.0,
        "rarity": 70.0,
        "competition": 28.0,
        "rotation": 40.0,
        "confidence": 55.0,
        "supply_ease": 60.0,
        "price_stability": 60.0,
    }
    kind, label = _classify_niche(
        margin_pct=60.0,
        gauges=gauges,
        listing_count=10,
        demand_delta=20.0,
        median_eur=95.0,
        brand="jellycat",
        disappeared=3,
        has_liquidity=True,
        has_model=True,
        category="peluche",
        flags="",
    )
    assert kind == "hidden"
    assert "cachée" in label.lower() or "peu connue" in label.lower()


def test_enrich_name_adds_color_and_era() -> None:
    name = _enrich_name_from_titles(
        "Carhartt Detroit Jacket",
        ["Carhartt Detroit Jacket marron vintage années 2000 taille M"],
    )
    assert "marron" in name.lower()
    assert "2000" in name or "Y2K" in name or "y2k" in name.lower()


def test_priority_tiers() -> None:
    assert opportunity_priority(90)[0] == "exceptional"


def test_gauges_rotation_low_without_liquidity() -> None:
    snap = SimpleNamespace(
        listing_count=10,
        disappeared_count=0,
        unique_sellers=4,
        new_listings=3,
        median_ttl_days=None,
        price_min_cents=4000,
        price_max_cents=9000,
        price_median_cents=6000,
    )
    gauges = _compute_gauges(snap, w7=None, w30=None)
    assert gauges["rotation"] < 30
    assert gauges["confidence"] < 80


def test_gauges_rotation_high_with_ttl() -> None:
    snap = SimpleNamespace(
        listing_count=12,
        disappeared_count=6,
        unique_sellers=5,
        new_listings=8,
        median_ttl_days=2.5,
        price_min_cents=4000,
        price_max_cents=9000,
        price_median_cents=6000,
    )
    gauges = _compute_gauges(snap, w7=None, w30=None)
    assert gauges["rotation"] >= 60
    assert gauges["confidence"] >= 55


def test_analysis_omits_disparitions_when_zero() -> None:
    text = _ai_analysis(
        "Carhartt Cargo",
        niche_type="hidden",
        gauges={
            "demand": 60,
            "rarity": 70,
            "competition": 30,
            "rotation": 12,
            "confidence": 50,
        },
        margin_eur=40,
        buy=50,
        resell=90,
        category="pantalon",
        facts_line="n=10 · 3 vendeurs · P25 50€",
        disappeared=0,
        demand_delta=10,
        listing_count=10,
        sellers=3,
        ttl=None,
    )
    assert "disparition" not in text.lower() or "sans disparitions" in text.lower()
    assert "Faits :" in text
    assert "n=10" in text


def test_why_and_badges_differ_by_metrics() -> None:
    g_low = {
        "demand": 40,
        "rarity": 50,
        "competition": 60,
        "rotation": 12,
        "confidence": 40,
    }
    g_hi = {
        "demand": 80,
        "rarity": 85,
        "competition": 20,
        "rotation": 75,
        "confidence": 70,
    }
    why_a = _why_one_liner(
        "undervalued",
        gauges=g_low,
        margin_pct=40,
        disappeared=0,
        demand_delta=0,
        listing_count=20,
        sellers=10,
    )
    why_b = _why_one_liner(
        "high_rotation",
        gauges=g_hi,
        margin_pct=70,
        disappeared=8,
        demand_delta=50,
        listing_count=12,
        sellers=3,
    )
    assert why_a != why_b
    badges_a = _badges_for(
        niche_type="undervalued",
        gauges=g_low,
        margin_pct=40,
        brand="carhartt",
        disappeared=0,
        demand_delta=0,
    )
    badges_b = _badges_for(
        niche_type="high_rotation",
        gauges=g_hi,
        margin_pct=70,
        brand="carhartt",
        disappeared=8,
        demand_delta=50,
    )
    assert badges_a != badges_b
    assert "⚡ Rotation rapide" in badges_b
    assert "⚡ Rotation rapide" not in badges_a


def test_snapshot_to_opportunity_distinct_profiles(monkeypatch) -> None:
    monkeypatch.setattr(
        "vinted_bot.services.opportunity_engine._sample_titles_for_snap",
        lambda snap, limit=12: ["Carhartt Cargo marron"],
    )
    monkeypatch.setattr(
        "vinted_bot.services.opportunity_engine._find_photo",
        lambda snap: None,
    )

    # Profil « volume sans liquidité » — scores OK mais rotation faible
    thin = SimpleNamespace(
        niche_key="dickies|dickies_874|pantalon|",
        brand_slug="dickies",
        model_slug="dickies_874",
        category_slug="pantalon",
        keyword_flags="",
        listing_count=16,
        disappeared_count=0,
        unique_sellers=4,
        new_listings=12,
        median_ttl_days=None,
        price_min_cents=2000,
        price_max_cents=6000,
        price_median_cents=4200,
        price_p25_cents=2500,
        margin_proxy_pct=68.0,
        score=74.0,
        metrics={},
    )
    liquid = SimpleNamespace(
        niche_key="carhartt|detroit_jacket|veste|vintage",
        brand_slug="carhartt",
        model_slug="detroit_jacket",
        category_slug="veste",
        keyword_flags="vintage",
        listing_count=14,
        disappeared_count=9,
        unique_sellers=4,
        new_listings=10,
        median_ttl_days=2.0,
        price_min_cents=5000,
        price_max_cents=16000,
        price_median_cents=12000,
        price_p25_cents=7000,
        margin_proxy_pct=71.0,
        score=82.0,
        metrics={"price_p75_cents": 14000},
    )
    op_a = snapshot_to_opportunity(thin)
    op_b = snapshot_to_opportunity(liquid)
    assert op_a is not None and op_b is not None
    assert op_a.why_short != op_b.why_short
    assert op_a.ai_analysis != op_b.ai_analysis
    assert op_a.rotation_score < op_b.rotation_score
    assert "sans disparitions" in op_a.ai_analysis.lower() or op_a.disappeared_count == 0
    assert "disparition" in op_b.ai_analysis.lower()
    assert op_a.facts_line != op_b.facts_line


def _sample_op(**kw: object) -> Opportunity:
    base = dict(
        niche_key="carhartt|detroit_jacket|veste|vintage",
        name="Carhartt Detroit Jacket marron",
        score=94.0,
        niche_type="hidden",
        niche_type_label="💎 Niche peu connue / sous-exploitée",
        priority="exceptional",
        priority_label="🥇 Opportunité exceptionnelle",
        badges=("🔥 Forte demande", "💎 Peu connue", "💰 Forte marge"),
        price_buy_avg_eur=70.0,
        price_resell_avg_eur=130.0,
        price_max_eur=180.0,
        price_buy_max_eur=70.0,
        price_resell_target_eur=145.0,
        margin_eur=60.0,
        margin_pct=85.0,
        demand_score=82.0,
        rarity_score=78.0,
        competition_score=28.0,
        rotation_score=74.0,
        supply_ease_score=70.0,
        price_stability_score=65.0,
        confidence=72.0,
        unique_sellers=4,
        disappeared_pct=40.0,  # ensemble, jamais 1 annonce
        median_ttl_days=2.5,
        price_p75_eur=140.0,
        facts_line="n=14 · 4 vendeurs · P25 70€ · médiane 130€ · disparues 40% · TTL ~2.5j",
        multi_angle_composite=78.0,
        multi_angle_block="📥 Demande `80` — test",
        signals=("📈 demande en hausse", "💎 sous-évaluation"),
        angle_demand=80.0,
        angle_supply=70.0,
        angle_price=75.0,
        angle_behavioral=60.0,
        angle_emerging=55.0,
        angle_profitability=82.0,
        angle_anomaly=70.0,
        lifecycle="growth",
        lifecycle_label="📈 Croissance",
        lifecycle_avoid=False,
        depth_summary="couleurs marron · tailles M,L",
        weak_signal=True,
        weak_signal_summary="progression + faible concurrence",
        confidence_label="bonne",
        international=False,
        international_summary="pas de signal international clair",
        explain_why="**Pourquoi** : forte demande",
        explain_signals="**Signaux** : liquidité",
        explain_strategy="**Stratégie** : acheter ≤ 70 €",
        why_short="demande confirmée (liquidité) + stock concentré (4 vendeurs)",
        ai_analysis="Niche workwear recherchée avec offre limitée.",
        strategy_where="Chercher : `Carhartt` · `Detroit Jacket`",
        strategy_buy="Prix max achat : **≤ 70 €**",
        strategy_sell="Prix revente cible : **145 €**",
        action="buy",
        action_detail="Rechercher activement",
        photo_url=None,
        brand_slug="carhartt",
        model_slug="detroit_jacket",
        category_slug="veste",
        keyword_flags="vintage",
        search_terms=("Carhartt", "Detroit Jacket", "vintage"),
        sample_size=12000,
        listing_count=14,
        disappeared_count=9,
    )
    base.update(kw)
    return Opportunity(**base)  # type: ignore[arg-type]


def test_snapshot_rejects_thin_sample() -> None:
    thin = SimpleNamespace(
        niche_key="x|y|z|",
        brand_slug="carhartt",
        model_slug="detroit_jacket",
        category_slug="veste",
        keyword_flags="vintage",
        listing_count=1,
        disappeared_count=0,
        unique_sellers=1,
        new_listings=1,
        median_ttl_days=None,
        price_min_cents=5000,
        price_max_cents=5000,
        price_median_cents=5000,
        price_p25_cents=5000,
        margin_proxy_pct=40.0,
        score=90.0,
        metrics={},
    )
    assert snapshot_to_opportunity(thin) is None


def test_publish_requires_ensemble_not_micro_cluster() -> None:
    micro = _sample_op(listing_count=5, unique_sellers=2, score=90.0)
    solid = _sample_op(listing_count=14, unique_sellers=4, score=90.0)
    out = filter_publishable_opportunities([micro, solid], min_score=65.0)
    assert len(out) == 1
    assert out[0].listing_count >= 8


def test_market_study_embed_radar_essentials_only() -> None:
    op = _sample_op(
        photo_url="https://images1.vinted.net/t/01_x/f200/photo.jpeg?s=1"
    )
    embed = build_opportunity_embed(op)
    assert "Carhartt Detroit Jacket marron" in embed["title"]
    assert "Carhartt Detroit Jacket marron" in embed["description"]
    assert "⭐ Opportunité" in embed["description"]
    assert "94/100" in embed["description"]
    assert "Voir les annonces similaires" in embed["description"]
    names = {f["name"] for f in embed["fields"]}
    assert names == {
        "💰 Marché",
        "📊 Signaux marché",
        "🧠 Pourquoi ?",
        "🔎 Rechercher",
        "🔗 Explorer la niche",
    }
    # Pas de jargon technique dans le message principal
    blob = json.dumps(embed, ensure_ascii=False)
    for banned in ("P25", "P75", "TTL", "multi-angle", "sous-score", "Rareté", "Concurrence"):
        assert banned not in blob
    assert "Achat observé" in embed["fields"][0]["value"]
    assert "Demande" in embed["fields"][1]["value"]
    assert "catalog?search_text=" in embed["url"]
    assert "Carhartt" in embed["url"] or "carhartt" in embed["url"].lower()
    assert "annonces analysées" in embed["footer"]["text"]
    # Image de référence en grand (pas thumbnail)
    assert "image" in embed
    assert "f800" in embed["image"]["url"]
    assert "thumbnail" not in embed


def test_explore_url_is_valid_catalog_search() -> None:
    from vinted_bot.services.opportunity_engine import _vinted_explore_url

    url = _vinted_explore_url(_sample_op())
    assert url.startswith("https://")
    assert "/catalog?" in url
    assert "search_text=" in url
    assert "order=newest_first" in url


def test_top_board_explains_why() -> None:
    board = build_opportunities_board_embed([_sample_op()])
    assert "RADAR" in board["title"]
    assert "94/100" in board["description"]
    assert "demande confirmée" in board["description"]


def test_pepite_separate_module_link() -> None:
    embed = build_pepite_from_opportunity_embed(
        title="Detroit Jacket M",
        url="https://www.vinted.fr/items/1",
        price_cents=5500,
        resell_cents=14000,
        margin_pct=150.0,
        photo_url=None,
        niche_name="Carhartt Detroit Jacket marron",
        niche_score=94.0,
    )
    assert "Pépite" in embed["title"]
    assert "Niche source" in embed["fields"][2]["name"]
