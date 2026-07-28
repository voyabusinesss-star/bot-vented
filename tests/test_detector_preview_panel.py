"""Tests panneau aperçu détecteur de niches."""

from vinted_bot.interactions.detector_preview_panel import (
    build_detector_preview_panel_payload,
)


def test_detector_preview_panel_payload() -> None:
    payload = build_detector_preview_panel_payload()
    embeds = payload["embeds"]
    assert len(embeds) == 2

    intro = embeds[0]
    assert intro["title"] == "🧠 APERÇU DÉTECTEUR DE NICHES"
    assert "Resello" in intro["description"]
    assert "Premium" in intro["description"]
    assert "Exemple réel" in intro["description"]

    example = embeds[1]
    assert "Wrangler" in example["title"] or "Wrangler" in example.get("description", "")
    assert example.get("fields")
    market = next(f for f in example["fields"] if f["name"] == "💰 Marché")
    assert "18" in market["value"] or "€" in market["value"]
