"""Tests intro salon niches vinted."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from vinted_bot.interactions.niches_vinted_intro_panel import (
    CATALOG_FILENAME,
    build_niches_vinted_intro_payload,
    load_niches_vinted_catalog_bytes,
    resolve_niches_vinted_catalog_path,
)


def test_niches_vinted_intro_payload() -> None:
    url = "https://cdn.discordapp.com/attachments/1/2/file.xlsx"
    payload = build_niches_vinted_intro_payload(
        catalog_filename=CATALOG_FILENAME,
        download_url=url,
    )
    embed = payload["embeds"][0]
    assert embed["title"] == "📊 1000 Niches Vinted Rentables — Accès Gratuit"
    assert "C'est quoi ?" in embed["description"]
    assert "À l'intérieur" in embed["description"]
    assert "prix d'achat conseillé" in embed["description"]
    field_value = embed["fields"][0]["value"]
    assert "Un clic, tout le catalogue" in field_value
    assert f"]({url})" in field_value
    assert "TÉLÉCHARGER LES 1000 NICHES" in field_value
    assert payload["components"][0]["components"][0]["url"] == url
    assert payload["components"][0]["components"][0]["label"] == "📥 TÉLÉCHARGER LES 1000 NICHES"


def test_niches_vinted_intro_payload_draft_without_url() -> None:
    payload = build_niches_vinted_intro_payload(
        catalog_filename=CATALOG_FILENAME,
        download_url="",
    )
    embed = payload["embeds"][0]
    assert "Un clic, tout le catalogue" in embed["fields"][0]["value"]
    assert "components" not in payload
    assert "url" not in embed


def test_load_niches_vinted_catalog_bytes(tmp_path: Path) -> None:
    catalog = tmp_path / CATALOG_FILENAME
    catalog.write_bytes(b"fake-xlsx")
    data, name = load_niches_vinted_catalog_bytes(str(catalog))
    assert data == b"fake-xlsx"
    assert name == CATALOG_FILENAME


def test_load_niches_vinted_catalog_missing() -> None:
    with pytest.raises(FileNotFoundError):
        load_niches_vinted_catalog_bytes("/nonexistent/file.xlsx")


def test_resolve_niches_vinted_catalog_path_relative() -> None:
    path = resolve_niches_vinted_catalog_path(f"config/{CATALOG_FILENAME}")
    assert path.name == CATALOG_FILENAME
    assert "config" in str(path)
