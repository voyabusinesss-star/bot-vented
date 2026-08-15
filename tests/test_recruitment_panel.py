"""Tests panneau / helpers tickets recrutement."""

from __future__ import annotations

from vinted_bot.interactions.recruitment_panel import (
    RECRUIT_CLOSE,
    RECRUIT_OPEN,
    build_recruitment_panel_payload,
    build_ticket_candidature_payload,
    build_ticket_overwrites,
    find_open_ticket_channel,
    format_ticket_transcript,
    parse_ticket_opener_id,
    sanitize_ticket_channel_name,
    ticket_topic_for_user,
)


def test_sanitize_ticket_channel_name() -> None:
    assert sanitize_ticket_channel_name("FaustinAU") == "recrutement-faustinau"
    assert sanitize_ticket_channel_name("Jean Dupont!") == "recrutement-jean-dupont"
    assert sanitize_ticket_channel_name("").startswith("recrutement-")


def test_ticket_topic_roundtrip() -> None:
    topic = ticket_topic_for_user(902559525666717747)
    assert topic == "recruit:902559525666717747"
    assert parse_ticket_opener_id(topic) == "902559525666717747"
    assert parse_ticket_opener_id("autre") == ""


def test_build_recruitment_panel_payload() -> None:
    payload = build_recruitment_panel_payload()
    assert payload["embeds"][0]["title"]
    button = payload["components"][0]["components"][0]
    assert button["custom_id"] == RECRUIT_OPEN


def test_build_ticket_candidature_payload() -> None:
    payload = build_ticket_candidature_payload(
        opener_mention="<@123>",
        staff_mention="<@&456>",
    )
    desc = payload["embeds"][0]["description"]
    assert "Âge" in desc
    assert "Poste demandé" in desc
    assert payload["embeds"][0]["title"]
    button = payload["components"][0]["components"][0]
    assert button["custom_id"] == RECRUIT_CLOSE
    assert button["style"] == 4


def test_build_ticket_overwrites() -> None:
    ows = build_ticket_overwrites(
        everyone_id="1",
        opener_user_id="2",
        bot_user_id="3",
        staff_role_id="4",
    )
    by_id = {str(o["id"]): o for o in ows}
    assert by_id["1"]["deny"] == str(1 << 10)  # VIEW deny
    assert by_id["1"]["type"] == 0
    assert by_id["2"]["type"] == 1  # member
    assert int(by_id["2"]["allow"]) & (1 << 10)
    assert int(by_id["2"]["allow"]) & (1 << 11)  # send messages
    assert int(by_id["4"]["allow"]) & (1 << 10)


def test_find_open_ticket_channel() -> None:
    channels = [
        {
            "id": "10",
            "type": 0,
            "parent_id": "cat",
            "topic": "recruit:42",
            "name": "recrutement-alice",
        },
        {
            "id": "11",
            "type": 0,
            "parent_id": "other",
            "topic": "recruit:42",
            "name": "recrutement-alice",
        },
    ]
    found = find_open_ticket_channel(channels, category_id="cat", user_id="42")
    assert found is not None
    assert found["id"] == "10"
    found_other = find_open_ticket_channel(channels, category_id="missing", user_id="42")
    assert found_other is not None
    assert found_other["id"] == "10"
    assert find_open_ticket_channel(channels, category_id="cat", user_id="99") is None


def test_format_ticket_transcript() -> None:
    messages = [
        {
            "id": "2",
            "timestamp": "2026-07-31T09:40:00+00:00",
            "author": {"username": "alice", "discriminator": "0"},
            "content": "J'ai 22 ans",
            "embeds": [],
            "attachments": [],
        },
        {
            "id": "1",
            "timestamp": "2026-07-31T09:35:00+00:00",
            "author": {"username": "Resello", "bot": True, "discriminator": "0000"},
            "content": "",
            "embeds": [{"title": "CANDIDATURE", "description": "questions"}],
            "attachments": [],
        },
    ]
    text = format_ticket_transcript(messages)
    assert "---- LOG DE TICKET ----" in text
    assert "alice" in text
    assert "J'ai 22 ans" in text
    # Chronological: embed first then alice reply
    assert text.index("CANDIDATURE") < text.index("J'ai 22 ans")
