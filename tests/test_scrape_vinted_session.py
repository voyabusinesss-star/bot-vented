"""Tests session Vinted dédiée scrape."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from vinted_bot.config import Settings
from vinted_bot.services.scrape_vinted_session import (
    load_scrape_storage_state,
    save_scrape_storage_state,
    scrape_session_configured,
)


@patch("vinted_bot.services.scrape_vinted_session.get_checkpoint", return_value=None)
@patch("vinted_bot.services.scrape_vinted_session.session_scope")
@patch("vinted_bot.config.get_settings")
def test_scrape_session_from_env(mock_settings, mock_session_scope, mock_get_cp) -> None:
    mock_settings.return_value = Settings(
        vinted_scrape_session="mytokenvalue12345678",
    )
    mock_session_scope.return_value.__enter__.return_value = MagicMock()
    state = load_scrape_storage_state()
    assert state is not None
    assert state["cookies"][0]["name"] == "access_token_web"
    assert scrape_session_configured()


@patch("vinted_bot.services.scrape_vinted_session.set_checkpoint")
@patch("vinted_bot.services.scrape_vinted_session.session_scope")
def test_save_scrape_storage_state(mock_session_scope, mock_set_cp) -> None:
    mock_session_scope.return_value.__enter__.return_value = MagicMock()
    save_scrape_storage_state(
        {"cookies": [{"name": "access_token_web", "value": "x", "domain": ".vinted.fr"}]},
        vinted_username="bot_scrape",
    )
    mock_set_cp.assert_called_once()
