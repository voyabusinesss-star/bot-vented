"""Détecteur de niches vêtements — achat bas / revente plus haute."""

from __future__ import annotations

import random
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from vinted_bot.clients.vinted_browser import VintedBrowser, vinted_browser
from vinted_bot.config import get_settings, sanitize_discord_channel_id
from vinted_bot.db.repositories import get_checkpoint, set_checkpoint, upsert_listing
from vinted_bot.db.session import session_scope
from vinted_bot.niche_config import NichesConfig, load_niches_config
from vinted_bot.notify.discord import (
    VETEMENT_CATEGORIES,
    DiscordNotifier,
    normalize_brand,
)
from vinted_bot.parsers.search import SearchItem, parse_catalog_payload
from vinted_bot.services.deal_filter import (
    category_label,
    detect_category,
    is_shoe_listing,
)
from vinted_bot.services.market_entities import (
    enrich_listing_entities,
    is_analyzable_listing,
)
from vinted_bot.utils.logging import get_logger
from vinted_bot.utils.retry import retry_call

log = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class NicheOpportunity:
    """Niche marque × catégorie avec potentiel de marge."""

    brand: str
    category: str
    category_label: str
    sample_count: int
    median_price_eur: float
    cheap_price_eur: float
    margin_eur: float
    margin_pct: float
    probe_label: str
    example_title: str
    example_url: str
    example_vinted_id: int

    @property
    def key(self) -> str:
        return f"{normalize_brand(self.brand)}|{self.category}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _price_eur(item: SearchItem) -> float | None:
    if item.price_cents is None or item.price_cents <= 0:
        return None
    return item.price_cents / 100.0


def _is_clothing_item(item: SearchItem) -> bool:
    """Legacy: alertes marge spot = vêtements only (pas sneakers)."""
    if is_shoe_listing(item.title):
        return False
    category = detect_category(item.title)
    if category and category in ("chaussure", "dunk", "air_force_1"):
        return False
    if category and category in VETEMENT_CATEGORIES:
        return True
    return category is None or category == "default"


def persist_probe_items(
    items: Sequence[SearchItem],
    *,
    source_query: str,
) -> int:
    """Persiste les annonces de sondes (toutes catégories) pour market-intel."""
    saved = 0
    created_ids: list[int] = []
    with session_scope() as session:
        for item in items:
            if not is_analyzable_listing(item.title, brand=item.brand):
                continue
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
                source_query=f"niche:{source_query}",
            )
            enrich_listing_entities(session, listing)
            saved += 1
            if created:
                created_ids.append(int(item.vinted_id))

    if created_ids:
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
                        .where(Listing.vinted_id.in_(created_ids))
                    )
                    .unique()
                    .all()
                )
                for listing in rows:
                    for photo in list(listing.photos):
                        session.expunge(photo)
                    session.expunge(listing)
            if rows:
                send_private_filter_alerts(rows)
        except Exception as exc:  # noqa: BLE001
            log.warning("private_filters_discovery_hook_failed", error=str(exc)[:160])
    return saved


