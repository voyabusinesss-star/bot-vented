"""Tests parsers recherche."""

from __future__ import annotations

import json
from pathlib import Path

from vinted_bot.parsers.search import parse_catalog_item, parse_catalog_payload

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_catalog_payload() -> None:
    payload = json.loads((FIXTURES / "catalog_items.json").read_text())
    items = parse_catalog_payload(payload)
    assert len(items) == 2
    assert items[0].vinted_id == 123456789
    assert items[0].title == "Nike Air Max 90"
    assert items[0].price_cents == 4500
    assert items[0].brand == "Nike"
    assert items[0].size == "42"
    assert items[0].url.startswith("https://www.vinted.fr/")
    assert items[1].price_cents == 3050


def test_parse_catalog_item_missing_id() -> None:
    assert parse_catalog_item({"title": "x"}) is None
