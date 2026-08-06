"""Tests dédup fiches / niches détecteur."""

from vinted_bot.services.niche_product_sheets import (
    FICHES_SKIP_TTL_HOURS,
    MAX_POSTED_FICHES_KEPT,
    MAX_SKIPPED_FICHES_KEPT,
)


def test_fiche_dedup_retention_constants() -> None:
    # Postées : quasi permanent · Examinées : 30j, gros buffer
    assert MAX_POSTED_FICHES_KEPT >= 1000
    assert MAX_SKIPPED_FICHES_KEPT >= 500
    assert FICHES_SKIP_TTL_HOURS >= 168.0
