"""Tests blocage bande passante warm-up Playwright."""

from __future__ import annotations

from vinted_bot.clients.vinted_browser import should_block_bandwidth_request


def test_should_block_images_and_fonts() -> None:
    assert should_block_bandwidth_request("image", "https://cdn.vinted.fr/a.jpg") is True
    assert should_block_bandwidth_request("font", "https://cdn.vinted.fr/f.woff2") is True
    assert should_block_bandwidth_request("stylesheet", "https://vinted.fr/app.css") is True


def test_should_block_analytics_urls() -> None:
    assert (
        should_block_bandwidth_request(
            "script",
            "https://www.googletagmanager.com/gtag/js?id=UA-1",
        )
        is True
    )


def test_should_allow_document_and_api() -> None:
    assert (
        should_block_bandwidth_request(
            "document",
            "https://www.vinted.fr/",
        )
        is False
    )
    assert (
        should_block_bandwidth_request(
            "fetch",
            "https://www.vinted.fr/api/v2/catalog/items?page=1",
        )
        is False
    )
