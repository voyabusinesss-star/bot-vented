"""Tests panneau règlement."""

from vinted_bot.interactions.reglement_panel import (
    REGLEMENT_ACCEPT,
    build_reglement_accept_components,
    build_reglement_panel_payload,
)


def test_reglement_panel_payload() -> None:
    payload = build_reglement_panel_payload()
    embed = payload["embeds"][0]
    assert "Respect" in embed["description"]
    assert "LEAKS INTERDITS" in embed["description"]
    button = payload["components"][0]["components"][0]
    assert button["custom_id"] == REGLEMENT_ACCEPT
    assert "accepte" in button["label"].lower()


def test_reglement_accept_components() -> None:
    components = build_reglement_accept_components()
    assert components[0]["components"][0]["custom_id"] == REGLEMENT_ACCEPT
