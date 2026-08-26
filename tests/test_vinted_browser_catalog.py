"""Tests anti-crash catalogue Vinted (402 proxy, backoff)."""

from __future__ import annotations

import pytest

from vinted_bot.clients.vinted_browser import (
    _catalog_response_blocked,
    catalog_error_backoff_seconds,
    CatalogFetchBlockedError,
)


def test_catalog_response_blocked_402_bandwidth() -> None:
    blocked, proxy_exhausted, penalty = _catalog_response_blocked(
        402,
        "Bandwidth limit reached. Please upgrade to continue using the proxy.",
    )
    assert blocked is True
    assert proxy_exhausted is True
    assert penalty == 600.0


def test_catalog_response_blocked_403() -> None:
    blocked, proxy_exhausted, penalty = _catalog_response_blocked(403, "forbidden")
    assert blocked is True
    assert proxy_exhausted is False
    assert penalty == 90.0


def test_catalog_error_backoff_proxy_exhausted() -> None:
    exc = CatalogFetchBlockedError(
        status=402,
        reason="proxy_bandwidth_exhausted",
        proxy_exhausted=True,
    )
    assert catalog_error_backoff_seconds(exc) == 600.0


def test_catalog_error_backoff_thread_limit() -> None:
    assert (
        catalog_error_backoff_seconds(RuntimeError("can't start new thread"))
        == 120.0
    )


def test_playwright_proxy_invalid_raises() -> None:
    from vinted_bot.clients.vinted_browser import VintedBrowser

    browser = VintedBrowser(proxy_url='["http://bad"]')
    with pytest.raises(RuntimeError, match="SCRAPE_PROXY_URLS invalide"):
        browser._start_impl()
