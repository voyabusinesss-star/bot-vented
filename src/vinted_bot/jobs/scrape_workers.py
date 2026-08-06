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
    target_poll_interval_seconds,
)
from vinted_bot.jobs.discord_outbox import DiscordFlushWorker
from vinted_bot.services.filter_scrape_targets import active_filter_search_targets
from vinted_bot.services.scrape_heartbeat import (
    read_scrape_heartbeat,
    write_scrape_heartbeat,
)
from vinted_bot.services.scrape_search import scrape_search_once
from vinted_bot.utils.logging import get_logger
from vinted_bot.utils.proxy import assign_proxy_for_worker, rotate_proxy

log = get_logger(__name__)
_last_silence_alert_at = 0.0


def _post_scrape_ops_alert(message: str) -> None:
    """Ping #logs si le scrape est silencieux (best-effort, rate-limité)."""
    global _last_silence_alert_at
    now = time.time()
    if now - _last_silence_alert_at < 600.0:
        return
    settings = get_settings()
    channel = (settings.discord_channel_logs or "").strip()
    token = (settings.discord_bot_token or "").strip()
    if not channel or not token:
        return
    try:
        import httpx

        url = f"https://discord.com/api/v10/channels/{channel}/messages"
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(
                url,
                headers={
                    "Authorization": f"Bot {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "embeds": [
                        {
                            "title": "Scrape silence / instabilité",
                            "description": message[:1800],
                            "color": 0xE67E22,
                        }
                    ]
                },
            )
        if resp.status_code in (200, 201):
            _last_silence_alert_at = now
            log.warning("scrape_silence_alert_sent", status=resp.status_code)
        else:
            log.warning(
                "scrape_silence_alert_failed",
                status=resp.status_code,
                body=resp.text[:120],
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("scrape_silence_alert_error", error=str(exc)[:160])


def _check_scrape_silence(*, silence_after: float) -> None:
    data = read_scrape_heartbeat()
    if not data:
        _post_scrape_ops_alert(
            "Aucun heartbeat scrape — workers peut-être down / Chromium bloqué."
        )
        return
    try:
        age = time.time() - float(data.get("ts") or 0)
    except (TypeError, ValueError):
        age = 9999.0
    if age < silence_after:
        return
    status = data.get("status") or "unknown"
    _post_scrape_ops_alert(
        f"Pas d'activité scrape depuis **{int(age)}s** "
        f"(status=`{status}`, cycle=`{data.get('cycle')}`, "
        f"posted=`{data.get('posted')}`). "
        "Vérifier Chromium / Railway RAM."
    )
    write_scrape_heartbeat(
        cycle=data.get("cycle"),
        status="silence_alert",
        silence_age_s=int(age),
        posted=data.get("posted"),
    )


def partition_targets(
    targets: Sequence[SearchTarget],
    n_workers: int,
) -> list[list[SearchTarget]]:
    """Répartit les cibles en groupes sticky équilibrés (pas de file low en fin)."""
    n = max(1, int(n_workers))
    # Round-robin pur sur liste triée marque — chaque salon a une place équitable
    ordered = sorted(
        targets,
        key=lambda t: (
            t.brand,
            t.query,
            tuple(t.catalog_ids),
        ),
    )
    buckets: list[list[SearchTarget]] = [[] for _ in range(n)]
    for i, target in enumerate(ordered):
        buckets[i % n].append(target)
    return [b for b in buckets if b]


def _target_key(target: SearchTarget) -> tuple[str, str, tuple[int, ...]]:
    return (target.brand, target.query, tuple(target.catalog_ids))


def _pick_due_target(
    targets: Sequence[SearchTarget],
    next_run: dict[tuple[str, str, tuple[int, ...]], float],
    *,
    now: float,
) -> SearchTarget | None:
    due: list[tuple[float, SearchTarget]] = []
    for target in targets:
        key = _target_key(target)
        when = next_run.get(key, 0.0)
        if when <= now:
            due.append((when, target))
    if not due:
        return None
    # High d'abord (fraîcheur salons concurrencés), puis le plus en retard
    due.sort(
        key=lambda item: (
            PRIORITY_RANK.get(item[1].priority, 9),
            item[0],
            item[1].brand,
            item[1].query,
        )
    )
    return due[0][1]


def _seconds_until_next(
    targets: Sequence[SearchTarget],
    next_run: dict[tuple[str, str, tuple[int, ...]], float],
    *,
    now: float,
) -> float:
    waits = [next_run.get(_target_key(t), now) - now for t in targets]
    if not waits:
        return 1.0
    return max(0.05, min(waits))


def _poll_sleep(min_s: float, max_s: float) -> None:
    lo = max(0.05, float(min_s))
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
        defer_discord=not is_user_filter,
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
        self._next_run: dict[tuple[str, str, tuple[int, ...]], float] = {
            _target_key(t): 0.0 for t in self.targets
        }
        self._last_scrape_at: dict[tuple[str, str, tuple[int, ...]], float] = {}
        self._revisit_samples: list[float] = []
        self._last_activity = time.monotonic()

    def last_activity_age(self) -> float:
        return max(0.0, time.monotonic() - self._last_activity)

    def _touch(self) -> None:
        self._last_activity = time.monotonic()

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
            # force_stop : évite de bloquer 55s+ si Playwright est coincé
            force = getattr(self._browser, "force_stop", None)
            if callable(force):
                force()
            else:
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
            try:
                self._browser.warm_up()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "brand_worker_warmup_soft_fail",
                    worker_id=self.worker_id,
                    error=str(exc)[:160],
                )
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
            self._touch()
            cycle_posted = 0
            cycle_created = 0
            cycle_found = 0
            cycle_skipped = 0
            try:
                browser = self._ensure_browser()
                now = time.monotonic()
                target = _pick_due_target(self.targets, self._next_run, now=now)
                if target is None:
                    wait_s = _seconds_until_next(
                        self.targets, self._next_run, now=now
                    )
                    self._stop.wait(min(wait_s, 2.0))
                    continue

                key = _target_key(target)
                started = time.monotonic()
                last = self._last_scrape_at.get(key)
                seconds_since = (started - last) if last is not None else None
                try:
                    created, posted, found, skipped = _scrape_target(
                        target,
                        browser=browser,
                        headless=self.headless,
                        max_items=self.max_items,
                    )
                    self._touch()
                    cycle_created += created
                    cycle_posted += posted
                    cycle_found += found
                    cycle_skipped += skipped
                    self._successes += 1
                    self._last_scrape_at[key] = time.monotonic()
                    if seconds_since is not None:
                        self._revisit_samples.append(float(seconds_since))
                        if len(self._revisit_samples) >= 20:
                            samples = sorted(self._revisit_samples)
                            self._revisit_samples.clear()
                            n = len(samples)
                            p50 = samples[n // 2]
                            p95 = samples[min(n - 1, int(n * 0.95))]
                            log.info(
                                "scrape_revisit_p50_p95",
                                worker_id=self.worker_id,
                                p50_seconds=round(p50, 1),
                                p95_seconds=round(p95, 1),
                                n=n,
                            )
                    log.info(
                        "brand_worker_target_done",
                        worker_id=self.worker_id,
                        brand=target.brand,
                        catalog_ids=target.catalog_ids,
                        duration_seconds=round(time.monotonic() - started, 2),
                        seconds_since_last_scrape=(
                            round(seconds_since, 1)
                            if seconds_since is not None
                            else None
                        ),
                        created=created,
                        posted=posted,
                        found=found,
                        skipped_deal=skipped,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._touch()
                    log.exception(
                        "brand_worker_target_failed",
                        worker_id=self.worker_id,
                        brand=target.brand,
                        error=str(exc)[:200],
                    )
                    crashed = "crashed" in str(exc).lower() or "Target closed" in str(exc)
                    self._close_browser()
                    # Crash renderer : restart immédiat ; autre erreur : petite pause
                    if not crashed:
                        time.sleep(self.reconnect_delay)
                    else:
                        time.sleep(min(2.0, self.reconnect_delay))
                    browser = self._ensure_browser()
                    self._touch()

                interval = target_poll_interval_seconds(target)
                self._next_run[key] = time.monotonic() + interval

                if self._successes >= self.restart_every:
                    self._recycle_browser(rotate=True)
                    browser = self._ensure_browser()

                # Petite pause anti-burst entre scrapes (pas le cadence marque)
                lo = max(0.05, float(self.poll_min))
                hi = max(lo, float(self.poll_max))
                time.sleep(random.uniform(lo, hi))

                write_scrape_heartbeat(
                    cycle=self._cycle,
                    worker_id=self.worker_id,
                    posted=cycle_posted,
                    created=cycle_created,
                    found=cycle_found,
                    skipped_deal=cycle_skipped,
                    brands=len(self.targets),
                    brand_names=[target.brand],
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
    # Railway RAM : 1 Chromium brand = stable ; 2+ → OOM / Target crashed
    if n_workers > 1:
        log.warning(
            "scrape_workers_clamped",
            requested=n_workers,
            clamped=1,
            hint="Anti-OOM Railway — force 1 brand worker Chromium",
        )
        n_workers = 1
    poll_min = float(settings.scrape_poll_seconds_min)
    poll_max = float(settings.scrape_poll_seconds_max)
    proxies = list(settings.scrape_proxy_urls or [])
    restart_every = max(1, min(20, cfg.browser_restart_every_cycles))
    reconnect = max(3.0, min(cfg.reconnect_delay_seconds, 10.0))
    filter_interval = float(
        getattr(settings, "private_filter_scrape_interval_seconds", 8.0) or 8.0
    )
    filter_enabled = bool(
        getattr(settings, "scrape_filter_worker_enabled", False)
    )
    silence_after = float(
        getattr(settings, "scrape_silence_alert_seconds", 120.0) or 120.0
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
        filter_worker=filter_enabled,
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
            start_delay=float(i) * 12.0,
        )
        w.start()
        brand_workers.append(w)

    flush_worker = DiscordFlushWorker(
        poll_seconds=0.4,
        buffer_seconds=0.2,
        max_messages=5,
    )
    flush_worker.start()

    from vinted_bot.jobs.db_retention import DbRetentionWorker

    retention_worker = DbRetentionWorker(interval_seconds=300.0)
    retention_worker.start()

    filter_worker: FilterWorker | None = None
    if filter_enabled:
        # 2e Chromium = risque OOM — démarrage retardé
        time.sleep(60.0)
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
    else:
        log.info("filter_worker_disabled", reason="scrape_filter_worker_enabled=false")

    try:
        while True:
            time.sleep(20.0)
            _check_scrape_silence(silence_after=silence_after)
            # Au-dessus du pire cas catalog (CALL_TIMEOUT ~55s × 2 retries + marge).
            # Un seuil trop bas tue le worker EN PLEIN scrape → spiral Chromium/OOM.
            stuck_after = 200.0
            for idx, w in enumerate(list(brand_workers)):
                stuck = w.is_alive() and w.last_activity_age() > stuck_after
                if w.is_alive() and not stuck:
                    continue
                log.warning(
                    "brand_worker_dead_restart",
                    worker_id=w.worker_id,
                    stuck=stuck,
                    idle_seconds=(
                        round(w.last_activity_age(), 1) if stuck else None
                    ),
                )
                w.stop()
                w.join(timeout=20.0)
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
            if filter_worker is not None and not filter_worker.is_alive():
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
        if filter_worker is not None:
            filter_worker.stop()
        flush_worker.stop()
        flush_worker.join(timeout=15.0)
        retention_worker.stop()
        write_scrape_heartbeat(cycle=0, status="pool_stopped")
        log.info("permanent_pool_stopped")
