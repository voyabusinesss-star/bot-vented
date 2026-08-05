"""Pool de workers scrape permanents (24/7).

Chaque worker possède un navigateur Playwright sticky + un groupe de marques,
poll en boucle 2–5 s, déduplique via la DB, poste Discord sur nouvel ID.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Sequence

from vinted_bot.clients.vinted_browser import VintedBrowser
from vinted_bot.config import get_settings
from vinted_bot.config_loader import (
    PRIORITY_RANK,
    SearchTarget,
    active_searches_for_channels,
    load_searches_config,
    resolve_policy,
)
from vinted_bot.services.filter_scrape_targets import active_filter_search_targets
from vinted_bot.services.scrape_heartbeat import write_scrape_heartbeat
from vinted_bot.services.scrape_search import scrape_search_once
from vinted_bot.utils.logging import get_logger
from vinted_bot.utils.proxy import assign_proxy_for_worker, rotate_proxy

log = get_logger(__name__)


def partition_targets(
    targets: Sequence[SearchTarget],
    n_workers: int,
) -> list[list[SearchTarget]]:
    """Répartit les cibles en groupes sticky (high priority d'abord, round-robin)."""
    n = max(1, int(n_workers))
    ordered = sorted(
        targets,
        key=lambda t: (
            PRIORITY_RANK.get(t.priority, 9),
            t.brand,
            t.query,
            tuple(t.catalog_ids),
        ),
    )
    buckets: list[list[SearchTarget]] = [[] for _ in range(n)]
    for i, target in enumerate(ordered):
        buckets[i % n].append(target)
    return [b for b in buckets if b]


def _poll_sleep(min_s: float, max_s: float) -> None:
    lo = max(0.2, float(min_s))
    hi = max(lo, float(max_s))
    time.sleep(random.uniform(lo, hi))


def _scrape_target(
    target: SearchTarget,
    *,
    browser: VintedBrowser,
    headless: bool,
    max_items: int | None,
) -> tuple[int, int, int, int]:
    """Retourne (created, posted, found, skipped_deal)."""
    searches_cfg = load_searches_config()
    policy = resolve_policy(target, searches_cfg.priorities)
    per_search = max_items or policy.max_items or searches_cfg.max_items
    if target.max_discord_posts is not None:
        discord_cap = target.max_discord_posts
    elif policy.max_discord_posts is not None:
        discord_cap = policy.max_discord_posts
    else:
        discord_cap = searches_cfg.max_discord_posts

    is_user_filter = getattr(target, "source", "yaml") == "user_filter"
    if is_user_filter:
        discord_cap = 0

    expected_brand = target.brand
    skip_brand_filter = False
    keep_text = False
    if is_user_filter:
        keep_text = True
        if not target.brand or target.brand == "filter":
            expected_brand = None
            skip_brand_filter = True
        else:
            skip_brand_filter = True

    result = scrape_search_once(
        target.query,
        max_items=per_search,
        headless=headless,
        browser=browser,
        expected_brand=expected_brand,
        brand_ids=target.brand_ids or None,
        catalog_ids=target.catalog_ids or None,
        order=target.order or searches_cfg.order,
        max_discord_posts=discord_cap,
        price_from=getattr(target, "price_from", None),
        price_to=getattr(target, "price_to", None),
        skip_brand_channel_filter=skip_brand_filter,
        keep_search_text=keep_text,
    )
    return (
        result.items_created,
        result.items_posted_discord,
        result.items_found,
        result.items_skipped_deal,
    )


class BrandWorker:
    """Thread permanent : un navigateur + un groupe de marques."""

    def __init__(
        self,
        *,
        worker_id: int,
        targets: list[SearchTarget],
        proxy_url: str | None,
        all_proxies: list[str],
        headless: bool = True,
        max_items: int | None = None,
        poll_min: float = 2.0,
        poll_max: float = 5.0,
        restart_every: int = 40,
        reconnect_delay: float = 10.0,
        start_delay: float = 0.0,
    ) -> None:
        self.worker_id = worker_id
        self.targets = list(targets)
        self.proxy_url = proxy_url
        self.all_proxies = list(all_proxies)
        self.headless = headless
        self.max_items = max_items
        self.poll_min = poll_min
        self.poll_max = poll_max
        self.restart_every = max(1, restart_every)
        self.reconnect_delay = max(5.0, reconnect_delay)
        self.start_delay = float(start_delay)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._browser: VintedBrowser | None = None
        self._successes = 0
        self._cycle = 0

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name=f"scrape-brand-{self.worker_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._close_browser()

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _close_browser(self) -> None:
        if self._browser is None:
            return
        try:
            self._browser.stop()
        except Exception:  # noqa: BLE001
            pass
        self._browser = None

    def _ensure_browser(self) -> VintedBrowser:
        settings = get_settings()
        if self._browser is None:
            self._browser = VintedBrowser(
                base_url=settings.vinted_base_url,
                headless=self.headless,
                delay_seconds=settings.request_delay_seconds,
                proxy_url=self.proxy_url,
            )
            self._browser.start()
            self._browser.warm_up()
            log.info(
                "brand_worker_browser_ready",
                worker_id=self.worker_id,
                brands=len(self.targets),
                proxy=bool(self.proxy_url),
            )
        return self._browser

    def _recycle_browser(self, *, rotate: bool) -> None:
        if rotate and self.all_proxies:
            self.proxy_url = rotate_proxy(self.all_proxies, self.proxy_url)
            log.info(
                "brand_worker_proxy_rotate",
                worker_id=self.worker_id,
                has_proxy=bool(self.proxy_url),
            )
        if self._browser is not None:
            try:
                self._browser.restart(proxy_url=self.proxy_url)
            except Exception:  # noqa: BLE001
                self._close_browser()
                self._ensure_browser()
        else:
            self._ensure_browser()
        self._successes = 0

    def _run(self) -> None:
        if self.start_delay > 0:
            time.sleep(self.start_delay)
        log.info(
            "brand_worker_start",
            worker_id=self.worker_id,
            brands=[t.brand for t in self.targets],
            proxy=bool(self.proxy_url),
        )
        write_scrape_heartbeat(
            cycle=0,
            status="worker_start",
            worker_id=self.worker_id,
            brands=len(self.targets),
        )
        while not self._stop.is_set():
            self._cycle += 1
            cycle_posted = 0
            cycle_created = 0
            cycle_found = 0
            cycle_skipped = 0
            try:
                browser = self._ensure_browser()
                for target in self.targets:
                    if self._stop.is_set():
                        break
                    started = time.monotonic()
                    try:
                        created, posted, found, skipped = _scrape_target(
                            target,
                            browser=browser,
                            headless=self.headless,
                            max_items=self.max_items,
                        )
                        cycle_created += created
                        cycle_posted += posted
                        cycle_found += found
                        cycle_skipped += skipped
                        self._successes += 1
                        log.info(
                            "brand_worker_target_done",
                            worker_id=self.worker_id,
                            brand=target.brand,
                            catalog_ids=target.catalog_ids,
                            duration_seconds=round(time.monotonic() - started, 2),
                            created=created,
                            posted=posted,
                            found=found,
                            skipped_deal=skipped,
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.exception(
                            "brand_worker_target_failed",
                            worker_id=self.worker_id,
                            brand=target.brand,
                            error=str(exc)[:200],
                        )
                        self._close_browser()
                        time.sleep(self.reconnect_delay)
                        browser = self._ensure_browser()

                    if self._successes >= self.restart_every:
                        self._recycle_browser(rotate=True)
                        browser = self._ensure_browser()

                    _poll_sleep(self.poll_min, self.poll_max)

                write_scrape_heartbeat(
                    cycle=self._cycle,
                    worker_id=self.worker_id,
                    posted=cycle_posted,
                    created=cycle_created,
                    found=cycle_found,
                    skipped_deal=cycle_skipped,
                    brands=len(self.targets),
                    brand_names=[t.brand for t in self.targets[:8]],
                )
            except Exception as exc:  # noqa: BLE001
                log.exception(
                    "brand_worker_cycle_failed",
                    worker_id=self.worker_id,
                    error=str(exc)[:200],
                )
                write_scrape_heartbeat(
                    cycle=self._cycle,
                    worker_id=self.worker_id,
                    status="error",
                    error=str(exc)[:200],
                )
                self._close_browser()
                time.sleep(self.reconnect_delay)

        self._close_browser()
        log.info("brand_worker_stopped", worker_id=self.worker_id)


class FilterWorker:
    """Thread permanent pour les filtres privés (DM)."""

    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        all_proxies: list[str] | None = None,
        headless: bool = True,
        poll_min: float = 2.0,
        poll_max: float = 5.0,
        filter_interval: float = 8.0,
        reconnect_delay: float = 10.0,
    ) -> None:
        self.proxy_url = proxy_url
        self.all_proxies = list(all_proxies or [])
        self.headless = headless
        self.poll_min = poll_min
        self.poll_max = poll_max
        self.filter_interval = max(3.0, float(filter_interval))
        self.reconnect_delay = max(5.0, reconnect_delay)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._browser: VintedBrowser | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="scrape-filters",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._browser is not None:
            try:
                self._browser.stop()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        settings = get_settings()
        last_pulse = 0.0
        log.info("filter_worker_start", interval=self.filter_interval)
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_pulse < self.filter_interval:
                time.sleep(min(1.0, self.filter_interval - (now - last_pulse)))
                continue
            try:
                targets = active_filter_search_targets()
                if not targets:
                    last_pulse = time.monotonic()
                    time.sleep(self.filter_interval)
                    continue
                if self._browser is None:
                    self._browser = VintedBrowser(
                        base_url=settings.vinted_base_url,
                        headless=self.headless,
                        delay_seconds=settings.request_delay_seconds,
                        proxy_url=self.proxy_url,
                    )
                    self._browser.start()
                    self._browser.warm_up()
                log.info(
                    "filter_worker_pulse",
                    targets=len(targets),
                    queries=[t.query for t in targets][:12],
                )
                for target in targets:
                    if self._stop.is_set():
                        break
                    try:
                        _scrape_target(
                            target,
                            browser=self._browser,
                            headless=self.headless,
                            max_items=None,
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.exception(
                            "filter_worker_target_failed",
                            query=target.query,
                            error=str(exc)[:160],
                        )
                        if self._browser is not None:
                            try:
                                self._browser.stop()
                            except Exception:  # noqa: BLE001
                                pass
                            self._browser = None
                        time.sleep(self.reconnect_delay)
                        break
                    _poll_sleep(self.poll_min, self.poll_max)
                last_pulse = time.monotonic()
            except Exception as exc:  # noqa: BLE001
                log.exception("filter_worker_failed", error=str(exc)[:200])
                if self._browser is not None:
                    try:
                        self._browser.stop()
                    except Exception:  # noqa: BLE001
                        pass
                    self._browser = None
                if self.all_proxies:
                    self.proxy_url = rotate_proxy(self.all_proxies, self.proxy_url)
                time.sleep(self.reconnect_delay)
        log.info("filter_worker_stopped")


def run_permanent_scrape_pool(
    *,
    max_items: int | None = None,
    headless: bool = True,
) -> None:
    """Démarre les workers permanents et survit 24/7 (restart des threads morts)."""
    from vinted_bot.services.private_alert_queue import ensure_private_alert_worker

    ensure_private_alert_worker()
    settings = get_settings()
    cfg = load_searches_config()
    n_workers = max(1, int(settings.scrape_parallel_workers))
    poll_min = float(settings.scrape_poll_seconds_min)
    poll_max = float(settings.scrape_poll_seconds_max)
    proxies = list(settings.scrape_proxy_urls or [])
    restart_every = max(1, cfg.browser_restart_every_cycles)
    reconnect = max(5.0, cfg.reconnect_delay_seconds)
    filter_interval = float(
        getattr(settings, "private_filter_scrape_interval_seconds", 8.0) or 8.0
    )

    channel_map = settings.brand_channel_map()
    sneaker_map = settings.sneaker_channel_map()
    all_targets = active_searches_for_channels(channel_map, sneaker_map=sneaker_map)
    groups = partition_targets(all_targets, n_workers)

    log.info(
        "permanent_pool_start",
        workers=len(groups),
        total_targets=len(all_targets),
        poll_min=poll_min,
        poll_max=poll_max,
        proxies=len(proxies),
        group_sizes=[len(g) for g in groups],
    )
    write_scrape_heartbeat(
        cycle=0,
        status="pool_start",
        workers=len(groups),
        brands=len(all_targets),
        proxies=len(proxies),
    )

    brand_workers: list[BrandWorker] = []
    for i, group in enumerate(groups):
        proxy = assign_proxy_for_worker(proxies, i)
        w = BrandWorker(
            worker_id=i,
            targets=group,
            proxy_url=proxy,
            all_proxies=proxies,
            headless=headless,
            max_items=max_items,
            poll_min=poll_min,
            poll_max=poll_max,
            restart_every=restart_every,
            reconnect_delay=reconnect,
            start_delay=float(i) * 15.0,
        )
        w.start()
        brand_workers.append(w)

    # Filtre worker : démarre après le 1er brand (évite 2 Chromium au boot)
    time.sleep(45.0 if len(groups) <= 1 else max(5.0, float(len(groups)) * 5.0))
    filter_worker = FilterWorker(
        proxy_url=assign_proxy_for_worker(proxies, len(groups)) if proxies else None,
        all_proxies=proxies,
        headless=headless,
        poll_min=poll_min,
        poll_max=poll_max,
        filter_interval=filter_interval,
        reconnect_delay=reconnect,
    )
    filter_worker.start()

    try:
        while True:
            time.sleep(30.0)
            # Relance un brand worker mort
            for idx, w in enumerate(list(brand_workers)):
                if w.is_alive():
                    continue
                log.warning("brand_worker_dead_restart", worker_id=w.worker_id)
                w.stop()
                proxy = assign_proxy_for_worker(proxies, w.worker_id)
                # Recharge les cibles (salons / yaml peuvent changer)
                fresh = active_searches_for_channels(
                    settings.brand_channel_map(),
                    sneaker_map=settings.sneaker_channel_map(),
                )
                fresh_groups = partition_targets(fresh, max(1, len(groups)))
                group = (
                    fresh_groups[w.worker_id]
                    if w.worker_id < len(fresh_groups)
                    else (fresh_groups[0] if fresh_groups else w.targets)
                )
                nw = BrandWorker(
                    worker_id=w.worker_id,
                    targets=group,
                    proxy_url=proxy,
                    all_proxies=proxies,
                    headless=headless,
                    max_items=max_items,
                    poll_min=poll_min,
                    poll_max=poll_max,
                    restart_every=restart_every,
                    reconnect_delay=reconnect,
                )
                nw.start()
                brand_workers[idx] = nw
            if not filter_worker.is_alive():
                log.warning("filter_worker_dead_restart")
                filter_worker.stop()
                filter_worker = FilterWorker(
                    proxy_url=assign_proxy_for_worker(proxies, len(groups))
                    if proxies
                    else None,
                    all_proxies=proxies,
                    headless=headless,
                    poll_min=poll_min,
                    poll_max=poll_max,
                    filter_interval=filter_interval,
                    reconnect_delay=reconnect,
                )
                filter_worker.start()
    except KeyboardInterrupt:
        log.info("permanent_pool_interrupt")
    finally:
        for w in brand_workers:
            w.stop()
        filter_worker.stop()
        write_scrape_heartbeat(cycle=0, status="pool_stopped")
        log.info("permanent_pool_stopped")
