"""Boucle de scraping continue (24/7) avec priorités."""

from __future__ import annotations

import time

from vinted_bot.clients.vinted_browser import VintedBrowser
from vinted_bot.config import get_settings
from vinted_bot.config_loader import (
    active_searches_for_channels,
    load_searches_config,
    select_targets_for_cycle,
)
from vinted_bot.services.filter_scrape_targets import active_filter_search_targets
from vinted_bot.services.scrape_search import scrape_all_configured
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)


def run_scrape_loop(
    *,
    max_items: int | None = None,
    headless: bool = True,
    interval_seconds: float | None = None,
) -> None:
    """
    Tourne indéfiniment :
    - pulse fréquent des **filtres privés** (alertes DM)
    - **toutes** les marques YAML en parallèle chaque tour
    """
    settings = get_settings()
    from vinted_bot.services.private_alert_queue import ensure_private_alert_worker
    from vinted_bot.services.scrape_heartbeat import write_scrape_heartbeat

    ensure_private_alert_worker()

    cfg = load_searches_config()
    interval = (
        interval_seconds
        if interval_seconds is not None
        else cfg.loop_interval_seconds
    )
    filter_interval = float(
        getattr(settings, "private_filter_scrape_interval_seconds", 20.0) or 20.0
    )
    reconnect_delay = max(5.0, cfg.reconnect_delay_seconds)
    parallel_workers = max(1, int(getattr(settings, "scrape_parallel_workers", 6) or 6))

    log.info(
        "loop_start",
        interval_seconds=interval,
        private_filter_scrape_interval_seconds=filter_interval,
        scrape_parallel_workers=parallel_workers,
        max_items=max_items or cfg.max_items,
        priorities={
            name: {
                "every_n_cycles": p.every_n_cycles,
                "extra_passes": p.extra_passes,
                "max_items": p.max_items,
                "max_discord_posts": p.max_discord_posts,
            }
            for name, p in cfg.priorities.items()
        },
    )
    write_scrape_heartbeat(
        cycle=0, status="starting", workers=parallel_workers
    )

    filter_browser: VintedBrowser | None = None
    cycle = 0
    yaml_cycle = 0
    last_filter_pulse = 0.0

    try:
        while True:
            cycle += 1
            cycle_started = time.monotonic()
            try:
                filter_targets = active_filter_search_targets()
                ran_filters = False
                now = time.monotonic()

                # 1) Filtres privés (navigateur dédié, séquentiel)
                if filter_targets and (now - last_filter_pulse) >= filter_interval:
                    if filter_browser is None:
                        filter_browser = VintedBrowser(
                            base_url=settings.vinted_base_url,
                            headless=headless,
                            delay_seconds=settings.request_delay_seconds,
                        )
                        filter_browser.start()
                        filter_browser.warm_up()
                    log.info(
                        "loop_private_filter_pulse",
                        cycle=cycle,
                        targets=len(filter_targets),
                        queries=[t.query for t in filter_targets],
                    )
                    scrape_all_configured(
                        max_items=max_items,
                        headless=headless,
                        browser=filter_browser,
                        targets=filter_targets,
                        cycle=cycle,
                        include_user_filters=False,
                        workers=1,
                    )
                    last_filter_pulse = time.monotonic()
                    ran_filters = True

                # 2) Toutes les marques en parallèle (N navigateurs)
                channel_map = settings.brand_channel_map()
                sneaker_map = settings.sneaker_channel_map()
                all_targets = active_searches_for_channels(
                    channel_map, sneaker_map=sneaker_map
                )
                yaml_cycle += 1
                due = select_targets_for_cycle(
                    yaml_cycle, all_targets, cfg.priorities
                )
                write_scrape_heartbeat(
                    cycle=cycle,
                    status="scraping",
                    brands=len(due),
                    workers=parallel_workers,
                )
                log.info(
                    "loop_yaml_parallel",
                    cycle=cycle,
                    brands=len(due),
                    workers=parallel_workers,
                    sample=[t.brand for t in due[:12]],
                )
                results = scrape_all_configured(
                    max_items=max_items,
                    headless=headless,
                    browser=None,
                    targets=due,
                    cycle=cycle,
                    include_user_filters=False,
                    workers=parallel_workers,
                )
                log.info(
                    "loop_cycle_done",
                    cycle=cycle,
                    elapsed_seconds=round(time.monotonic() - cycle_started, 2),
                    searches=len(results),
                    created=sum(r.items_created for r in results),
                    posted=sum(r.items_posted_discord for r in results),
                    found=sum(r.items_found for r in results),
                    skipped_deal=sum(r.items_skipped_deal for r in results),
                    private_filter_pulse=ran_filters,
                    workers=parallel_workers,
                )
                write_scrape_heartbeat(
                    cycle=cycle,
                    posted=sum(r.items_posted_discord for r in results),
                    created=sum(r.items_created for r in results),
                    found=sum(r.items_found for r in results),
                    skipped_deal=sum(r.items_skipped_deal for r in results),
                    brands=len(results),
                    workers=parallel_workers,
                )

            except Exception as exc:
                log.exception("loop_cycle_failed", cycle=cycle, error=str(exc))
                write_scrape_heartbeat(
                    cycle=cycle,
                    status="error",
                    error=str(exc)[:200],
                )
                if filter_browser is not None:
                    try:
                        filter_browser.stop()
                    except Exception:
                        pass
                    filter_browser = None
                log.info("loop_reconnect_wait", seconds=reconnect_delay)
                time.sleep(reconnect_delay)
                continue

            remaining = max(0.2, float(interval))
            log.info("loop_sleep", seconds=round(remaining, 1), next_cycle=cycle + 1)
            time.sleep(remaining)
    finally:
        if filter_browser is not None:
            try:
                filter_browser.stop()
            except Exception:
                pass
        log.info("loop_stopped", last_cycle=cycle)
