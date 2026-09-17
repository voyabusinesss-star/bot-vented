"""Tests OAuth refresh Vinted."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from vinted_bot.services.scrape_vinted_session import hydrate_scrape_storage_state
from vinted_bot.services.vinted_oauth import exchange_refresh_token


@patch("httpx.Client")
def test_exchange_refresh_token_web_client(mock_client_cls) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "access_token": "new_access",
        "refresh_token": "new_refresh",
    }
    mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_resp

    out = exchange_refresh_token("old_refresh")
    assert out["access_token"] == "new_access"
    assert out["refresh_token"] == "new_refresh"


@patch("vinted_bot.services.scrape_vinted_session.save_scrape_storage_state")
@patch("vinted_bot.services.scrape_vinted_session.exchange_refresh_token")
def test_hydrate_from_refresh_only(mock_exchange, mock_save) -> None:
    mock_exchange.return_value = {
        "access_token": "acc",
        "refresh_token": "ref",
    }
    state = {
        "cookies": [
            {"name": "refresh_token_web", "value": "ref", "domain": ".vinted.fr"}
        ],
        "origins": [],
    }
    out = hydrate_scrape_storage_state(state)
    assert out is not None
    names = {c["name"] for c in out["cookies"]}
    assert "access_token_web" in names
    assert "refresh_token_web" in names
    mock_save.assert_called_once()
