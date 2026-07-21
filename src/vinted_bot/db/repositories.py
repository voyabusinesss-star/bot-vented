"""Accès données (upsert, dedup)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

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
) -> Listing:
    """Insert ou met à jour une annonce (dedup sur vinted_id)."""
    now = _utcnow()
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
            set_={
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
                "updated_at": now,
            },
        )
        .returning(Listing.id)
    )
    listing_id = session.execute(stmt).scalar_one()
    listing = session.get(Listing, listing_id)
    assert listing is not None
    # L'upsert SQL ne rafraîchit pas l'objet déjà en mémoire
    session.refresh(listing)

    if photo_urls is not None:
        # delete SQL + expire : évite les doublons via la relation ORM
        session.execute(delete(Photo).where(Photo.listing_id == listing_id))
        session.expire(listing, ["photos"])
        session.flush()
        for position, photo_url in enumerate(photo_urls):
            listing.photos.append(
                Photo(url=photo_url, position=position)
            )
        session.flush()

    return listing


def get_listing_by_vinted_id(session: Session, vinted_id: int) -> Listing | None:
    return session.scalar(select(Listing).where(Listing.vinted_id == vinted_id))


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
