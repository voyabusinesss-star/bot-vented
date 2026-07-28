"""Tests cibles scrape dérivées des filtres privés."""

from types import SimpleNamespace

from vinted_bot.clients.vinted_browser import build_catalog_params
from vinted_bot.config_loader import merge_search_targets
from vinted_bot.services.filter_scrape_targets import (
    active_filter_search_targets,
    filter_row_to_search_target,
)


def test_filter_row_nike_tn_maps_to_target() -> None:
    row = SimpleNamespace(
        brand="Nike",
        model="TN",
        category="Chaussures",
        keyword="TN",
        min_price_eur=None,
        max_price_eur=70.0,
        is_active=True,
    )
    target = filter_row_to_search_target(
        row, brand_ids_map={"nike": [53]}
    )
    assert target is not None
    assert target.source == "user_filter"
    assert target.max_discord_posts == 0
    assert target.brand == "nike"
    assert target.brand_ids == [53]
    assert "tn" in target.query.lower()
    assert target.price_to == 70.0
    assert target.catalog_ids == [1231, 1242]


def test_paused_filters_ignored() -> None:
    rows = [
        SimpleNamespace(
            brand="Nike",
            model="TN",
            category="Chaussures",
            keyword="TN",
            min_price_eur=None,
            max_price_eur=70.0,
            is_active=False,
        ),
        SimpleNamespace(
            brand="Nike",
            model="TN",
            category="Chaussures",
            keyword="TN",
            min_price_eur=None,
            max_price_eur=70.0,
            is_active=True,
        ),
    ]
    targets = active_filter_search_targets(filters=rows, max_targets=10)
    assert len(targets) == 1
    assert targets[0].price_to == 70.0


def test_dedupe_identical_filters() -> None:
    rows = [
        SimpleNamespace(
            brand="Nike",
            model="TN",
            category="Chaussures",
            keyword="TN",
            min_price_eur=None,
            max_price_eur=70.0,
            is_active=True,
        ),
        SimpleNamespace(
            brand="Nike",
            model="TN",
            category="Chaussures",
            keyword="TN",
            min_price_eur=None,
            max_price_eur=70.0,
            is_active=True,
        ),
    ]
    targets = active_filter_search_targets(filters=rows, max_targets=10)
    assert len(targets) == 1


def test_merge_search_targets_dedupes() -> None:
    from vinted_bot.config_loader import SearchTarget

    a = SearchTarget(brand="nike", query="nike", brand_ids=[53], source="yaml")
    b = SearchTarget(
        brand="nike",
        query="tn",
        brand_ids=[53],
        price_to=70.0,
        source="user_filter",
        max_discord_posts=0,
    )
    c = SearchTarget(
        brand="nike",
        query="tn",
        brand_ids=[53],
        price_to=70.0,
        source="user_filter",
        max_discord_posts=0,
    )
    merged = merge_search_targets([a], [b, c])
    assert len(merged) == 2
    assert merged[0].query == "nike"
    assert merged[1].query == "tn"


def test_build_catalog_params_price_to() -> None:
    params = build_catalog_params(
        "tn",
        brand_ids=[53],
        catalog_ids=[1231],
        price_from=10,
        price_to=70,
    )
    assert ("search_text", "tn") in params
    assert ("price_from", "10") in params
    assert ("price_to", "70") in params
    assert ("brand_ids[]", "53") in params


def test_keyword_only_filter_no_brand_ids() -> None:
    row = SimpleNamespace(
        brand=None,
        model=None,
        category=None,
        keyword="Jellycat",
        min_price_eur=None,
        max_price_eur=40.0,
        is_active=True,
    )
    target = filter_row_to_search_target(row, brand_ids_map={})
    assert target is not None
    assert target.brand == "filter"
    assert target.query == "Jellycat"
    assert target.brand_ids == []
    assert target.price_to == 40.0
