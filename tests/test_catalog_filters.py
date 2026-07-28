"""Tests params catalog / filtres."""

from vinted_bot.clients.vinted_browser import build_catalog_params
from vinted_bot.config_loader import load_searches_config


def test_build_catalog_params_newest_and_filters() -> None:
    params = dict(
        build_catalog_params(
            "nike",
            page=1,
            per_page=12,
            order="newest_first",
            brand_ids=[53],
            catalog_ids=[5],
        )
    )
    # urlencode with list uses multi keys — rebuild as list check
    param_list = build_catalog_params(
        "nike",
        brand_ids=[53, 14],
        catalog_ids=[5],
        order="newest_first",
    )
    assert ("order", "newest_first") in param_list
    assert ("search_text", "nike") in param_list
    assert ("brand_ids[]", "53") in param_list
    assert ("brand_ids[]", "14") in param_list
    assert ("catalog[]", "5") in param_list


def test_build_catalog_params_with_price() -> None:
    param_list = build_catalog_params(
        "tn",
        brand_ids=[53],
        price_to=70,
        price_from=20.5,
    )
    assert ("price_to", "70") in param_list
    assert ("price_from", "20.5") in param_list


def test_searches_config_loop_defaults() -> None:
    cfg = load_searches_config()
    assert cfg.loop_interval_seconds >= 1
    assert cfg.browser_restart_every_cycles >= 1
    assert cfg.order == "newest_first"
    assert cfg.searches
    # Défaut global vêtements ; Nike/Adidas/luxe peuvent overrider avec chaussures
    assert cfg.searches[0].catalog_ids  # non vide
    carhartt = next(s for s in cfg.searches if s.brand == "carhartt")
    assert carhartt.catalog_ids == [4, 5]
