"""Tests panneau MES ALERTES."""

from types import SimpleNamespace

from vinted_bot.interactions.alerts_panel import build_user_alerts_payload


def test_build_user_alerts_payload_format() -> None:
    rows = [
        SimpleNamespace(
            id=1,
            name="Nike TN",
            brand="Nike",
            model="TN",
            category="Chaussures",
            keyword="TN",
            min_price_eur=None,
            max_price_eur=70.0,
            is_active=True,
        ),
        SimpleNamespace(
            id=2,
            name=None,
            brand="Nike",
            model="TN",
            category="Chaussures",
            keyword="TN",
            min_price_eur=None,
            max_price_eur=70.0,
            is_active=True,
        ),
    ]
    payload = build_user_alerts_payload(plan="starter", filters=rows, limit=5)
    embed = payload["embeds"][0]
    assert embed["title"] == "🔔 TES ALERTES PERSONNALISÉES"
    desc = embed["description"]
    assert "**Plan :** STARTER" in desc
    assert "**Filtres utilisés :** 2/5" in desc
    assert "Prix max : 70 €" in desc
    assert "🟢 Actif" in desc
    assert "Actions :" in desc
    components = payload["components"]
    assert len(components) == 4  # edit, pause, delete, create
    assert components[-1]["components"][0]["custom_id"] == "alert:create"


def test_build_user_alerts_empty() -> None:
    payload = build_user_alerts_payload(plan="premium", filters=[], limit=20)
    desc = payload["embeds"][0]["description"]
    assert "Aucune alerte" in desc
    assert "**Filtres utilisés :** 0/20" in desc
    assert len(payload["components"]) == 1
