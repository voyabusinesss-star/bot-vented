"""Tests du filtre deal (marque × catégorie × prix + score)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vinted_bot.services.deal_filter import (
    clear_deal_filters_cache,
    detect_category,
    evaluate_deal,
    format_price_eur,
    load_deal_filters,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_deal_filters_cache()
    yield
    clear_deal_filters_cache()


def test_load_deal_filters_from_repo() -> None:
    cfg = load_deal_filters()
    assert cfg.settings.enabled is True
    assert "ralph_lauren" in cfg.brands or "ralph lauren" in cfg.brands
    ralph = cfg.brands.get("ralph_lauren") or cfg.brands["ralph lauren"]
    assert "polo" in ralph.items
    assert ralph.items["polo"].max_buy_price == 25


def test_detect_category_priority() -> None:
    # dunk avant sweat générique
    assert detect_category("Nike Dunk Low Panda") == "dunk"
    assert detect_category("Air Force 1 blanche") == "air_force_1"
    assert detect_category("Polo Ralph Lauren XL") == "polo"
    assert detect_category("Sweat à capuche Stussy") == "hoodie"
    assert detect_category("Veste Carhartt vintage") == "veste"
    assert detect_category("Baskets Louis Vuitton") == "chaussure"
    assert detect_category("Casquette random") is None


def test_evaluate_pepite_stone_island() -> None:
    # Prix bas + grosse marge + marque forte → PÉPITE
    now_ts = datetime.now(timezone.utc).timestamp()
    deal = evaluate_deal(
        brand="Stone Island",
        title="Sweat Stone Island vintage",
        price_cents=4500,  # 45€ — max 60, resell 150
        raw_json={"created_at_ts": now_ts},
    )
    assert deal.should_post is True
    assert deal.category == "sweat"
    assert deal.estimated_profit == 105.0
    assert deal.score > 90
    assert deal.level == "pepite"
    assert "PÉPITE" in deal.level_label


def test_evaluate_reject_price_above_max_without_margin_bypass() -> None:
    # Prix > max → reject (même si on ne regarde plus la marge)
    deal = evaluate_deal(
        brand="Ralph Lauren",
        title="Polo Ralph Lauren",
        price_cents=4500,  # 45€ > max polo 25
        raw_json={"created_at_ts": datetime.now(timezone.utc).timestamp()},
    )
    assert deal.should_post is False
    assert deal.reason == "price_above_max"


def test_evaluate_accept_under_max_even_with_thin_margin() -> None:
    # Sous le max : on poste même si marge estimée faible (pas de filtre marge)
    # max_buy polo RL = 25, average 50 → buy 24, marge 26 peut être OK;
    # buy 24 under max always passes price gate.
    deal = evaluate_deal(
        brand="Ralph Lauren",
        title="Polo Ralph Lauren bleu",
        price_cents=2400,
        raw_json={"created_at_ts": datetime.now(timezone.utc).timestamp()},
    )
    assert deal.buy_price == 24.0
    assert deal.reason != "price_above_max"
    assert deal.reason != "price_and_margin_too_weak"


def test_evaluate_reject_unknown_category() -> None:
    # Avant : category_not_matched. Maintenant fallback médian (ne rate pas).
    deal = evaluate_deal(
        brand="Nike",
        title="Casquette Nike vintage",
        price_cents=800,
        raw_json={"created_at_ts": datetime.now(timezone.utc).timestamp()},
    )
    assert deal.reason in ("ok_fallback", "ok", "score_too_low", "price_and_margin_too_weak")
    assert deal.reason != "category_not_matched"
    assert deal.category == "default"


def test_escarpins_luxury_detected() -> None:
    deal = evaluate_deal(
        brand="Prada",
        title="Escarpins Prada cuir",
        price_cents=15000,
        size="38",
        raw_json={"created_at_ts": datetime.now(timezone.utc).timestamp()},
    )
    assert deal.reason != "shoes_not_allowed"
    assert deal.category == "chaussure"
    assert deal.should_post is True


def test_replica_rejected() -> None:
    deal = evaluate_deal(
        brand="Stone Island",
        title="Sweat Stone Island replica 1:1",
        price_cents=4000,
        raw_json={"created_at_ts": datetime.now(timezone.utc).timestamp()},
    )
    assert deal.should_post is False
    assert deal.reason == "replica_item"


def test_evaluate_reject_unconfigured_brand() -> None:
    deal = evaluate_deal(
        brand="Puma",
        title="Sweat Puma",
        price_cents=1000,
    )
    assert deal.should_post is False
    assert deal.reason == "brand_not_configured"


def test_kids_xl_xxl_adult_kept() -> None:
    from vinted_bot.services.deal_filter import is_kids_listing

    assert is_kids_listing("Polo Ralph Lauren", "XL") is False
    assert is_kids_listing("Sweat Nike Tech", "XXL") is False
    assert is_kids_listing("Veste Carhartt", "XXXL") is False
    assert is_kids_listing("Hoodie Stussy coupe fille", "M") is False
    assert is_kids_listing("Chemise style garçon", "L") is False

    deal = evaluate_deal(
        brand="Ralph Lauren",
        title="Polo Ralph Lauren",
        price_cents=1500,
        size="XL",
        raw_json={"created_at_ts": datetime.now(timezone.utc).timestamp()},
    )
    assert deal.should_post is True
    assert deal.reason != "kids_item"


def test_kids_signals_rejected() -> None:
    from vinted_bot.services.deal_filter import is_kids_listing

    assert is_kids_listing("Sweat Nike enfant", "M") is True
    assert is_kids_listing("Hoodie Stussy kids", "XL") is True
    assert is_kids_listing("Veste The North Face", "12 ans") is True
    assert is_kids_listing("Pantalon Carhartt", "140 cm") is True
    assert is_kids_listing("Pull Lacoste 10-12 ans", "M") is True

    deal = evaluate_deal(
        brand="Nike",
        title="Hoodie Nike enfant",
        price_cents=2000,
        size="XL",
        raw_json={"created_at_ts": datetime.now(timezone.utc).timestamp()},
    )
    assert deal.should_post is False
    assert deal.reason == "kids_item"


def test_shoes_rejected_for_classic_brands() -> None:
    from vinted_bot.services.deal_filter import is_shoe_listing

    assert is_shoe_listing("Nike Dunk Low Panda") is True
    assert is_shoe_listing("Air Force 1 blanche") is True
    assert is_shoe_listing("Baskets Adidas Samba") is True
    assert is_shoe_listing("Hoodie Nike Tech") is False

    # Marques classiques sans allow_shoes (ex. Carhartt)
    deal = evaluate_deal(
        brand="Carhartt",
        title="Baskets Carhartt",
        price_cents=5000,
        size="42",
        raw_json={"created_at_ts": datetime.now(timezone.utc).timestamp()},
    )
    assert deal.should_post is False
    assert deal.reason == "shoes_not_allowed"


def test_shoes_only_rejects_clothing() -> None:
    deal = evaluate_deal(
        brand="Jordan",
        title="Hoodie Jordan Jumpman",
        price_cents=4000,
        size="L",
        raw_json={"created_at_ts": datetime.now(timezone.utc).timestamp()},
    )
    assert deal.should_post is False
    assert deal.reason == "not_a_shoe"


def test_shoes_only_accepts_sneakers() -> None:
    deal = evaluate_deal(
        brand="New Balance",
        title="New Balance 550 blanche",
        price_cents=5000,
        size="42",
        raw_json={"created_at_ts": datetime.now(timezone.utc).timestamp()},
    )
    assert deal.should_post is True
    assert deal.category == "chaussure"
    assert deal.reason in ("ok", "ok_fallback")


def test_shoes_allowed_for_luxury() -> None:
    deal = evaluate_deal(
        brand="Louis Vuitton",
        title="Baskets Louis Vuitton Trainer",
        price_cents=20000,
        size="42",
        raw_json={"created_at_ts": datetime.now(timezone.utc).timestamp()},
    )
    assert deal.reason != "shoes_not_allowed"
    assert deal.category == "chaussure"
    assert deal.should_post is True


def test_evaluate_reject_too_old() -> None:
    # max_listing_age_minutes = 45 dans deal_filters.yaml
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    deal = evaluate_deal(
        brand="Stone Island",
        title="Sweat Stone Island vintage",
        price_cents=4500,
        raw_json={"created_at_ts": old_ts},
    )
    assert deal.should_post is False
    assert deal.reason == "too_old"


def test_evaluate_accept_fresh_within_max_age() -> None:
    fresh_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp()
    deal = evaluate_deal(
        brand="Stone Island",
        title="Sweat Stone Island vintage",
        price_cents=4500,
        raw_json={"created_at_ts": fresh_ts},
    )
    assert deal.should_post is True
    assert deal.reason == "ok"


def test_freshness_boosts_score() -> None:
    fresh = evaluate_deal(
        brand="Stone Island",
        title="Veste Stone Island",
        price_cents=8000,
        raw_json={"created_at_ts": datetime.now(timezone.utc).timestamp()},
    )
    slightly_older = evaluate_deal(
        brand="Stone Island",
        title="Veste Stone Island",
        price_cents=8000,
        published_at=datetime.now(timezone.utc) - timedelta(minutes=20),
    )
    assert fresh.should_post is True
    assert slightly_older.should_post is True
    assert fresh.score >= slightly_older.score


def test_format_price_eur() -> None:
    assert format_price_eur(45) == "45€"
    assert format_price_eur(45.5) == "45,50€"


def test_disabled_filter_posts_everything(tmp_path: Path) -> None:
    path = tmp_path / "deal_filters.yaml"
    path.write_text(
        "settings:\n  enabled: false\n  max_listing_age_minutes: null\nbrands: {}\n",
        encoding="utf-8",
    )
    clear_deal_filters_cache()
    cfg = load_deal_filters(str(path))
    deal = evaluate_deal(
        brand="Anything",
        title="Whatever",
        price_cents=99900,
        config=cfg,
    )
    assert deal.should_post is True
    assert deal.reason == "filter_disabled"
