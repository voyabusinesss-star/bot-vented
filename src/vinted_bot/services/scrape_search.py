"""Service : scrape une recherche Vinted et upsert en base."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Sequence

from vinted_bot.clients.vinted_browser import VintedBrowser, vinted_browser
from vinted_bot.config import get_settings
from vinted_bot.config_loader import (
    SearchTarget,
    active_searches_for_channels,
    load_searches_config,
)
from vinted_bot.db.models import ScrapeRun
from vinted_bot.db.repositories import (
    create_scrape_run,
    finish_scrape_run,
    get_checkpoint,
    get_unposted_listings_by_vinted_ids,
    mark_discord_posted,
    set_checkpoint,
    upsert_listing,
)
from vinted_bot.db.session import session_scope
from vinted_bot.notify.discord import is_allowed_brand, publish_listings_to_discord
from vinted_bot.parsers.search import SearchItem, parse_catalog_payload
from vinted_bot.utils.logging import get_logger
from vinted_bot.utils.retry import retry_call

log = get_logger(__name__)


@dataclass(slots=True)
class ScrapeSearchResult:
    query: str
    items_found: int
    items_upserted: int
    items_created: int
    items_posted_discord: int
    items_skipped_brand: int
    bootstrap: bool
    scrape_run_id: int
    items: list[SearchItem] = field(default_factory=list)


def _checkpoint_key(query: str) -> str:
    return f"bootstrap:{query.strip().lower()}"


def _is_bootstrapped(query: str) -> bool:
    with session_scope() as session:
        return get_checkpoint(session, _checkpoint_key(query)) is not None


def _mark_bootstrapped(query: str, *, seen_ids: list[int]) -> None:
    with session_scope() as session:
        set_checkpoint(
            session,
            _checkpoint_key(query),
            {
                "bootstrapped": True,
                "seen_count": len(seen_ids),
                "vinted_ids": seen_ids[:50],
            },
        )


def scrape_search_once(
    query: str,
    *,
    max_items: int = 24,
    headless: bool = True,
    base_url: str | None = None,
    browser: VintedBrowser | None = None,
    expected_brand: str | None = None,
    brand_ids: Sequence[int] | None = None,
    catalog_ids: Sequence[int] | None = None,
    order: str = "newest_first",
    max_discord_posts: int | None = None,
) -> ScrapeSearchResult:
    settings = get_settings()
    base = base_url or settings.vinted_base_url
    per_page = max(1, min(max_items, 96))
    owns_browser = browser is None

    with session_scope() as session:
        run = create_scrape_run(session, query=query)
        run_id = run.id

    items: list[SearchItem] = []
    created_count = 0
    upserted = 0
    skipped_brand = 0
    created_vinted_ids: list[int] = []
    bootstrap = not _is_bootstrapped(query)

    try:
        def _run_with_browser(active: VintedBrowser) -> None:
            nonlocal items, skipped_brand, created_count, upserted, created_vinted_ids

            payload = retry_call(
                lambda: active.search_catalog(
                    query,
                    page=1,
                    per_page=per_page,
                    order=order,
                    brand_ids=brand_ids,
                    catalog_ids=catalog_ids,
                ),
                max_retries=settings.max_retries,
                label=f"catalog:{query}",
            )
            raw_items = parse_catalog_payload(payload, base_url=base)[:max_items]
            brand_map = settings.brand_channel_map()
            if expected_brand:
                items = [
                    item
                    for item in raw_items
                    if is_allowed_brand(item.brand, {expected_brand: "x"})
                ]
                skipped_brand = len(raw_items) - len(items)
            elif brand_map:
                items = [
                    item
                    for item in raw_items
                    if is_allowed_brand(item.brand, brand_map)
                ]
                skipped_brand = len(raw_items) - len(items)
            else:
                items = raw_items

            log.info(
                "search_parsed",
                query=query,
                found=len(raw_items),
                kept=len(items),
                skipped_brand=skipped_brand,
                bootstrap=bootstrap,
                order=order,
            )

            with session_scope() as session:
                for item in items:
                    _, created = upsert_listing(
                        session,
                        vinted_id=item.vinted_id,
                        title=item.title,
                        url=item.url,
                        price_cents=item.price_cents,
                        currency=item.currency,
                        brand=item.brand,
                        size=item.size,
                        photo_urls=item.photo_urls,
                        raw_json=item.raw_json,
                    )
                    upserted += 1
                    if created:
                        created_count += 1
                        created_vinted_ids.append(item.vinted_id)

        if owns_browser:
            with vinted_browser(
                base_url=base,
                headless=headless,
                delay_seconds=settings.request_delay_seconds,
            ) as active:
                active.warm_up()
                _run_with_browser(active)
        else:
            assert browser is not None
            _run_with_browser(browser)

    except Exception as exc:
        log.exception("scrape_search_failed", query=query, error=str(exc))
        with session_scope() as session:
            run = session.get(ScrapeRun, run_id)
            if run is not None:
                finish_scrape_run(
                    session,
                    run,
                    status="failed",
                    items_found=len(items),
                    items_upserted=upserted,
                    error=str(exc),
                )
        raise

    with session_scope() as session:
        run = session.get(ScrapeRun, run_id)
        assert run is not None
        finish_scrape_run(
            session,
            run,
            status="success",
            items_found=len(items),
            items_upserted=upserted,
        )

    all_vinted_ids = [item.vinted_id for item in items]

    # Premier passage : snapshot sans spam Discord
    if bootstrap:
        with session_scope() as session:
            unposted = get_unposted_listings_by_vinted_ids(session, all_vinted_ids)
            mark_discord_posted(session, [listing.id for listing in unposted])
        _mark_bootstrapped(query, seen_ids=all_vinted_ids)
        log.info(
            "bootstrap_done",
            query=query,
            marked=len(all_vinted_ids),
            created=created_count,
        )
        return ScrapeSearchResult(
            query=query,
            items_found=len(items),
            items_upserted=upserted,
            items_created=created_count,
            items_posted_discord=0,
            items_skipped_brand=skipped_brand,
            bootstrap=True,
            scrape_run_id=run_id,
            items=items,
        )

    # Live : Discord uniquement pour les VRAIES nouvelles (inserts de ce run)
    # Limite les rafales : poste les N plus récentes, marque le reste comme vu.
    with session_scope() as session:
        to_post = get_unposted_listings_by_vinted_ids(session, created_vinted_ids)
        to_post.sort(key=lambda listing: listing.vinted_id, reverse=True)
        post_cap = max_discord_posts
        if post_cap is None:
            post_cap = load_searches_config().max_discord_posts
        post_cap = max(0, int(post_cap))
        announce = to_post[:post_cap] if post_cap else to_post
        silent = to_post[post_cap:] if post_cap else []
        for listing in announce:
            for photo in list(listing.photos):
                session.expunge(photo)
            session.expunge(listing)
        if silent:
            mark_discord_posted(session, [listing.id for listing in silent])
            log.info(
                "discord_cap_skipped",
                query=query,
                skipped=len(silent),
                posted_cap=post_cap,
            )

    posted_ids = publish_listings_to_discord(
        announce,
        query=query,
        items_found=len(items),
        items_upserted=upserted,
        scrape_run_id=run_id,
        settings=settings,
    )
    posted = len(posted_ids)
    if posted_ids:
        with session_scope() as session:
            mark_discord_posted(session, posted_ids)

    log.info(
        "scrape_done",
        query=query,
        found=len(items),
        created=created_count,
        updated=upserted - created_count,
        new_for_discord=len(announce),
        posted=posted,
        skipped_brand=skipped_brand,
        bootstrap=False,
    )

    return ScrapeSearchResult(
        query=query,
        items_found=len(items),
        items_upserted=upserted,
        items_created=created_count,
        items_posted_discord=posted,
        items_skipped_brand=skipped_brand,
        bootstrap=False,
        scrape_run_id=run_id,
        items=items,
    )


def scrape_all_configured(
    *,
    max_items: int | None = None,
    headless: bool = True,
    browser: VintedBrowser | None = None,
    targets: list[SearchTarget] | None = None,
    cycle: int | None = None,
) -> list[ScrapeSearchResult]:
    """Scrape les recherches actives (ou une liste filtrée par priorité)."""
    settings = get_settings()
    searches_cfg = load_searches_config()
    channel_map = settings.brand_channel_map()
    all_targets = targets if targets is not None else active_searches_for_channels(channel_map)
    default_max = max_items or searches_cfg.max_items

    if not all_targets:
        log.warning("scrape_all_no_targets")
        return []

    log.info(
        "scrape_all_start",
        targets=len(all_targets),
        brands=[t.brand for t in all_targets],
        cycle=cycle,
        max_items=default_max,
    )

    results: list[ScrapeSearchResult] = []

    def _run(active: VintedBrowser) -> None:
        for index, target in enumerate(all_targets):
            from vinted_bot.config_loader import resolve_policy

            policy = resolve_policy(target, searches_cfg.priorities)
            per_search = max_items or policy.max_items or default_max
            started = time.monotonic()
            log.info(
                "scrape_all_target",
                index=index + 1,
                total=len(all_targets),
                brand=target.brand,
                query=target.query,
                priority=target.priority,
                max_items=per_search,
                max_discord_posts=policy.max_discord_posts,
                brand_ids=target.brand_ids,
                catalog_ids=target.catalog_ids,
            )
            try:
                result = scrape_search_once(
                    target.query,
                    max_items=per_search,
                    headless=headless,
                    browser=active,
                    expected_brand=target.brand,
                    brand_ids=target.brand_ids or None,
                    catalog_ids=target.catalog_ids or None,
                    order=target.order or searches_cfg.order,
                    max_discord_posts=(
                        policy.max_discord_posts
                        if policy.max_discord_posts is not None
                        else searches_cfg.max_discord_posts
                    ),
                )
                duration = round(time.monotonic() - started, 2)
                results.append(result)
                log.info(
                    "scrape_all_target_done",
                    brand=target.brand,
                    priority=target.priority,
                    duration_seconds=duration,
                    created=result.items_created,
                    posted=result.items_posted_discord,
                    found=result.items_found,
                )
            except Exception as exc:
                duration = round(time.monotonic() - started, 2)
                log.exception(
                    "scrape_all_target_failed",
                    brand=target.brand,
                    query=target.query,
                    duration_seconds=duration,
                    error=str(exc),
                )
            if index < len(all_targets) - 1:
                time.sleep(searches_cfg.delay_between_searches_seconds)

    if browser is not None:
        _run(browser)
    else:
        with vinted_browser(
            base_url=settings.vinted_base_url,
            headless=headless,
            delay_seconds=settings.request_delay_seconds,
        ) as active:
            active.warm_up()
            _run(active)

    log.info(
        "scrape_all_done",
        searches=len(results),
        created=sum(r.items_created for r in results),
        posted=sum(r.items_posted_discord for r in results),
        bootstraps=sum(1 for r in results if r.bootstrap),
    )
    return results
