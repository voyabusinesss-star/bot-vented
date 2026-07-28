"""Tests routing Discord."""

from vinted_bot.notify.discord import (
    belongs_in_all_vetement,
    build_listing_payload,
    is_allowed_brand,
    is_classique_brand,
    normalize_brand,
    route_channel,
)


def test_normalize_brand() -> None:
    assert normalize_brand("Nike") == "nike"
    assert normalize_brand("Adidas Originals") == "adidas originals"
    assert normalize_brand("Polo Ralph Lauren") == "ralph lauren"
    assert normalize_brand("Carhartt WIP") == "carhartt"
    assert normalize_brand("Yves Saint Laurent") == "saint laurent"
    assert normalize_brand("Under Armor") == "under armour"
    assert normalize_brand(None) == ""
    assert normalize_brand("") == ""


def test_route_channel_by_brand() -> None:
    channels = {
        "nike": "111",
        "adidas": "222",
        "ralph lauren": "333",
    }
    assert route_channel("Nike", channels) == "111"
    assert route_channel("adidas originals", channels) == "222"
    assert route_channel("Polo Ralph Lauren", channels) == "333"
    assert route_channel("Puma", channels) is None
    assert route_channel(None, channels) is None


def test_route_channel_sneakers_override() -> None:
    clothing = {"nike": "111"}
    sneakers = {"nike": "999", "jordan": "888"}
    assert route_channel("Nike", clothing, sneaker_map=sneakers, is_shoe=True) == "999"
    assert route_channel("Nike", clothing, sneaker_map=sneakers, is_shoe=False) == "111"
    assert route_channel("Jordan", clothing, sneaker_map=sneakers, is_shoe=True) == "888"
    assert normalize_brand("On Running") == "on cloud"
    assert normalize_brand("Dr. Martens") == "dr martens"


def test_route_channel_shoe_never_falls_back_to_clothing() -> None:
    clothing = {"nike": "111", "carhartt": "222"}
    sneakers = {"jordan": "888"}  # pas de canal Nike sneakers
    assert route_channel("Nike", clothing, sneaker_map=sneakers, is_shoe=True) is None
    assert route_channel("Nike", clothing, sneaker_map=None, is_shoe=True) is None
    assert route_channel("Carhartt", clothing, sneaker_map=sneakers, is_shoe=False) == "222"


def test_is_classique_brand() -> None:
    assert is_classique_brand("Nike") is True
    assert is_classique_brand("Carhartt WIP") is True
    assert is_classique_brand("Louis Vuitton") is False
    assert is_classique_brand("Jordan") is False


def test_is_allowed_brand() -> None:
    channels = {"nike": "111"}
    assert is_allowed_brand("Nike", channels) is True
    assert is_allowed_brand("Puma", channels) is False
    assert is_allowed_brand("Jordan", channels, sneaker_map={"jordan": "888"}) is True


def test_belongs_in_all_vetement() -> None:
    sneaker_ids = {"999", "888"}
    assert belongs_in_all_vetement("Nike", is_shoe=False) is True
    assert belongs_in_all_vetement("Nike", is_shoe=True) is False
    assert belongs_in_all_vetement("Jordan", is_shoe=True) is False
    assert belongs_in_all_vetement("Jordan", is_shoe=False) is False
    assert belongs_in_all_vetement("Louis Vuitton", is_shoe=False) is False
    assert belongs_in_all_vetement("Moncler", is_shoe=False) is False
    assert belongs_in_all_vetement("New Balance", is_shoe=False) is False
    assert belongs_in_all_vetement("Carhartt", is_shoe=False) is True
    assert belongs_in_all_vetement("adidas originals", is_shoe=False) is True
    # Posté dans un salon Pépites Sneakers → jamais #all-vetement
    assert (
        belongs_in_all_vetement(
            "Nike",
            is_shoe=False,
            brand_channel_id="999",
            sneaker_channel_ids=sneaker_ids,
        )
        is False
    )
    # Salon vêtement classique Nike → OK
    assert (
        belongs_in_all_vetement(
            "Nike",
            is_shoe=False,
            brand_channel_id="111",
            sneaker_channel_ids=sneaker_ids,
        )
        is True
    )


