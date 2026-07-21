"""Service : scrape une recherche Vinted et upsert en base."""

from __future__ import annotations

from dataclasses import dataclass

from vinted_bot.clients.vinted_browser import vinted_browser
from vinted_bot.config import get_settings
from vinted_bot.db.models import ScrapeRun
from vinted_bot.db.repositories import (
    create_scrape_run,
    finish_scrape_run,
    get_unposted_listings_by_vinted_ids,
    mark_discord_posted,
    upsert_listing,
)
from vinted_bot.db.session import session_scope
from vinted_bot.notify.discord import is_allowed_brand, publish_listings_to_discord
from vinted_bot.parsers.search import SearchItem, parse_catalog_payload
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class ScrapeSearchResult:
    query: str
    items_found: int
    items_upserted: int
    items_posted_discord: int
    scrape_run_id: int
    items: list[SearchItem]


def scrape_search_once(
    query: str,
    *,
    max_items: int = 24,
    headless: bool = True,
    base_url: str | None = None,
) -> ScrapeSearchResult:
    settings = get_settings()
    base = base_url or settings.vinted_base_url
    per_page = max(1, min(max_items, 96))

    with session_scope() as session:
        run = create_scrape_run(session, query=query)
        run_id = run.id

    items: list[SearchItem] = []
    upserted = 0
    posted = 0

    try:
        with vinted_browser(
            base_url=base,
            headless=headless,
            delay_seconds=settings.request_delay_seconds,
        ) as browser:
            browser.warm_up()
            payload = browser.search_catalog(query, page=1, per_page=per_page)
            raw_items = parse_catalog_payload(payload, base_url=base)[:max_items]
            brand_map = settings.brand_channel_map()
            if brand_map:
                items = [
                    item
                    for item in raw_items
                    if is_allowed_brand(item.brand, brand_map)
                ]
                skipped = len(raw_items) - len(items)
            else:
                items = raw_items
                skipped = 0
            log.info(
                "search_parsed",
                query=query,
                count=len(items),
                skipped_unwanted_brands=skipped,
            )

            with session_scope() as session:
                for item in items:
                    upsert_listing(
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

    # Auto-post Discord : uniquement les annonces encore jamais postées
    vinted_ids = [item.vinted_id for item in items]
    with session_scope() as session:
        unposted = get_unposted_listings_by_vinted_ids(session, vinted_ids)
        for listing in unposted:
            for photo in list(listing.photos):
                session.expunge(photo)
            session.expunge(listing)

    posted_ids = publish_listings_to_discord(
        unposted,
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
        "discord_publish_done",
        query=query,
        unposted=len(unposted),
        posted=posted,
    )

    return ScrapeSearchResult(
        query=query,
        items_found=len(items),
        items_upserted=upserted,
        items_posted_discord=posted,
        scrape_run_id=run_id,
        items=items,
    )
