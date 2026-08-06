"""Discord outbox — file chronologique par salon (published_at)."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from vinted_bot.config import get_settings, sanitize_discord_channel_id
from vinted_bot.db.models import DiscordOutbox, Listing
from vinted_bot.db.repositories import mark_discord_posted
from vinted_bot.db.session import session_scope
from vinted_bot.notify.discord import (
    DiscordNotifier,
    belongs_in_all_vetement,
    build_listing_payload,
    is_classique_brand,
    is_vetement_for_all,
    route_channel,
)
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

OUTBOX_STATUS_PENDING = "pending"
OUTBOX_STATUS_SENT = "sent"
OUTBOX_STATUS_FAILED = "failed"
KIND_BRAND = "brand"
KIND_ALL = "all"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def resolve_discord_channels(listing: Listing) -> tuple[str | None, str | None, bool]:
    """Retourne (brand_channel_id, all_channel_id_or_none, is_shoe)."""
    from vinted_bot.services.deal_filter import is_shoe_listing

    settings = get_settings()
    deal = None
    try:
        from vinted_bot.notify.discord import get_deal_evaluation

        deal = get_deal_evaluation(listing)
    except Exception:  # noqa: BLE001
        deal = None
    category = getattr(deal, "category", None) if deal is not None else None
    is_shoe = False
    if category in ("chaussure", "dunk", "air_force_1"):
        is_shoe = True
    elif is_shoe_listing(listing.title):
        is_shoe = True

    brand_channel_id = route_channel(
        listing.brand,
        settings.brand_channel_map(),
        sneaker_map=settings.sneaker_channel_map(),
        is_shoe=is_shoe,
    )
    if not brand_channel_id:
        return None, None, is_shoe

    sneaker_ids = set(settings.sneaker_channel_map().values())
    if (
        brand_channel_id not in sneaker_ids
        and is_classique_brand(listing.brand)
        and is_shoe
    ):
        return None, None, is_shoe

    all_channel = sanitize_discord_channel_id(settings.discord_channel_all)
    is_vetement = is_vetement_for_all(listing.title, category)
    mirror_all = bool(all_channel) and all_channel != brand_channel_id and belongs_in_all_vetement(
        listing.brand,
        is_shoe=is_shoe,
        brand_channel_id=brand_channel_id,
        sneaker_channel_ids=sneaker_ids,
        is_vetement=is_vetement,
    )
    return brand_channel_id, (all_channel if mirror_all else None), is_shoe


def pending_brand_listing_ids(listing_ids: Sequence[int]) -> set[int]:
    """Listings déjà en file brand (évite de re-sélectionner avant flush)."""
    if not listing_ids:
        return set()
    with session_scope() as session:
        rows = session.scalars(
            select(DiscordOutbox.listing_id)
            .where(DiscordOutbox.listing_id.in_(list(listing_ids)))
            .where(DiscordOutbox.kind == KIND_BRAND)
            .where(DiscordOutbox.status == OUTBOX_STATUS_PENDING)
        ).all()
    return set(int(x) for x in rows)


def enqueue_listings_for_discord(listings: Sequence[Listing]) -> list[int]:
    """Enqueue brand (+ ALL si éligible). Retourne listing ids enqueued (brand)."""
    if not listings:
        return []
    enqueued_listing_ids: list[int] = []
    now = _utcnow()
    with session_scope() as session:
        for listing in listings:
            brand_ch, all_ch, _is_shoe = resolve_discord_channels(listing)
            if not brand_ch:
                log.info(
                    "outbox_skip_no_channel",
                    brand=listing.brand,
                    vinted_id=listing.vinted_id,
                )
                continue
            published = _as_aware(listing.published_at) or _as_aware(
                listing.first_seen_at
            ) or now

            def _enqueue(channel_id: str, kind: str) -> bool:
                exists = session.scalar(
                    select(DiscordOutbox.id)
                    .where(DiscordOutbox.listing_id == listing.id)
                    .where(DiscordOutbox.channel_id == channel_id)
                    .where(DiscordOutbox.kind == kind)
                    .where(DiscordOutbox.status == OUTBOX_STATUS_PENDING)
                    .limit(1)
                )
                if exists:
                    return False
                session.add(
                    DiscordOutbox(
                        listing_id=listing.id,
                        channel_id=channel_id,
                        published_at=published,
                        enqueued_at=now,
                        status=OUTBOX_STATUS_PENDING,
                        kind=kind,
                    )
                )
                return True

            if _enqueue(brand_ch, KIND_BRAND):
                enqueued_listing_ids.append(listing.id)
            if all_ch:
                _enqueue(all_ch, KIND_ALL)
        session.flush()
    if enqueued_listing_ids:
        log.info("discord_outbox_enqueued", count=len(enqueued_listing_ids))
    return enqueued_listing_ids


def flush_discord_outbox(
    *,
    buffer_seconds: float = 0.0,
    max_per_channel: int = 40,
) -> int:
    """Envoie les pending triés par published_at ASC par salon.

    buffer_seconds : n'envoie que les rows avec enqueued_at <= now - buffer
    (laisse d'autres marques arriver pour mélanger ALL).
    """
    settings = get_settings()
    if not settings.discord_bot_token.strip():
        return 0

    cutoff = _utcnow()
    if buffer_seconds > 0:
        from datetime import timedelta

        cutoff = cutoff - timedelta(seconds=buffer_seconds)

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(DiscordOutbox)
                .where(DiscordOutbox.status == OUTBOX_STATUS_PENDING)
                .where(DiscordOutbox.enqueued_at <= cutoff)
                .order_by(
                    DiscordOutbox.channel_id.asc(),
                    DiscordOutbox.published_at.asc(),
                    DiscordOutbox.id.asc(),
                )
                .limit(500)
            ).all()
        )
        if not rows:
            return 0
        # Cap per channel
        by_channel: dict[str, list[DiscordOutbox]] = {}
        for row in rows:
            bucket = by_channel.setdefault(row.channel_id, [])
            if len(bucket) < max_per_channel:
                bucket.append(row)
        ordered: list[DiscordOutbox] = []
        for channel_id in sorted(by_channel.keys()):
            ordered.extend(by_channel[channel_id])
        listing_ids = {r.listing_id for r in ordered}
        listings = {
            listing.id: listing
            for listing in session.scalars(
                select(Listing)
                .options(selectinload(Listing.photos))
                .where(Listing.id.in_(listing_ids))
            )
            .unique()
            .all()
        }
        # Capture primitives before session closes (expire_on_commit)
        payload_jobs: list[tuple[int, str, str, Listing, dict[str, Any]]] = []
        for row in ordered:
            listing = listings.get(row.listing_id)
            if listing is None:
                row.status = OUTBOX_STATUS_FAILED
                continue
            for photo in list(listing.photos):
                session.expunge(photo)
            session.expunge(listing)
            payload_jobs.append(
                (
                    int(row.id),
                    str(row.channel_id),
                    str(row.kind),
                    listing,
                    build_listing_payload(listing),
                )
            )

    if not payload_jobs:
        return 0

    posted_listing_ids: list[int] = []
    sent_row_ids: list[int] = []
    failed_row_ids: list[int] = []
    delay = float(settings.discord_post_delay_seconds or 0.0)

    with DiscordNotifier(settings) as notifier:
        for index, (row_id, channel_id, kind, listing, payload) in enumerate(
            payload_jobs
        ):
            try:
                notifier.post_message(channel_id, payload)
                sent_row_ids.append(row_id)
                if kind == KIND_BRAND:
                    posted_listing_ids.append(listing.id)
                log.info(
                    "discord_outbox_sent",
                    outbox_id=row_id,
                    kind=kind,
                    channel_id=channel_id,
                    listing_id=listing.id,
                    vinted_id=listing.vinted_id,
                    brand=listing.brand,
                    published_at=(
                        listing.published_at.isoformat()
                        if listing.published_at
                        else None
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                failed_row_ids.append(row_id)
                log.warning(
                    "discord_outbox_send_failed",
                    outbox_id=row_id,
                    channel_id=channel_id,
                    error=str(exc)[:200],
                )
            if index < len(payload_jobs) - 1 and delay > 0:
                time.sleep(delay)

    with session_scope() as session:
        if sent_row_ids:
            for row in session.scalars(
                select(DiscordOutbox).where(DiscordOutbox.id.in_(sent_row_ids))
            ).all():
                row.status = OUTBOX_STATUS_SENT
        if failed_row_ids:
            for row in session.scalars(
                select(DiscordOutbox).where(DiscordOutbox.id.in_(failed_row_ids))
            ).all():
                row.status = OUTBOX_STATUS_FAILED
        if posted_listing_ids:
            mark_discord_posted(session, posted_listing_ids)

    return len(sent_row_ids)


class DiscordFlushWorker(threading.Thread):
    """Thread unique : flush chronologique avec buffer anti-groupement."""

    def __init__(
        self,
        *,
        poll_seconds: float = 0.75,
        buffer_seconds: float = 2.5,
        name: str = "discord-flush",
    ) -> None:
        super().__init__(name=name, daemon=True)
        self.poll_seconds = max(0.2, float(poll_seconds))
        self.buffer_seconds = max(0.0, float(buffer_seconds))
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        log.info(
            "discord_flush_worker_start",
            poll_seconds=self.poll_seconds,
            buffer_seconds=self.buffer_seconds,
        )
        while not self._stop.is_set():
            try:
                flush_discord_outbox(buffer_seconds=self.buffer_seconds)
            except Exception as exc:  # noqa: BLE001
                log.exception("discord_flush_worker_failed", error=str(exc)[:200])
            self._stop.wait(self.poll_seconds)
        # Drain remaining without buffer on shutdown
        try:
            flush_discord_outbox(buffer_seconds=0.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("discord_flush_drain_failed", error=str(exc)[:160])
        log.info("discord_flush_worker_stopped")
