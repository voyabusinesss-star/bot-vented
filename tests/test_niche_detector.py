"""Tests détecteur de niches (sans navigateur)."""

from __future__ import annotations

from types import SimpleNamespace

from vinted_bot.niche_config import load_niches_config
from vinted_bot.services.niche_detector import analyze_probe_items, build_niche_embed


def _item(
    *,
    title: str,
    brand: str,
    price_cents: int,
    vinted_id: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        title=title,
        brand=brand,
        price_cents=price_cents,
        url=f"https://www.vinted.fr/items/{vinted_id}",
        vinted_id=vinted_id,
    )


def test_load_niches_config() -> None:
    cfg = load_niches_config()
    assert cfg.min_samples >= 1
    assert len(cfg.probes) >= 1
    # Détecteur multi-catégories : pas de filtre mode-only sur les probes
    assert cfg.catalog_ids == []
    assert any(c.catalog_id is None for c in cfg.discovery_catalogs)
    assert len(cfg.discovery_catalogs) >= 3


def test_analyze_probe_finds_margin_niche() -> None:
    items = [
        _item(title="Hoodie Nike vintage M", brand="Nike", price_cents=1500, vinted_id=1),
        _item(title="Hoodie Nike noir L", brand="Nike", price_cents=1800, vinted_id=2),
        _item(title="Nike hoodie gris", brand="Nike", price_cents=2000, vinted_id=3),
        _item(title="Hoodie Nike rouge", brand="Nike", price_cents=3500, vinted_id=4),
        _item(title="Hoodie Nike bleu", brand="Nike", price_cents=4000, vinted_id=5),
        _item(title="Hoodie Nike blanc", brand="Nike", price_cents=4200, vinted_id=6),
        _item(title="Dunk Nike Low", brand="Nike", price_cents=5000, vinted_id=7),  # shoe
    ]
    ops = analyze_probe_items(
        items,  # type: ignore[arg-type]
        probe_label="test",
        min_samples=6,
        min_margin_pct=20,
        min_margin_eur=8,
    )
    assert ops
    top = ops[0]
    assert top.brand == "nike"
    assert top.category == "hoodie"
    assert top.margin_pct >= 20
    assert "Dunk" not in top.example_title


def test_analyze_rejects_shoes_only_bucket() -> None:
    items = [
        _item(title="Nike Dunk Low", brand="Nike", price_cents=4000, vinted_id=i)
        for i in range(1, 10)
    ]
    ops = analyze_probe_items(
        items,  # type: ignore[arg-type]
        probe_label="shoes",
        min_samples=6,
        min_margin_pct=10,
        min_margin_eur=5,
    )
    assert ops == []


def test_build_niche_embed() -> None:
    from vinted_bot.services.niche_detector import NicheOpportunity

    niche = NicheOpportunity(
        brand="nike",
        category="hoodie",
        category_label="Hoodie",
        sample_count=10,
        median_price_eur=40.0,
        cheap_price_eur=20.0,
        margin_eur=20.0,
        margin_pct=50.0,
        probe_label="test",
        example_title="Hoodie Nike",
        example_url="https://www.vinted.fr/items/1",
        example_vinted_id=1,
    )
    embed = build_niche_embed(niche)
    assert "Niche" in embed["title"]
    assert embed["url"] == niche.example_url
