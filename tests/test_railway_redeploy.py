"""Tests auto-redeploy Railway sur 403 persistants."""

from __future__ import annotations

import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from vinted_bot.config import Settings
from vinted_bot.services import scrape_block_tracker as sbt
from vinted_bot.services.railway_redeploy import (
    maybe_auto_redeploy_on_403,
    redeploy_cooldown_remaining,
    trigger_service_redeploy,
)


@contextmanager
def _reset_tracker():
    with sbt._lock:
        sbt._tracker.consecutive_403 = 0
        sbt._tracker.last_403_at = None
        sbt._tracker.last_catalog_ok_at = None
        sbt._tracker.total_403 = 0
        sbt._tracker._recent_403_at.clear()
    yield


def _redeploy_settings(**overrides) -> Settings:
    base = dict(
        scrape_auto_redeploy_enabled=True,
        scrape_proxy_urls=[],
        scrape_403_redeploy_threshold=8,
        scrape_auto_redeploy_cooldown_seconds=1800.0,
        railway_api_token="tok",
        railway_service_id="svc",
        railway_environment_id="env",
        discord_bot_token="",
        discord_channel_logs="",
    )
    base.update(overrides)
    return Settings(**base)


@patch("vinted_bot.services.railway_redeploy.get_settings")
def test_maybe_auto_redeploy_skipped_when_disabled(mock_settings) -> None:
    mock_settings.return_value = _redeploy_settings(scrape_auto_redeploy_enabled=False)
    with _reset_tracker():
        with sbt._lock:
            sbt._tracker.consecutive_403 = 10
        assert maybe_auto_redeploy_on_403() is False


@patch("vinted_bot.services.railway_redeploy.get_settings")
def test_maybe_auto_redeploy_skipped_when_proxy_configured(mock_settings) -> None:
    mock_settings.return_value = _redeploy_settings(
        scrape_proxy_urls=["http://u:p@proxy:80"]
    )
    with _reset_tracker():
        with sbt._lock:
            sbt._tracker.consecutive_403 = 10
        assert maybe_auto_redeploy_on_403() is False


@patch("vinted_bot.services.railway_redeploy.trigger_service_redeploy")
@patch("vinted_bot.services.railway_redeploy.redeploy_cooldown_remaining", return_value=600.0)
@patch("vinted_bot.services.railway_redeploy.get_settings")
def test_maybe_auto_redeploy_skipped_during_cooldown(
    mock_settings, mock_cooldown, mock_trigger
) -> None:
    mock_settings.return_value = _redeploy_settings()
    with _reset_tracker():
        with sbt._lock:
            sbt._tracker.consecutive_403 = 10
        assert maybe_auto_redeploy_on_403() is False
    mock_trigger.assert_not_called()


@patch("vinted_bot.services.railway_redeploy._post_redeploy_alert")
@patch("vinted_bot.services.railway_redeploy.trigger_service_redeploy", return_value=True)
@patch("vinted_bot.services.railway_redeploy.redeploy_cooldown_remaining", return_value=0.0)
@patch("vinted_bot.services.railway_redeploy.get_settings")
def test_maybe_auto_redeploy_triggers_at_threshold(
    mock_settings, mock_cooldown, mock_trigger, mock_alert
) -> None:
    mock_settings.return_value = _redeploy_settings()
    with _reset_tracker():
        with sbt._lock:
            sbt._tracker.consecutive_403 = 8
        assert maybe_auto_redeploy_on_403() is True
    mock_trigger.assert_called_once_with(reason="403_threshold")
    mock_alert.assert_called_once()


@patch("vinted_bot.services.railway_redeploy.set_checkpoint")
@patch("vinted_bot.services.railway_redeploy.session_scope")
@patch("httpx.Client")
@patch("vinted_bot.services.railway_redeploy.get_settings")
def test_trigger_service_redeploy_graphql_success(
    mock_settings, mock_client_cls, mock_session_scope, mock_set_checkpoint
) -> None:
    mock_settings.return_value = _redeploy_settings()
    mock_session = MagicMock()
    mock_session_scope.return_value.__enter__.return_value = mock_session

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"data":{"serviceInstanceRedeploy":true}}'
    mock_resp.json.return_value = {"data": {"serviceInstanceRedeploy": True}}
    mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_resp

    assert trigger_service_redeploy(reason="test") is True
    mock_set_checkpoint.assert_called_once()


@patch("vinted_bot.services.railway_redeploy.get_checkpoint", return_value={"ts": time.time() - 100})
@patch("vinted_bot.services.railway_redeploy.session_scope")
def test_redeploy_cooldown_remaining(mock_session_scope, mock_get_checkpoint) -> None:
    mock_session_scope.return_value.__enter__.return_value = MagicMock()
    remaining = redeploy_cooldown_remaining(cooldown_seconds=1800.0)
    assert 1690 <= remaining <= 1710
