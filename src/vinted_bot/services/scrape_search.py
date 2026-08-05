"""Service : scrape une recherche Vinted et upsert en base."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

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
from vinted_bot.notify.discord import (
    attach_deal_evaluation,
    is_allowed_brand,
    publish_listings_to_discord,
)
from vinted_bot.parsers.search import SearchItem, parse_catalog_payload
from vinted_bot.services.deal_filter import evaluate_listing, load_deal_filters
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
    items_skipped_deal: int = 0
    bootstrap: bool = False
    scrape_run_id: int = 0
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
    price_from: float | None = None,
    price_to: float | None = None,
    skip_brand_channel_filter: bool = False,
    keep_search_text: bool = False,
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
    # Filtres privés : pas de bootstrap silencieux (sinon aucune alerte DM au 1er passage)
    bootstrap = (not keep_search_text) and (not _is_bootstrapped(query))

    try:
        def _run_with_browser(active: VintedBrowser) -> None:
            nonlocal items, skipped_brand, created_count, upserted, created_vinted_ids

            # YAML marque seule (query≈brand + brand_ids) : search_text vide.
            # Filtres privés : garder le texte (TN, mot-clé…) même avec brand_ids.
            from vinted_bot.notify.discord import normalize_brand

            q = (query or "").strip()
            if keep_search_text:
                catalog_query = q
            elif brand_ids and expected_brand and normalize_brand(q) == normalize_brand(
                expected_brand
            ):
                catalog_query = ""
            elif brand_ids and not q:
                catalog_query = ""
            else:
                catalog_query = q

            payload = retry_call(
                lambda: active.search_catalog(
                    catalog_query,
                    page=1,
                    per_page=per_page,
                    order=order,
                    brand_ids=brand_ids,
                    catalog_ids=catalog_ids,
                    price_from=price_from,
                    price_to=price_to,
                ),
                max_retries=settings.max_retries,
                label=f"catalog:{query}",
            )
            raw_items = parse_catalog_payload(payload, base_url=base)[:max_items]
            brand_map = settings.brand_channel_map()
            sneaker_map = settings.sneaker_channel_map()
            if expected_brand:
                items = [
                    item
                    for item in raw_items
                    if is_allowed_brand(
                        item.brand,
                        {expected_brand: "x"},
                        sneaker_map={expected_brand: "x"},
                    )
                ]
                skipped_brand = len(raw_items) - len(items)
            elif skip_brand_channel_filter:
                items = raw_items
                skipped_brand = 0
            elif brand_map or sneaker_map:
                items = [
                    item
                    for item in raw_items
                    if is_allowed_brand(
                        item.brand, brand_map, sneaker_map=sneaker_map
                    )
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
                    listing, created = upsert_listing(
                        session,
                        vinted_id=item.vinted_id,
                        title=item.title,
                        url=item.url,
                        price_cents=item.price_cents,
                        currency=item.currency,
                        brand=item.brand,
                        size=item.size,
                        published_at=item.published_at,
                        photo_urls=item.photo_urls,
                        raw_json=item.raw_json,
                        source_query=query,
                    )
                    try:
                        from vinted_bot.services.market_entities import (
                            enrich_listing_entities,
                        )

                        enrich_listing_entities(session, listing)
                    except Exception as enrich_exc:  # noqa: BLE001
                        log.warning(
                            "listing_enrich_failed",
                            vinted_id=item.vinted_id,
                            error=str(enrich_exc),
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
            items_skipped_deal=0,
            bootstrap=True,
            scrape_run_id=run_id,
            items=items,
        )

    # Live : Discord uniquement pour les VRAIES nouvelles (inserts de ce run)
    # Filtre deal (marque×catégorie×prix) puis plafond de posts.
    deal_cfg = load_deal_filters()
    skipped_deal = 0
    with session_scope() as session:
        to_post = get_unposted_listings_by_vinted_ids(session, created_vinted_ids)
        to_post.sort(key=lambda listing: listing.vinted_id, reverse=True)

        qualified: list[tuple[Any, Any]] = []
        rejected_ids: list[int] = []
        for listing in to_post:
            deal = evaluate_listing(listing, config=deal_cfg)
            if deal.should_post:
                qualified.append((listing, deal))
            else:
                rejected_ids.append(listing.id)
                skipped_deal += 1
                log.info(
                    "deal_filter_skipped",
                    vinted_id=listing.vinted_id,
                    brand=listing.brand,
                    title=listing.title,
                    price_cents=listing.price_cents,
                    reason=deal.reason,
                    category=deal.category,
                    score=deal.score,
                )

        # Meilleurs scores d'abord (premium-ready)
        qualified.sort(key=lambda pair: pair[1].score, reverse=True)

        post_cap = max_discord_posts
        if post_cap is None:
            post_cap = load_searches_config().max_discord_posts
        post_cap = max(0, int(post_cap))
        # 0 = silence Discord explicite (ne jamais poster)
        selected = qualified[:post_cap]
        capped = qualified[post_cap:]

        announce: list[Any] = []
        for listing, deal in selected:
            attach_deal_evaluation(listing, deal)
            for photo in list(listing.photos):
                session.expunge(photo)
            session.expunge(listing)
            announce.append(listing)

        silent_ids = rejected_ids + [listing.id for listing, _ in capped]
        if silent_ids:
            mark_discord_posted(session, silent_ids)
            log.info(
                "discord_filter_or_cap_skipped",
                query=query,
                skipped_deal=skipped_deal,
                skipped_cap=len(capped),
                posted_cap=post_cap,
                qualified=len(qualified),
            )

    posted_ids = publish_listings_to_discord(
        announce,
        query=query,
        items_found=len(items),
        items_upserted=upserted,
        scrape_run_id=run_id,
        settings=settings,
        # Aperçu géré juste après (public only) — pas via ce helper
        bot_preview=False,
    )
    posted = len(posted_ids)
    if posted_ids:
        with session_scope() as session:
            mark_discord_posted(session, posted_ids)

    # Salon aperçu bot : 1 ping ralenti depuis le scrape PUBLIC seulement
    # (jamais les filtres privés / keep_search_text).
    if not keep_search_text:
        try:
            from vinted_bot.notify.discord import (
                DiscordNotifier,
                maybe_post_bot_preview_from_candidates,
            )

            candidates: list[Any] = list(announce)
            # Enrichit avec d'autres deals du scrape pour diversifier marques /
            # textile vs chaussures (pas seulement adidas/jordan sneakers).
            sample_ids = list(
                dict.fromkeys([*created_vinted_ids, *all_vinted_ids])
            )[:30]
            if sample_ids:
                with session_scope() as session:
                    from sqlalchemy import select
                    from sqlalchemy.orm import selectinload

                    from vinted_bot.db.models import Listing as ListingModel

                    rows = list(
                        session.scalars(
                            select(ListingModel)
                            .options(selectinload(ListingModel.photos))
                            .where(ListingModel.vinted_id.in_(sample_ids))
                        )
                        .unique()
                        .all()
                    )
                    seen = {
                        int(getattr(c, "vinted_id", 0) or 0) for c in candidates
                    }
                    for listing in rows:
                        vid = int(listing.vinted_id or 0)
                        if vid in seen:
                            continue
                        deal = evaluate_listing(listing, config=deal_cfg)
                        if not deal.should_post:
                            continue
                        attach_deal_evaluation(listing, deal)
                        for photo in list(listing.photos):
                            session.expunge(photo)
                        session.expunge(listing)
                        candidates.append(listing)
                        seen.add(vid)

            if candidates:
                with DiscordNotifier(settings) as notifier:
                    maybe_post_bot_preview_from_candidates(
                        notifier, candidates, settings=settings
                    )
        except Exception as exc:  # noqa: BLE001
            log.warning("bot_preview_hook_failed", error=str(exc)[:200])

    # Filtres privés → DM uniquement (indépendant des salons publics)
    # Cible filtre (keep_search_text) : toute la page newest + fraîcheur + dédup.
    # YAML public : uniquement les inserts de ce run (évite le spam).
    private_dm = 0
    private_ids = (
        list(dict.fromkeys([*created_vinted_ids, *all_vinted_ids]))
        if keep_search_text
        else list(created_vinted_ids)
    )
    if private_ids:
        try:
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            from vinted_bot.db.models import Listing
            from vinted_bot.services.private_filters import send_private_filter_alerts

            with session_scope() as session:
                rows = list(
                    session.scalars(
                        select(Listing)
                        .options(selectinload(Listing.photos))
                        .where(Listing.vinted_id.in_(private_ids))
                    ).unique().all()
                )
                for listing in rows:
                    for photo in list(listing.photos):
                        session.expunge(photo)
                    session.expunge(listing)
            if rows:
                def _recency(listing: Any) -> float:
                    for attr in (listing.published_at, listing.first_seen_at):
                        if attr is None:
                            continue
                        try:
                            return -attr.timestamp()
                        except Exception:  # noqa: BLE001
                            continue
                    return 0.0

                rows.sort(key=_recency)
                private_dm = send_private_filter_alerts(rows)
        except Exception as exc:  # noqa: BLE001
            log.warning("private_filters_hook_failed", error=str(exc)[:160])

    log.info(
        "scrape_done",
        query=query,
        found=len(items),
        created=created_count,
        updated=upserted - created_count,
        new_for_discord=len(announce),
        posted=posted,
        private_dm=private_dm,
        skipped_brand=skipped_brand,
        skipped_deal=skipped_deal,
        bootstrap=False,
    )

    return ScrapeSearchResult(
        query=query,
        items_found=len(items),
        items_upserted=upserted,
        items_created=created_count,
        items_posted_discord=posted,
        items_skipped_brand=skipped_brand,
        items_skipped_deal=skipped_deal,
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
    max_discord_posts: int | None = None,
    include_user_filters: bool = True,
) -> list[ScrapeSearchResult]:
    """Scrape les recherches actives (ou une liste filtrée par priorité).

    max_discord_posts=0 : collecte silencieuse (pipeline détecteur / filtres privés).
    """
    settings = get_settings()
    searches_cfg = load_searches_config()
    channel_map = settings.brand_channel_map()
    sneaker_map = settings.sneaker_channel_map()
    if targets is not None:
        all_targets = list(targets)
    else:
        all_targets = active_searches_for_channels(
            channel_map, sneaker_map=sneaker_map
        )
        if include_user_filters:
            from vinted_bot.config_loader import merge_search_targets
            from vinted_bot.services.filter_scrape_targets import (
                active_filter_search_targets,
            )

            all_targets = merge_search_targets(
                active_filter_search_targets(),
                all_targets,
            )
    default_max = max_items or searches_cfg.max_items

    if not all_targets:
        log.warning("scrape_all_no_targets")
        return []

    log.info(
        "scrape_all_start",
        targets=len(all_targets),
        brands=[t.brand for t in all_targets],
        sources=[getattr(t, "source", "yaml") for t in all_targets],
        cycle=cycle,
        max_items=default_max,
        max_discord_posts=max_discord_posts,
    )

    results: list[ScrapeSearchResult] = []

    def _run(active: VintedBrowser) -> None:
        for index, target in enumerate(all_targets):
            from vinted_bot.config_loader import resolve_policy

            policy = resolve_policy(target, searches_cfg.priorities)
            per_search = max_items or policy.max_items or default_max
            if max_discord_posts is not None:
                discord_cap = max_discord_posts
            elif target.max_discord_posts is not None:
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
                    # Marque connue : filtre expected_brand, ignore allowlist salons
                    skip_brand_filter = True

            started = time.monotonic()
            log.info(
                "scrape_all_target",
                index=index + 1,
                total=len(all_targets),
                brand=target.brand,
                query=target.query,
                priority=target.priority,
                source=getattr(target, "source", "yaml"),
                max_items=per_search,
                max_discord_posts=discord_cap,
                brand_ids=target.brand_ids,
                catalog_ids=target.catalog_ids,
                price_from=getattr(target, "price_from", None),
                price_to=getattr(target, "price_to", None),
            )
            try:
                result = scrape_search_once(
                    target.query,
                    max_items=per_search,
                    headless=headless,
                    browser=active,
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
                duration = round(time.monotonic() - started, 2)
                results.append(result)
                log.info(
                    "scrape_all_target_done",
                    brand=target.brand,
                    priority=target.priority,
                    source=getattr(target, "source", "yaml"),
                    duration_seconds=duration,
                    created=result.items_created,
                    posted=result.items_posted_discord,
                    found=result.items_found,
                    skipped_deal=result.items_skipped_deal,
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
