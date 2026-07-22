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
    Tourne indéfiniment avec cycles intelligents :
    - sélection des recherches selon priorité (high/medium/low)
    - pause configurable entre cycles
    - recyclage navigateur périodique
    - reprise auto après erreur
    - métriques + détection de ralentissement
    """
    settings = get_settings()
    cfg = load_searches_config()
    interval = (
        interval_seconds
        if interval_seconds is not None
        else cfg.loop_interval_seconds
    )
    restart_every = max(1, cfg.browser_restart_every_cycles)
    reconnect_delay = max(5.0, cfg.reconnect_delay_seconds)
    slow_factor = max(1.1, cfg.slow_cycle_factor)

    log.info(
        "loop_start",
        interval_seconds=interval,
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
    cycle_durations: list[float] = []

    try:
        while True:
            cycle += 1
            cycle_started = time.monotonic()
            channel_map = settings.brand_channel_map()
            all_targets = active_searches_for_channels(channel_map)
            due_targets = select_targets_for_cycle(
                cycle, all_targets, cfg.priorities
            )

            by_priority: dict[str, int] = {}
            for t in due_targets:
                by_priority[t.priority] = by_priority.get(t.priority, 0) + 1

            log.info(
                "loop_cycle_start",
                cycle=cycle,
                due=len(due_targets),
                total_configured=len(all_targets),
                by_priority=by_priority,
                brands=[t.brand for t in due_targets],
            )

            if not due_targets:
                log.info("loop_cycle_skip_empty", cycle=cycle)
                time.sleep(interval)
                continue

            try:
                if browser is None:
                    browser = VintedBrowser(
                        base_url=settings.vinted_base_url,
                        headless=headless,
                        delay_seconds=settings.request_delay_seconds,
                    )
                    browser.start()
                    browser.warm_up()
                    cycles_on_browser = 0

                results = scrape_all_configured(
                    max_items=max_items,
                    headless=headless,
                    browser=browser,
                    targets=due_targets,
                    cycle=cycle,
                )
                cycles_on_browser += 1
                elapsed = round(time.monotonic() - cycle_started, 2)
                cycle_durations.append(elapsed)
                # moyenne mobile sur les 10 derniers cycles
                window = cycle_durations[-10:]
                avg = sum(window) / len(window)
                avg_search = (
                    round(elapsed / max(1, len(results)), 2) if results else 0.0
                )

                log.info(
                    "loop_cycle_done",
                    cycle=cycle,
                    elapsed_seconds=elapsed,
                    avg_search_seconds=avg_search,
                    avg_cycle_seconds=round(avg, 2),
                    searches=len(results),
                    created=sum(r.items_created for r in results),
                    posted=sum(r.items_posted_discord for r in results),
                    found=sum(r.items_found for r in results),
                    cycles_on_browser=cycles_on_browser,
                )

                if len(window) >= 3 and elapsed > avg * slow_factor:
                    log.warning(
                        "loop_slowdown_detected",
                        cycle=cycle,
                        elapsed_seconds=elapsed,
                        avg_cycle_seconds=round(avg, 2),
                        factor=slow_factor,
                    )

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

            log.info("loop_sleep", seconds=interval, next_cycle=cycle + 1)
            time.sleep(interval)
    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                pass
        log.info("loop_stopped", last_cycle=cycle)
