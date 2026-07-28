"""Tests DM Resello dashboard."""

from types import SimpleNamespace

from vinted_bot.services.resello_dm import (
    ALERT_DM_TOGGLE_PREFIX,
    build_resello_dm_dashboard_payload,
    filter_pretty_name,
    parse_dm_toggle_filter_id,
)


def test_filter_pretty_name() -> None:
    row = SimpleNamespace(
        name=None,
        brand="Nike",
        model="TN",
        keyword=None,
        max_price_eur=70.0,
    )
    assert filter_pretty_name(row) == "Nike TN < 70 €"


def test_build_resello_dm_dashboard() -> None:
    rows = [
        SimpleNamespace(
            id=10,
            name=None,
            brand="Nike",
            model="TN",
            category="Chaussures",
            keyword="TN",
            min_price_eur=None,
            max_price_eur=70.0,
            is_active=True,
        ),
        SimpleNamespace(
            id=11,
            name="Arc'teryx",
            brand="Arc'teryx",
            model=None,
            category="Veste",
            keyword=None,
            min_price_eur=None,
            max_price_eur=150.0,
            is_active=False,
        ),
    ]
    payload = build_resello_dm_dashboard_payload(
        plan="starter", filters=rows, limit=5
    )
    embed = payload["embeds"][0]
    assert embed["title"] == "🔔 TES FILTRES RESSELLO"
    desc = embed["description"]
    assert "Filtres actifs :** 1/5" in desc
    assert "Nike TN < 70 €" in desc
    assert "🟢 Surveillance active" in desc
    assert "🔴 Surveillance en pause" in desc
    assert "MES ALERTES" in desc or "Gestion complète" in desc

    components = payload["components"]
    assert len(components) == 1
    buttons = components[0]["components"]
    assert len(buttons) == 2
    assert buttons[0]["custom_id"] == f"{ALERT_DM_TOGGLE_PREFIX}10"
    assert "Désactiver" in buttons[0]["label"]
    assert buttons[1]["custom_id"] == f"{ALERT_DM_TOGGLE_PREFIX}11"
    assert "Réactiver" in buttons[1]["label"]


def test_parse_dm_toggle() -> None:
    assert parse_dm_toggle_filter_id("alert:dm_toggle:42") == 42
    assert parse_dm_toggle_filter_id("alert:create") is None
