"""Accès données (upsert, dedup)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from vinted_bot.db.models import Checkpoint, Listing, Photo, ScrapeRun


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
) -> tuple[Listing, bool]:
    """Insert ou met à jour une annonce.

    Retourne (listing, created) où created=True si nouvel insert.
    N'écrase pas les champs optionnels avec None (préserve enrichment futur).
    """
    now = _utcnow()
    existing = session.scalar(select(Listing.id).where(Listing.vinted_id == vinted_id))
    created = existing is None

    set_values: dict[str, Any] = {
        "title": title,
        "url": url,
        "currency": currency,
        "is_active": is_active,
        "disappeared_at": None if is_active else now,
        "updated_at": now,
    }
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

    stmt = (
        insert(Listing)
        .values(
            vinted_id=vinted_id,
            title=title,
            url=url,
            price_cents=price_cents,
            currency=currency,
            brand=brand,
            size=size,
            condition=condition,
            published_at=published_at,
            raw_json=raw_json,
            is_active=is_active,
            disappeared_at=None if is_active else now,
        )
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

    if photo_urls is not None:
        session.execute(delete(Photo).where(Photo.listing_id == listing_id))
        session.expire(listing, ["photos"])
        session.flush()
        for position, photo_url in enumerate(photo_urls):
            listing.photos.append(Photo(url=photo_url, position=position))
        session.flush()

    return listing, created


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
    run = ScrapeRun(query=query, status="running")
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
