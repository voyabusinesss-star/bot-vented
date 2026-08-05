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
    - toutes les marques YAML à chaque tour (spawn continu dès qu'une annonce match)
    """
    settings = get_settings()
    # Worker DM filtres privés (file async — ne bloque jamais le scrape)
    from vinted_bot.services.private_alert_queue import ensure_private_alert_worker

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
    restart_every = max(1, cfg.browser_restart_every_cycles)
    reconnect_delay = max(5.0, cfg.reconnect_delay_seconds)
    # Toutes les marques à chaque tour = spawn continu (pas de file d'attente)
    yaml_batch_size = max(1, len(active_searches_for_channels(
        settings.brand_channel_map(),
        sneaker_map=settings.sneaker_channel_map(),
    )) or 64)

    log.info(
        "loop_start",
        interval_seconds=interval,
        private_filter_scrape_interval_seconds=filter_interval,
        yaml_batch_size=yaml_batch_size,
        browser_restart_every_cycles=restart_every,
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

    browser: VintedBrowser | None = None
    cycles_on_browser = 0
    cycle = 0
    yaml_cycle = 0
    yaml_queue: list = []
    yaml_queue_pos = 0
    last_filter_pulse = 0.0

    try:
        while True:
            cycle += 1
            cycle_started = time.monotonic()
            try:
                # DB avant / hors greenlet Playwright (évite MissingGreenlet)
                filter_targets = active_filter_search_targets()
                ran_filters = False
                now = time.monotonic()

                if browser is None:
                    browser = VintedBrowser(
                        base_url=settings.vinted_base_url,
                        headless=headless,
                        delay_seconds=settings.request_delay_seconds,
                    )
                    browser.start()
                    browser.warm_up()
                    cycles_on_browser = 0


                # 1) Toujours prioriser les filtres privés dès que l'intervalle est écoulé
                if filter_targets and (now - last_filter_pulse) >= filter_interval:
                    log.info(
                        "loop_private_filter_pulse",
                        cycle=cycle,
                        targets=len(filter_targets),
                        queries=[t.query for t in filter_targets],
                    )
                    scrape_all_configured(
                        max_items=max_items,
                        headless=headless,
                        browser=browser,
                        targets=filter_targets,
                        cycle=cycle,
                        include_user_filters=False,
                    )
                    last_filter_pulse = time.monotonic()
                    ran_filters = True
                    cycles_on_browser += 1

                # 2) Toutes les marques dues ce cycle (pas de tranche partielle)
                channel_map = settings.brand_channel_map()
                sneaker_map = settings.sneaker_channel_map()
                all_targets = active_searches_for_channels(
                    channel_map, sneaker_map=sneaker_map
                )
                if not yaml_queue or yaml_queue_pos >= len(yaml_queue):
                    yaml_cycle += 1
                    yaml_queue = select_targets_for_cycle(
                        yaml_cycle, all_targets, cfg.priorities
                    )
                    yaml_queue_pos = 0
                    log.info(
                        "loop_yaml_queue_refill",
                        yaml_cycle=yaml_cycle,
                        due=len(yaml_queue),
                        brands=[t.brand for t in yaml_queue[:12]],
                    )

                batch = yaml_queue[yaml_queue_pos : yaml_queue_pos + yaml_batch_size]
                yaml_queue_pos += len(batch)
                if batch:
                    log.info(
                        "loop_yaml_batch",
                        cycle=cycle,
                        brands=[t.brand for t in batch],
                        remaining=max(0, len(yaml_queue) - yaml_queue_pos),
                    )
                    results = scrape_all_configured(
                        max_items=max_items,
                        headless=headless,
                        browser=browser,
                        targets=batch,
                        cycle=cycle,
                        include_user_filters=False,
                    )
                    cycles_on_browser += 1
                    log.info(
                        "loop_cycle_done",
                        cycle=cycle,
                        elapsed_seconds=round(time.monotonic() - cycle_started, 2),
                        searches=len(results),
                        created=sum(r.items_created for r in results),
                        posted=sum(r.items_posted_discord for r in results),
                        found=sum(r.items_found for r in results),
                        private_filter_pulse=ran_filters,
                        cycles_on_browser=cycles_on_browser,
                    )
                elif ran_filters:
                    log.info(
                        "loop_filter_only_done",
                        cycle=cycle,
                        elapsed_seconds=round(time.monotonic() - cycle_started, 2),
                    )
                else:
                    log.info("loop_idle", cycle=cycle)

                if cycles_on_browser >= restart_every:
                    log.info("loop_browser_recycle", cycle=cycle)
                    browser.stop()
                    browser = None
                    cycles_on_browser = 0

            except Exception as exc:
                log.exception("loop_cycle_failed", cycle=cycle, error=str(exc))
                if browser is not None:
                    try:
                        browser.stop()
                    except Exception:
                        pass
                    browser = None
                    cycles_on_browser = 0
                log.info("loop_reconnect_wait", seconds=reconnect_delay)
                time.sleep(reconnect_delay)
                continue

            # Zéro file d'attente artificielle : enchaîne immédiatement le prochain tour.
            remaining = max(0.2, float(interval))
            log.info("loop_sleep", seconds=round(remaining, 1), next_cycle=cycle + 1)
            time.sleep(remaining)
    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                pass
        log.info("loop_stopped", last_cycle=cycle)
