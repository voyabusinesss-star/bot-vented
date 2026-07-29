"""Tests intro salon Vintify."""

from vinted_bot.interactions.vintify_intro_panel import (
    DEFAULT_VINTIFY_URL,
    PREVIEW_FILENAME,
    build_vintify_intro_payload,
)


def test_vintify_intro_payload() -> None:
    payload = build_vintify_intro_payload(site_url=DEFAULT_VINTIFY_URL)
    embed = payload["embeds"][0]
    assert "Vintify" in embed["title"]
    assert "C'est quoi ?" in embed["description"]
    assert "Essayage virtuel" in embed["description"]
    field = embed["fields"][0]["value"]
    assert "Essaie dès maintenant" in field
    assert f"]({DEFAULT_VINTIFY_URL})" in field
    assert payload["components"][0]["components"][0]["url"] == DEFAULT_VINTIFY_URL
    assert embed["image"]["url"] == f"attachment://{PREVIEW_FILENAME}"
