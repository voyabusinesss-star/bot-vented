"""Accès données (upsert, dedup, market intel)."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from vinted_bot.db.models import (
    Checkpoint,
    Listing,
    ListingEntity,
    ListingObservation,
    NicheSnapshot,
    OpportunityHistory,
    Photo,
    ScrapeRun,
    TrendSnapshot,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def extract_seller_id(raw_json: dict[str, Any] | None) -> int | None:
    if not isinstance(raw_json, dict):
        return None
    user = raw_json.get("user") or raw_json.get("seller") or {}
    if not isinstance(user, dict):
        return None
    for key in ("id", "user_id"):
        value = user.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _raw_hash(raw_json: dict[str, Any] | None) -> str | None:
    if not isinstance(raw_json, dict):
        return None
    payload = json.dumps(raw_json, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:64]


def add_listing_observation(
    session: Session,
    listing: Listing,
    *,
    source_query: str | None = None,
    is_present: bool = True,
) -> ListingObservation:
    obs = ListingObservation(
        listing_id=listing.id,
        vinted_id=listing.vinted_id,
        observed_at=_utcnow(),
        price_cents=listing.price_cents,
        is_present=is_present,
        title=listing.title,
        brand=listing.brand,
        size=listing.size,
        source_query=source_query,
        raw_hash=_raw_hash(listing.raw_json if isinstance(listing.raw_json, dict) else None),
    )
    session.add(obs)
    session.flush()
    return obs


def upsert_listing(
    session: Session,
    *,
    vinted_id: int,
    title: str,
    url: str,
    price_cents: int | None = None,
    currency: str = "EUR",
    brand: str | None = None,
    size: str | None = None,
    condition: str | None = None,
    published_at: datetime | None = None,
    raw_json: dict[str, Any] | None = None,
    photo_urls: Sequence[str] | None = None,
    is_active: bool = True,
    source_query: str | None = None,
    record_observation: bool = True,
    seller_id: int | None = None,
    category_slug: str | None = None,
    model_slug: str | None = None,
    keyword_slugs: Sequence[str] | None = None,
) -> tuple[Listing, bool]:
    """Insert ou met à jour une annonce.

    Retourne (listing, created) où created=True si nouvel insert.
    N'écrase pas les champs optionnels avec None (préserve enrichment futur).
    """
    now = _utcnow()
    existing = session.scalar(select(Listing.id).where(Listing.vinted_id == vinted_id))
    created = existing is None
    resolved_seller = seller_id if seller_id is not None else extract_seller_id(raw_json)

    set_values: dict[str, Any] = {
        "title": title,
        "url": url,
        "currency": currency,
        "is_active": is_active,
        "disappeared_at": None if is_active else now,
        "updated_at": now,
    }
    if is_active:
        set_values["last_seen_at"] = now
    if price_cents is not None:
        set_values["price_cents"] = price_cents
    if brand is not None:
        set_values["brand"] = brand
    if size is not None:
        set_values["size"] = size
    if condition is not None:
        set_values["condition"] = condition
    if published_at is not None:
        set_values["published_at"] = published_at
    if raw_json is not None:
        set_values["raw_json"] = raw_json
    if resolved_seller is not None:
        set_values["seller_id"] = resolved_seller
    if category_slug is not None:
        set_values["category_slug"] = category_slug
    if model_slug is not None:
        set_values["model_slug"] = model_slug
    if keyword_slugs is not None:
        set_values["keyword_slugs"] = list(keyword_slugs)

    insert_values: dict[str, Any] = {
        "vinted_id": vinted_id,
        "title": title,
        "url": url,
        "price_cents": price_cents,
        "currency": currency,
        "brand": brand,
        "size": size,
        "condition": condition,
        "published_at": published_at,
        "raw_json": raw_json,
        "is_active": is_active,
        "disappeared_at": None if is_active else now,
        "first_seen_at": now,
        "last_seen_at": now if is_active else None,
        "seller_id": resolved_seller,
        "category_slug": category_slug,
        "model_slug": model_slug,
        "keyword_slugs": list(keyword_slugs) if keyword_slugs is not None else None,
    }

    stmt = (
        insert(Listing)
        .values(**insert_values)
        .on_conflict_do_update(
            index_elements=[Listing.vinted_id],
            set_=set_values,
        )
        .returning(Listing.id)
    )
    listing_id = session.execute(stmt).scalar_one()
    listing = session.get(Listing, listing_id)
    assert listing is not None
    session.refresh(listing)

    if created and listing.first_seen_at is None:
        listing.first_seen_at = now
    if is_active:
        listing.last_seen_at = now

    if photo_urls is not None:
        session.execute(delete(Photo).where(Photo.listing_id == listing_id))
        session.expire(listing, ["photos"])
        session.flush()
        for position, photo_url in enumerate(photo_urls):
            listing.photos.append(Photo(url=photo_url, position=position))
        session.flush()

    if record_observation:
        add_listing_observation(
            session,
            listing,
            source_query=source_query,
            is_present=is_active,
        )

    return listing, created


def backfill_listing_presence_signals(
    session: Session,
    *,
    limit: int = 5000,
) -> dict[str, int]:
    """Renseigne first/last_seen et seller_id manquants depuis scraped_at / raw_json.

    One-shot safe : n'écrase jamais une valeur déjà présente.
    """
    stmt = (
        select(Listing)
        .where(
            Listing.first_seen_at.is_(None)
            | Listing.last_seen_at.is_(None)
            | Listing.seller_id.is_(None)
        )
        .order_by(Listing.id.desc())
        .limit(max(1, limit))
    )
    listings = list(session.scalars(stmt).all())
    filled_first = 0
    filled_last = 0
    filled_seller = 0
    for listing in listings:
        anchor = listing.scraped_at or listing.updated_at or _utcnow()
        if listing.first_seen_at is None:
            listing.first_seen_at = anchor
            filled_first += 1
        if listing.last_seen_at is None and listing.is_active:
            listing.last_seen_at = listing.first_seen_at or anchor
            filled_last += 1
        if listing.seller_id is None:
            seller = extract_seller_id(
                listing.raw_json if isinstance(listing.raw_json, dict) else None
            )
            if seller is not None:
                listing.seller_id = seller
                filled_seller += 1
    if filled_first or filled_last or filled_seller:
        session.flush()
    return {
        "first_seen": filled_first,
        "last_seen": filled_last,
        "seller_id": filled_seller,
        "scanned": len(listings),
    }


def mark_listings_disappeared(
    session: Session,
    listing_ids: Sequence[int],
    *,
    source_query: str | None = None,
) -> int:
    if not listing_ids:
        return 0
    now = _utcnow()
    listings = list(
        session.scalars(select(Listing).where(Listing.id.in_(list(listing_ids)))).all()
    )
    count = 0
    for listing in listings:
        if not listing.is_active:
            continue
        listing.is_active = False
        listing.disappeared_at = now
        add_listing_observation(
            session,
            listing,
            source_query=source_query,
            is_present=False,
        )
        count += 1
    session.flush()
    return count


def replace_listing_entities(
    session: Session,
    listing_id: int,
    entities: Sequence[tuple[str, str, int]],
) -> None:
    """Remplace les entités d'une annonce. entities = (type, slug, confidence)."""
    session.execute(delete(ListingEntity).where(ListingEntity.listing_id == listing_id))
    for entity_type, entity_slug, confidence in entities:
        session.add(
            ListingEntity(
                listing_id=listing_id,
                entity_type=entity_type,
                entity_slug=entity_slug,
                confidence=confidence,
            )
        )
    session.flush()


