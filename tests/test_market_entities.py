"""Tests extraction marque / modèle / keywords."""

from __future__ import annotations

from vinted_bot.services.market_entities import (
    brand_saturation_penalty,
    category_domain,
    detect_extended_category,
    detect_keywords,
    detect_model,
    extract_entities_from_text,
    is_analyzable_listing,
    is_market_domain,
    load_category_defs,
    load_keyword_defs,
    load_model_defs,
)


def test_load_models_and_keywords() -> None:
    models = load_model_defs()
    keywords = load_keyword_defs()
    assert any(m.slug == "dunk_sb" for m in models)
    assert any(m.slug == "detroit_jacket" for m in models)
    assert any(k.slug == "vintage" for k in keywords)
    assert any(k.slug == "gorpcore" for k in keywords)


def test_detect_nike_dunk_sb() -> None:
    model = detect_model("Nike Dunk SB Low Travis", brand="Nike")
    assert model is not None
    assert model.slug == "dunk_sb"


def test_detect_carhartt_detroit() -> None:
    model = detect_model("Veste Carhartt Detroit Jacket vintage", brand="Carhartt")
    assert model is not None
    assert model.slug == "detroit_jacket"


def test_detect_salomon_xt6() -> None:
    model = detect_model("Salomon XT-6 Black", brand="Salomon")
    assert model is not None
    assert model.slug == "xt_6"


def test_detect_keywords_vintage_y2k() -> None:
    found = detect_keywords("Hoodie Nike vintage Y2K big logo")
    slugs = {k.slug for k in found}
    assert "vintage" in slugs
    assert "y2k" in slugs
    assert "big_logo" in slugs


def test_extract_niche_key() -> None:
    extracted = extract_entities_from_text(
        title="Carhartt Detroit Jacket vintage M",
        brand="Carhartt",
    )
    assert extracted.brand_slug == "carhartt"
    assert extracted.model_slug == "detroit_jacket"
    assert "vintage" in extracted.keyword_slugs
    assert extracted.in_domain
    assert "detroit_jacket" in extracted.niche_key


def test_domain_includes_sneakers() -> None:
    assert is_market_domain("Nike Dunk Low panda", brand="Nike")
    assert is_market_domain("Hoodie Nike noir", brand="Nike")


def test_domain_includes_objects_and_licenses() -> None:
    assert load_category_defs()
    assert detect_extended_category("Peluche Jellycat Bashful Bunny") == "peluche"
    assert detect_extended_category("Appareil photo argentique Olympus") == "appareil_photo"
    assert is_market_domain("Peluche Jellycat Bashful Bunny rose", brand="Jellycat")
    assert is_market_domain("Display booster Pokémon ETB", brand=None)
    assert is_market_domain("LEGO Star Wars Millennium Falcon", brand=None)
    assert category_domain("peluche") == "toys"
    assert category_domain("veste") == "fashion"


def test_detector_analyzes_any_category_not_fashion_only() -> None:
    """Le détecteur accepte maison, électronique, bijoux… pas seulement mode."""
    assert is_analyzable_listing("Canapé cuir vintage 3 places", brand=None)
    assert is_analyzable_listing("iPhone 12 128Go", brand="Apple")
    assert is_analyzable_listing("Collier argent 925", brand=None)
    assert is_market_domain("Lampe de chevet design", brand=None)
    assert detect_extended_category("Canapé cuir vintage") == "maison"
    assert detect_extended_category("Collier argent vintage") == "bijoux"
    assert category_domain("maison") == "home"
    assert category_domain("jeux_video") == "electronics"


def test_product_level_entity_without_fashion_brand() -> None:
    extracted = extract_entities_from_text(
        title="Jellycat Bashful Bunny peluche",
        brand=None,
    )
    assert extracted.in_domain
    assert extracted.model_slug == "bashful_bunny"
    assert extracted.brand_slug == "jellycat"
    assert extracted.category_slug == "peluche"


def test_detect_cp_company_goggle_from_title() -> None:
    extracted = extract_entities_from_text(
        title="C.P. Company goggle jacket vintage M",
        brand=None,
    )
    assert extracted.brand_slug == "cp company"
    assert extracted.model_slug == "goggle_jacket"
    assert "goggle_jacket" in extracted.niche_key


def test_saturation_penalty() -> None:
    assert brand_saturation_penalty("nike", None) > brand_saturation_penalty(
        "carhartt", "detroit_jacket"
    )
    assert brand_saturation_penalty("nike", "dunk") < brand_saturation_penalty(
        "nike", None
    )
