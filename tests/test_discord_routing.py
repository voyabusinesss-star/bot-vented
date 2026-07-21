"""Tests routing Discord."""

from vinted_bot.notify.discord import is_allowed_brand, normalize_brand, route_channel


def test_normalize_brand() -> None:
    assert normalize_brand("Nike") == "nike"
    assert normalize_brand("Adidas Originals") == "adidas originals"
    assert normalize_brand(None) == ""
    assert normalize_brand("") == ""


def test_route_channel_by_brand() -> None:
    channels = {
        "nike": "111",
        "adidas": "222",
    }
    assert route_channel("Nike", channels) == "111"
    assert route_channel("adidas originals", channels) == "222"
    assert route_channel("Puma", channels) is None
    assert route_channel(None, channels) is None


def test_is_allowed_brand() -> None:
    channels = {"nike": "111"}
    assert is_allowed_brand("Nike", channels) is True
    assert is_allowed_brand("Puma", channels) is False