def analyze_probe_items(
    items: Sequence[SearchItem],
    *,
    probe_label: str,
    min_samples: int,
    min_margin_pct: float,
    min_margin_eur: float,
) -> list[NicheOpportunity]:
    """Agrège les annonces en niches marque×catégorie et score les opportunités."""
    buckets: dict[tuple[str, str], list[tuple[SearchItem, float]]] = {}
    for item in items:
        if not _is_clothing_item(item):
            continue
        price = _price_eur(item)
        if price is None:
            continue
        brand = normalize_brand(item.brand) or "inconnu"
        category = detect_category(item.title) or "default"
        if category not in VETEMENT_CATEGORIES and category != "default":
            continue
        if category == "default":
            # Sans catégorie vêtement claire, on ignore (évite objets)
            continue
        buckets.setdefault((brand, category), []).append((item, price))

    opportunities: list[NicheOpportunity] = []
    for (brand, category), pairs in buckets.items():
        if len(pairs) < min_samples:
            continue
        prices = sorted(p for _, p in pairs)
        median = statistics.median(prices)
        if median <= 0:
            continue
        # Prix « bon achat » = 25e percentile
        idx = max(0, int(len(prices) * 0.25) - 1)
        cheap = prices[idx]
        margin_eur = median - cheap
        margin_pct = (margin_eur / median) * 100.0
        if margin_eur < min_margin_eur or margin_pct < min_margin_pct:
            continue
        # Exemple = annonce la moins chère du bucket
        example_item, example_price = min(pairs, key=lambda x: x[1])

        opportunities.append(
            NicheOpportunity(
                brand=brand,
                category=category,
                category_label=category_label(category) or category,
                sample_count=len(pairs),
                median_price_eur=round(median, 2),
                cheap_price_eur=round(example_price, 2),
                margin_eur=round(margin_eur, 2),
                margin_pct=round(margin_pct, 1),
                probe_label=probe_label,
                example_title=(example_item.title or "")[:120],
                example_url=example_item.url,
                example_vinted_id=example_item.vinted_id,
            )
        )

    opportunities.sort(key=lambda o: (o.margin_pct, o.margin_eur), reverse=True)
    return opportunities


def _niche_checkpoint_key(niche_key: str) -> str:
    return f"niche:alert:{niche_key}"


