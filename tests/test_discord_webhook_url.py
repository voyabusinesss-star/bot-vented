"""Tests parsing URL webhook Discord."""

from vinted_bot.interactions.discord_api import parse_discord_webhook_url


def test_parse_discord_webhook_url() -> None:
    url = "https://discord.com/api/webhooks/123456789/AbCdEfGhIjKlMnOp"
    parsed = parse_discord_webhook_url(url)
    assert parsed == ("123456789", "AbCdEfGhIjKlMnOp")

    assert parse_discord_webhook_url("") is None
    assert parse_discord_webhook_url("https://example.com") is None
