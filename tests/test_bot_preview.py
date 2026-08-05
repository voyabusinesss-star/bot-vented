"""Tests aperçu bot (listing avec boutons + diversité)."""

from __future__ import annotations

from vinted_bot.db.models import Listing
from vinted_bot.notify.discord import (
    build_listing_payload,
    build_listing_preview_payload,
    pick_diverse_preview_listing,
)


def _listing(**kwargs: object) -> Listing:
    defaults = {
        "id": 1,
        "vinted_id": 99,
        "title": "Polo Ralph Lauren",
        "url": "https://www.vinted.fr/items/99",
        "price_cents": 2500,
        "brand": "Ralph Lauren",
        "size": "M",
        "currency": "EUR",
    }
    defaults.update(kwargs)
    return Listing(**defaults)  # type: ignore[arg-type]


def test_preview_payload_has_buy_negotiate_buttons() -> None:
    listing = _listing()
    preview = build_listing_preview_payload(listing)
    full = build_listing_payload(listing)
    assert preview.get("components")
    assert "Acheter" in str(preview["components"])
    assert "Négocier" in str(preview["components"])
    assert preview["components"] == full["components"]


def test_pick_diverse_prefers_unseen_brand() -> None:
    import vinted_bot.notify.discord as discord_mod

    discord_mod._recent_preview_brands = ["shoe:adidas", "shoe:nike"]
    discord_mod._recent_preview_vinted_ids = []
    adidas = _listing(id=1, vinted_id=1, brand="Adidas", title="Adidas Samba")
    polo = _listing(id=2, vinted_id=2, brand="Ralph Lauren", title="Polo Ralph Lauren")
    picked = pick_diverse_preview_listing([adidas, polo])
    assert picked is not None
    assert picked.brand == "Ralph Lauren"