def upsert_niche_snapshot(
    session: Session,
    *,
    niche_key: str,
    window: str,
    brand_slug: str | None = None,
    model_slug: str | None = None,
    category_slug: str | None = None,
    keyword_flags: str | None = None,
    listing_count: int = 0,
    new_listings: int = 0,
    disappeared_count: int = 0,
    unique_sellers: int = 0,
    price_min_cents: int | None = None,
    price_max_cents: int | None = None,
    price_mean_cents: int | None = None,
    price_median_cents: int | None = None,
    price_p25_cents: int | None = None,
    median_ttl_days: float | None = None,
    margin_proxy_pct: float | None = None,
    score: float | None = None,
    metrics: dict[str, Any] | None = None,
) -> NicheSnapshot:
    now = _utcnow()
    stmt = (
        insert(NicheSnapshot)
        .values(
            niche_key=niche_key,
            window=window,
            brand_slug=brand_slug,
            model_slug=model_slug,
            category_slug=category_slug,
            keyword_flags=keyword_flags,
            listing_count=listing_count,
            new_listings=new_listings,
            disappeared_count=disappeared_count,
            unique_sellers=unique_sellers,
            price_min_cents=price_min_cents,
            price_max_cents=price_max_cents,
            price_mean_cents=price_mean_cents,
            price_median_cents=price_median_cents,
            price_p25_cents=price_p25_cents,
            median_ttl_days=median_ttl_days,
            margin_proxy_pct=margin_proxy_pct,
            score=score,
            metrics=metrics,
            computed_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_niche_snapshot_key_window",
            set_={
                "brand_slug": brand_slug,
                "model_slug": model_slug,
                "category_slug": category_slug,
                "keyword_flags": keyword_flags,
                "listing_count": listing_count,
                "new_listings": new_listings,
                "disappeared_count": disappeared_count,
                "unique_sellers": unique_sellers,
                "price_min_cents": price_min_cents,
                "price_max_cents": price_max_cents,
                "price_mean_cents": price_mean_cents,
                "price_median_cents": price_median_cents,
                "price_p25_cents": price_p25_cents,
                "median_ttl_days": median_ttl_days,
                "margin_proxy_pct": margin_proxy_pct,
                "score": score,
                "metrics": metrics,
                "computed_at": now,
            },
        )
        .returning(NicheSnapshot.id)
    )
    snapshot_id = session.execute(stmt).scalar_one()
    snapshot = session.get(NicheSnapshot, snapshot_id)
    assert snapshot is not None
    return snapshot


