"""Tests filtre anti-bruit tendances commerciales."""

from __future__ import annotations

from vinted_bot.services.market_trends_filter import (
    extract_commercial_phrases,
    is_commercially_relevant_phrase,
    is_generic_token,
)


def test_generic_adjectives_and_parts_rejected() -> None:
    assert is_generic_token("grand")
    assert is_generic_token("manches")
    assert is_generic_token("neuf")
    assert is_generic_token("noir")
    assert is_generic_token("xl")
    assert is_generic_token("42")
    assert is_generic_token("bon")


def test_commercial_phrases_accepted() -> None:
    assert is_commercially_relevant_phrase("detroit jacket")
    assert is_commercially_relevant_phrase("air max 95")
    assert is_commercially_relevant_phrase("hello kitty")
    assert is_commercially_relevant_phrase("nintendo switch")
    assert is_commercially_relevant_phrase("gore tex")
    assert is_commercially_relevant_phrase("pokemon")


def test_generic_phrases_rejected() -> None:
    assert not is_commercially_relevant_phrase("grand")
    assert not is_commercially_relevant_phrase("manches longues")
    assert not is_commercially_relevant_phrase("bon etat")
    assert not is_commercially_relevant_phrase("taille m")
    assert not is_commercially_relevant_phrase("noir")
    assert not is_commercially_relevant_phrase("veste")


def test_extract_prefers_known_entities() -> None:
    phrases = extract_commercial_phrases(
        "Nike Air Max 95 OG noir taille 42 état neuf"
    )
    keys = {f"{e}:{k}" for e, k, _ in phrases}
    assert any("air_max_95" in k or "air max 95" in d.lower() for e, k, d in phrases) or any(
        "model:" in f for f in keys
    )
    # Pas de taille / couleur / état
    joined = " ".join(k for _, k, _ in phrases)
    assert "42" not in joined
    assert "noir" not in joined.split("_")
    assert "neuf" not in joined


def test_extract_hello_kitty_and_switch() -> None:
    p1 = extract_commercial_phrases("Peluche Hello Kitty Sanrio rare")
    assert any("sanrio" in k or "hello" in d.lower() for _, k, d in p1)
    p2 = extract_commercial_phrases("Nintendo Switch OLED blanche")
    assert any("nintendo" in d.lower() or "switch" in d.lower() for _, _, d in p2)
