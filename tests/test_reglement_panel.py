"""Tests panneau règlement."""

from vinted_bot.interactions.reglement_panel import (
    REGLEMENT_ACCEPT,
    build_reglement_panel_payload,
)


def test_reglement_panel_payload() -> None:
    payload = build_reglement_panel_payload()
    embed = payload["embeds"][0]
    assert "Règlement" in embed["title"]
    assert "accepté" in embed["description"].lower() or "accept" in embed["description"].lower()
    button = payload["components"][0]["components"][0]
    assert button["custom_id"] == REGLEMENT_ACCEPT
    assert "accepte" in button["label"].lower()