def _was_recently_posted(niche_key: str, *, cooldown_hours: float) -> bool:
    with session_scope() as session:
        data = get_checkpoint(session, _niche_checkpoint_key(niche_key))
    if not data:
        return False
    posted_at = data.get("posted_at")
    if not posted_at:
        return False
    try:
        dt = datetime.fromisoformat(str(posted_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return _utcnow() - dt < timedelta(hours=cooldown_hours)


def _mark_posted(niche: NicheOpportunity) -> None:
    with session_scope() as session:
        set_checkpoint(
            session,
            _niche_checkpoint_key(niche.key),
            {
                "posted_at": _utcnow().isoformat(),
                "brand": niche.brand,
                "category": niche.category,
                "margin_pct": niche.margin_pct,
                "example_vinted_id": niche.example_vinted_id,
            },
        )


def build_niche_embed(niche: NicheOpportunity) -> dict[str, Any]:
    return {
        "title": f"🧭 Niche : {niche.brand.title()} · {niche.category_label}",
        "description": (
            f"**Potentiel revente** ~**{niche.margin_pct:.0f}%** "
            f"(+{niche.margin_eur:.0f} €)\n"
            f"Achat bas ≈ **{niche.cheap_price_eur:.0f} €** → "
            f"médiane marché ≈ **{niche.median_price_eur:.0f} €**\n"
            f"Échantillon : **{niche.sample_count}** annonces · sonde *{niche.probe_label}*"
        ),
        "url": niche.example_url,
        "color": 0x2ECC71,
        "fields": [
            {
                "name": "Exemple pas cher",
                "value": f"[{niche.example_title}]({niche.example_url})",
                "inline": False,
            },
            {
                "name": "Stratégie",
                "value": (
                    "Acheter sous la médiane (taille/état OK), "
                    "revendre autour du prix médian sur Vinted."
                ),
                "inline": False,
            },
        ],
        "footer": {"text": "Détecteur de niches · vêtements only"},
        "timestamp": _utcnow().isoformat(),
    }


def _fetch_probe_items(
    browser: VintedBrowser,
    *,
    query: str,
    catalog_ids: list[int],
    max_items: int,
    base_url: str,
) -> list[SearchItem]:
    settings = get_settings()
    per_page = max(1, min(max_items, 96))
    payload = retry_call(
        lambda: browser.search_catalog(
            query,
            page=1,
            per_page=per_page,
            order="newest_first",
            catalog_ids=catalog_ids,
        ),
        max_retries=settings.max_retries,
        label=f"niche:{query}",
    )
    return parse_catalog_payload(payload, base_url=base_url)[:max_items]


def run_niche_cycle(
    *,
    config: NichesConfig | None = None,
    headless: bool = True,
    post_discord: bool = True,
) -> list[NicheOpportunity]:
    """Un cycle : sonde les probes, score les niches, poste Discord."""
    cfg = config or load_niches_config()
    settings = get_settings()
    if not cfg.probes:
        log.warning("niche_no_probes")
        return []

    all_ops: list[NicheOpportunity] = []
    with vinted_browser(
        base_url=settings.vinted_base_url,
        headless=headless,
        delay_seconds=settings.request_delay_seconds,
    ) as browser:
        browser.warm_up()
        for i, probe in enumerate(cfg.probes):
            try:
                items = _fetch_probe_items(
                    browser,
                    query=probe.query,
                    catalog_ids=cfg.catalog_ids,
                    max_items=cfg.max_items_per_probe,
                    base_url=settings.vinted_base_url,
                )
                persisted = persist_probe_items(
                    items, source_query=probe.query
                )
                ops = analyze_probe_items(
                    items,
                    probe_label=probe.label or probe.query,
                    min_samples=cfg.min_samples,
                    min_margin_pct=cfg.min_margin_pct,
                    min_margin_eur=cfg.min_margin_eur,
                )
                all_ops.extend(ops)
                log.info(
                    "niche_probe_done",
                    query=probe.query,
                    items=len(items),
                    persisted=persisted,
                    opportunities=len(ops),
                )
            except Exception as exc:
                log.warning("niche_probe_failed", query=probe.query, error=str(exc))
            if i + 1 < len(cfg.probes):
                time.sleep(max(0.0, cfg.delay_between_probes_seconds))

    # Déduplique par niche key (garde la meilleure marge)
    best: dict[str, NicheOpportunity] = {}
    for op in all_ops:
        prev = best.get(op.key)
        if prev is None or op.margin_pct > prev.margin_pct:
            best[op.key] = op
    ranked = sorted(best.values(), key=lambda o: o.margin_pct, reverse=True)

    to_post: list[NicheOpportunity] = []
    for op in ranked:
        if _was_recently_posted(op.key, cooldown_hours=cfg.niche_cooldown_hours):
            continue
        to_post.append(op)
        if len(to_post) >= cfg.max_discord_posts_per_cycle:
            break

    # Les alertes brand×category legacy ne sont plus postées :
    # le salon #détecteur-niches reçoit les cartes Opportunity via market-intel.
    if to_post:
        log.info(
            "niche_legacy_alerts_skipped",
            count=len(to_post),
            reason="unified_opportunity_engine",
        )

    # Enrichit le moteur market-intel (opportunités + pépites Discord)
    try:
        from vinted_bot.services.market_intel import run_market_intel_cycle

        intel = run_market_intel_cycle(
            post_discord=post_discord,
            reconcile=True,
            force_discord=False,
        )
        log.info("niche_market_intel_attached", **intel)
    except Exception as exc:  # noqa: BLE001
        log.warning("niche_market_intel_failed", error=str(exc))

    log.info(
        "niche_cycle_done",
        probes=len(cfg.probes),
        opportunities=len(ranked),
        posted=len(to_post) if post_discord else 0,
    )
    return ranked


def run_discovery_collect(
    *,
    headless: bool = True,
    max_probes: int = 10,
    max_items: int | None = None,
) -> dict[str, Any]:
    """Collecte exploratoire multi-catégories — pas mode/chaussures only.

    1) Balayage catalogues (rotation) : flux newest toutes catégories
    2) Probes mots-clés (niches.yaml) sans filtre catalogue mode
    """
    cfg = load_niches_config()
    settings = get_settings()
    per_probe = max_items or min(cfg.max_items_per_probe, 36)
    per_catalog = max_items or min(cfg.max_items_per_catalog, 36)

    with session_scope() as session:
        probe_data = get_checkpoint(session, "detector:discovery_probe_offset") or {}
        cat_data = get_checkpoint(session, "detector:discovery_catalog_offset") or {}
    probe_offset = (
        int(probe_data.get("offset", 0) or 0) if isinstance(probe_data, dict) else 0
    )
    cat_offset = (
        int(cat_data.get("offset", 0) or 0) if isinstance(cat_data, dict) else 0
    )

    catalogs = cfg.discovery_catalogs or []
    cat_batch = []
    if catalogs:
        c_n = len(catalogs)
        c_start = cat_offset % c_n
        ordered_cats = catalogs[c_start:] + catalogs[:c_start]
        cat_batch = ordered_cats[: max(1, min(cfg.catalog_sweep_per_cycle, c_n))]

    probe_batch = []
    next_probe_offset = probe_offset
    if cfg.probes:
        n = len(cfg.probes)
        start = probe_offset % n
        ordered = cfg.probes[start:] + cfg.probes[:start]
        probe_batch = ordered[: max(1, min(max_probes, n))]
        next_probe_offset = (start + len(probe_batch)) % n

    fetched = 0
    upserted = 0
    catalog_ok = 0
    with vinted_browser(
        base_url=settings.vinted_base_url,
        headless=headless,
        delay_seconds=settings.request_delay_seconds,
    ) as browser:
        browser.warm_up()

        # 1) Balayage multi-catalogues (inclut hors vêtements)
        for i, cat in enumerate(cat_batch):
            label = cat.label or str(cat.catalog_id or "all")
            try:
                ids = [] if cat.catalog_id is None else [cat.catalog_id]
                items = _fetch_probe_items(
                    browser,
                    query="",  # flux newest du catalogue
                    catalog_ids=ids,
                    max_items=per_catalog,
                    base_url=settings.vinted_base_url,
                )
                saved = persist_probe_items(
                    items, source_query=f"catalog:{label}"
                )
                fetched += len(items)
                upserted += saved
                catalog_ok += 1
                log.info(
                    "discovery_catalog_done",
                    catalog=label,
                    catalog_id=cat.catalog_id,
                    items=len(items),
                    persisted=saved,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "discovery_catalog_failed",
                    catalog=label,
                    error=str(exc)[:160],
                )
            if i + 1 < len(cat_batch) or probe_batch:
                time.sleep(
                    max(0.0, cfg.delay_between_probes_seconds)
                    + random.uniform(0.4, 1.8)
                )

        # 2) Probes ciblées (toutes catégories si catalog_ids vide)
        for i, probe in enumerate(probe_batch):
            try:
                items = _fetch_probe_items(
                    browser,
                    query=probe.query,
                    catalog_ids=cfg.catalog_ids,
                    max_items=per_probe,
                    base_url=settings.vinted_base_url,
                )
                saved = persist_probe_items(items, source_query=probe.query)
                fetched += len(items)
                upserted += saved
                log.info(
                    "discovery_probe_done",
                    query=probe.query,
                    items=len(items),
                    persisted=saved,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "discovery_probe_failed",
                    query=probe.query,
                    error=str(exc)[:160],
                )
            if i + 1 < len(probe_batch):
                time.sleep(
                    max(0.0, cfg.delay_between_probes_seconds)
                    + random.uniform(0.5, 2.0)
                )

    next_cat_offset = cat_offset
    if catalogs and cat_batch:
        next_cat_offset = (cat_offset + len(cat_batch)) % len(catalogs)

    with session_scope() as session:
        set_checkpoint(
            session,
            "detector:discovery_probe_offset",
            {"offset": next_probe_offset},
        )
        set_checkpoint(
            session,
            "detector:discovery_catalog_offset",
            {"offset": next_cat_offset},
        )

    summary = {
        "catalogs": catalog_ok,
        "probes": len(probe_batch),
        "fetched": fetched,
        "upserted": upserted,
        "probe_offset": next_probe_offset,
        "catalog_offset": next_cat_offset,
    }
    log.info("discovery_collect_done", **summary)
    return summary


def run_niche_loop(
    *,
    interval_seconds: float | None = None,
    headless: bool = True,
) -> None:
    """Boucle continue du détecteur de niches."""
    cfg = load_niches_config()
    interval = (
        interval_seconds
        if interval_seconds is not None
        else cfg.loop_interval_seconds
    )
    log.info("niche_loop_start", interval_seconds=interval, probes=len(cfg.probes))
    while True:
        try:
            run_niche_cycle(config=cfg, headless=headless, post_discord=True)
        except Exception as exc:
            log.exception("niche_cycle_error", error=str(exc))
        time.sleep(max(30.0, interval))