def upsert_trend_snapshot(
    session: Session,
    *,
    snapshot_date: date,
    entity_type: str,
    entity_key: str,
    display_name: str,
    strength: float,
    direction: str | None = None,
    lifecycle: str | None = None,
    importance: str | None = None,
    recommendation: str | None = None,
    count_1d: int = 0,
    count_7d: int = 0,
    count_30d: int = 0,
    count_90d: int = 0,
    active_count: int = 0,
    disappeared_7d: int = 0,
    price_median_7d: float | None = None,
    price_median_30d: float | None = None,
    price_change_pct: float | None = None,
    rotation_change_pct: float | None = None,
    stock_change_pct: float | None = None,
    popularity_change_pct: float | None = None,
    continuation_pct: float | None = None,
    gauge_growth: float | None = None,
    gauge_rentabilite: float | None = None,
    gauge_rarity: float | None = None,
    gauge_demand: float | None = None,
    gauge_saturation: float | None = None,
    triggers: list[Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> TrendSnapshot:
    now = _utcnow()
    values = {
        "snapshot_date": snapshot_date,
        "entity_type": entity_type,
        "entity_key": entity_key,
        "display_name": display_name,
        "strength": strength,
        "direction": direction,
        "lifecycle": lifecycle,
        "importance": importance,
        "recommendation": recommendation,
        "count_1d": count_1d,
        "count_7d": count_7d,
        "count_30d": count_30d,
        "count_90d": count_90d,
        "active_count": active_count,
        "disappeared_7d": disappeared_7d,
        "price_median_7d": price_median_7d,
        "price_median_30d": price_median_30d,
        "price_change_pct": price_change_pct,
        "rotation_change_pct": rotation_change_pct,
        "stock_change_pct": stock_change_pct,
        "popularity_change_pct": popularity_change_pct,
        "continuation_pct": continuation_pct,
        "gauge_growth": gauge_growth,
        "gauge_rentabilite": gauge_rentabilite,
        "gauge_rarity": gauge_rarity,
        "gauge_demand": gauge_demand,
        "gauge_saturation": gauge_saturation,
        "triggers": triggers,
        "payload": payload,
        "computed_at": now,
    }
    stmt = (
        insert(TrendSnapshot)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_trend_snapshot_day_entity",
            set_={k: v for k, v in values.items() if k not in {"snapshot_date", "entity_type", "entity_key"}},
        )
        .returning(TrendSnapshot.id)
    )
    snapshot_id = session.execute(stmt).scalar_one()
    snapshot = session.get(TrendSnapshot, snapshot_id)
    assert snapshot is not None
    return snapshot


def list_trend_snapshots_for_date(
    session: Session,
    snapshot_date: date,
    *,
    min_strength: float = 0.0,
    limit: int = 50,
) -> list[TrendSnapshot]:
    stmt = (
        select(TrendSnapshot)
        .where(TrendSnapshot.snapshot_date == snapshot_date)
        .where(TrendSnapshot.strength >= min_strength)
        .order_by(TrendSnapshot.strength.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def list_trend_history(
    session: Session,
    *,
    entity_type: str,
    entity_key: str,
    since: date,
    limit: int = 90,
) -> list[TrendSnapshot]:
    stmt = (
        select(TrendSnapshot)
        .where(TrendSnapshot.entity_type == entity_type)
        .where(TrendSnapshot.entity_key == entity_key)
        .where(TrendSnapshot.snapshot_date >= since)
        .order_by(TrendSnapshot.snapshot_date.asc())
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def get_listing_by_vinted_id(session: Session, vinted_id: int) -> Listing | None:
    return session.scalar(select(Listing).where(Listing.vinted_id == vinted_id))


def get_unposted_listings_by_vinted_ids(
    session: Session, vinted_ids: Sequence[int]
) -> list[Listing]:
    if not vinted_ids:
        return []
    stmt = (
        select(Listing)
        .options(selectinload(Listing.photos))
        .where(Listing.vinted_id.in_(list(vinted_ids)))
        .where(Listing.discord_posted_at.is_(None))
        .order_by(Listing.id)
    )
    return list(session.scalars(stmt).unique().all())


def mark_discord_posted(session: Session, listing_ids: Sequence[int]) -> None:
    if not listing_ids:
        return
    now = _utcnow()
    listings = session.scalars(
        select(Listing).where(Listing.id.in_(list(listing_ids)))
    ).all()
    for listing in listings:
        listing.discord_posted_at = now
    session.flush()


def create_scrape_run(session: Session, query: str | None = None) -> ScrapeRun:
    run = ScrapeRun(
        query=query,
        status="running",
        items_found=0,
        items_upserted=0,
    )
    session.add(run)
    session.flush()
    return run


def finish_scrape_run(
    session: Session,
    run: ScrapeRun,
    *,
    status: str,
    items_found: int = 0,
    items_upserted: int = 0,
    error: str | None = None,
) -> ScrapeRun:
    run.status = status
    run.items_found = items_found
    run.items_upserted = items_upserted
    run.error = error
    run.finished_at = _utcnow()
    session.flush()
    return run


def set_checkpoint(session: Session, key: str, value: dict[str, Any]) -> Checkpoint:
    stmt = (
        insert(Checkpoint)
        .values(key=key, value=value)
        .on_conflict_do_update(
            index_elements=[Checkpoint.key],
            set_={"value": value, "updated_at": _utcnow()},
        )
        .returning(Checkpoint.id)
    )
    checkpoint_id = session.execute(stmt).scalar_one()
    checkpoint = session.get(Checkpoint, checkpoint_id)
    assert checkpoint is not None
    return checkpoint


def get_checkpoint(session: Session, key: str) -> Optional[dict[str, Any]]:
    row = session.scalar(select(Checkpoint).where(Checkpoint.key == key))
    return row.value if row else None


def record_opportunity_history(
    session: Session,
    *,
    niche_key: str,
    name: str,
    score: float,
    lifecycle: str | None = None,
    confidence: float | None = None,
    niche_type: str | None = None,
    brand_slug: str | None = None,
    model_slug: str | None = None,
    category_slug: str | None = None,
    signals: Sequence[str] | None = None,
    payload: dict[str, Any] | None = None,
    posted: bool = False,
) -> OpportunityHistory:
    row = OpportunityHistory(
        niche_key=niche_key,
        name=name,
        score=score,
        lifecycle=lifecycle,
        confidence=confidence,
        niche_type=niche_type,
        brand_slug=brand_slug,
        model_slug=model_slug,
        category_slug=category_slug,
        signals=list(signals) if signals else None,
        payload=payload,
        posted=posted,
        detected_at=_utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def get_opportunity_score_history(
    session: Session,
    niche_key: str,
    *,
    limit: int = 12,
) -> list[float]:
    rows = list(
        session.scalars(
            select(OpportunityHistory.score)
            .where(OpportunityHistory.niche_key == niche_key)
            .order_by(OpportunityHistory.detected_at.desc())
            .limit(max(1, limit))
        ).all()
    )
    rows.reverse()
    return [float(s) for s in rows]
