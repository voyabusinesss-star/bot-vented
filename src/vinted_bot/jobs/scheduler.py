"""Boucle de scraping continue (24/7) — pool de workers permanents."""

from __future__ import annotations

from vinted_bot.jobs.scrape_workers import run_permanent_scrape_pool
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)


def run_scrape_loop(
    *,
    max_items: int | None = None,
    headless: bool = True,
    interval_seconds: float | None = None,
) -> None:
    """
    Point d'entrée CLI ``scrape --loop``.

    ``interval_seconds`` est ignoré : chaque worker poll 2–5 s entre ses filtres
    (voir SCRAPE_POLL_SECONDS_MIN/MAX).
    """
    if interval_seconds is not None:
        log.info(
            "loop_interval_ignored",
            hint="permanent workers use SCRAPE_POLL_SECONDS_MIN/MAX",
            interval_seconds=interval_seconds,
        )
    run_permanent_scrape_pool(max_items=max_items, headless=headless)
