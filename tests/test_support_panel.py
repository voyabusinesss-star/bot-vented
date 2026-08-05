"""Tests panneau tickets aide."""

from __future__ import annotations

from vinted_bot.interactions.support_panel import (
    SUPPORT_CLOSE,
    SUPPORT_OPEN,
    build_support_panel_payload,
    build_support_ticket_payload,
    find_open_support_ticket,
    parse_ticket_opener_id,
    sanitize_support_channel_name,
    ticket_topic_for_user,
)


def test_sanitize_support_channel_name() -> None:
    assert sanitize_support_channel_name("FaustinAU") == "aide-faustinau"


def test_support_topic_roundtrip() -> None:
    topic = ticket_topic_for_user(42)
    assert topic == "aide:42"
    assert parse_ticket_opener_id(topic) == "42"
    assert parse_ticket_opener_id("recruit:42") == ""


def test_build_support_panel_payload() -> None:
    payload = build_support_panel_payload()
    assert payload["embeds"][0]["title"]
    assert payload["components"][0]["components"][0]["custom_id"] == SUPPORT_OPEN


def test_build_support_ticket_payload() -> None:
    payload = build_support_ticket_payload(
        opener_mention="<@1>",
        staff_mention="<@&2>",
    )
    assert "Besoin" in payload["embeds"][0]["description"] or "problème" in payload["embeds"][0]["description"]
    assert payload["components"][0]["components"][0]["custom_id"] == SUPPORT_CLOSE


def test_find_open_support_ticket() -> None:
    channels = [
        {
            "id": "10",
            "type": 0,
            "parent_id": "cat",
            "topic": "aide:42",
            "name": "aide-alice",
        },
        {
            "id": "11",
            "type": 0,
            "parent_id": "cat",
            "topic": "recruit:42",
            "name": "recrutement-alice",
        },
    ]
    found = find_open_support_ticket(channels, category_id="cat", user_id="42")
    assert found is not None
    assert found["id"] == "10"
