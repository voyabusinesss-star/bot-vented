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
    route_channel,
)
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

OUTBOX_STATUS_PENDING = "pending"
OUTBOX_STATUS_SENT = "sent"
OUTBOX_STATUS_FAILED = "failed"
OUTBOX_STATUS_SKIPPED = "skipped"
KIND_BRAND = "brand"
KIND_ALL = "all"


def _max_listing_age_minutes() -> int | None:
    try:
        from vinted_bot.services.deal_filter import load_deal_filters

        return load_deal_filters().settings.max_listing_age_minutes
    except Exception:  # noqa: BLE001
        return 8


def _is_stale_published_at(published_at: datetime | None, max_age_minutes: int | None) -> bool:
    if max_age_minutes is None or published_at is None:
        return False
    pub = _as_aware(published_at)
    if pub is None:
        return False
    age_m = (_utcnow() - pub).total_seconds() / 60.0
    return age_m > float(max_age_minutes)


def purge_stale_discord_outbox() -> int:
    """One-shot : skip tous les pending trop vieux (purge backlog)."""
    max_age = _max_listing_age_minutes()
    if max_age is None:
        return 0
    from datetime import timedelta

    cutoff = _utcnow() - timedelta(minutes=float(max_age))
    skipped_listing_ids: list[int] = []
    skipped = 0
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(DiscordOutbox)
                .where(DiscordOutbox.status == OUTBOX_STATUS_PENDING)
                .where(DiscordOutbox.published_at < cutoff)
                .limit(5000)
            ).all()
        )
        for row in rows:
            row.status = OUTBOX_STATUS_SKIPPED
            skipped += 1
            if row.kind == KIND_BRAND:
                skipped_listing_ids.append(int(row.listing_id))
        if skipped_listing_ids:
            mark_discord_posted(session, list(dict.fromkeys(skipped_listing_ids)))
    if skipped:
        log.info(
            "discord_outbox_purged_stale",
            count=skipped,
            max_age_minutes=max_age,
        )
    return skipped


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
    # Indémodables → toujours mirror ALL (sacs/accessoires inclus)
    mirror_all = (
        bool(all_channel)
        and all_channel != brand_channel_id
        and belongs_in_all_vetement(
            listing.brand,
            is_shoe=is_shoe,
            brand_channel_id=brand_channel_id,
            sneaker_channel_ids=sneaker_ids,
        )
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
            if _is_stale_published_at(published, _max_listing_age_minutes()):
                log.info(
                    "outbox_skip_stale",
                    brand=listing.brand,
                    vinted_id=listing.vinted_id,
                    published_at=published.isoformat(),
                )
                mark_discord_posted(session, [listing.id])
                continue

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
    max_messages: int = 5,
) -> int:
    """Envoie un drip de pending triés par published_at DESC (plus récent d'abord).

    buffer_seconds : n'envoie que les rows avec enqueued_at <= now - buffer.
    max_messages : cap global par tick (flux continu, anti-vague).
    """
    settings = get_settings()
    if not settings.discord_bot_token.strip():
        return 0

    cutoff = _utcnow()
    if buffer_seconds > 0:
        from datetime import timedelta

        cutoff = cutoff - timedelta(seconds=buffer_seconds)

    drip_cap = max(1, int(max_messages))

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(DiscordOutbox)
                .where(DiscordOutbox.status == OUTBOX_STATUS_PENDING)
                .where(DiscordOutbox.enqueued_at <= cutoff)
                .order_by(
                    DiscordOutbox.published_at.desc(),
                    DiscordOutbox.id.desc(),
                )
                .limit(200)
            ).all()
        )
        if not rows:
            return 0

        max_age = _max_listing_age_minutes()
        fresh_rows: list[DiscordOutbox] = []
        skipped_listing_ids: list[int] = []
        skipped_count = 0
        for row in rows:
            if _is_stale_published_at(row.published_at, max_age):
                row.status = OUTBOX_STATUS_SKIPPED
                skipped_count += 1
                if row.kind == KIND_BRAND:
                    skipped_listing_ids.append(int(row.listing_id))
                continue
            fresh_rows.append(row)
        if skipped_count:
            log.info(
                "discord_outbox_skipped_stale",
                count=skipped_count,
                max_age_minutes=max_age,
            )
        if skipped_listing_ids:
            mark_discord_posted(session, skipped_listing_ids)
        if not fresh_rows:
            return 0

        # Drip newest-first — pas de dump par salon
        ordered = fresh_rows[:drip_cap]
        pending_left = max(0, len(fresh_rows) - len(ordered))
        newest = ordered[0].published_at if ordered else None
        newest_age: float | None = None
        if newest is not None:
            pub = _as_aware(newest)
            if pub is not None:
                newest_age = round((_utcnow() - pub).total_seconds(), 1)

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
        payload_jobs: list[
            tuple[int, str, str, int, int | None, str | None, str | None, dict[str, Any]]
        ] = []
        built_payloads: dict[int, dict[str, Any]] = {}
        for row in ordered:
            listing = listings.get(row.listing_id)
            if listing is None:
                row.status = OUTBOX_STATUS_FAILED
                continue
            if listing.id not in built_payloads:
                built_payloads[listing.id] = build_listing_payload(listing)
            payload_jobs.append(
                (
                    int(row.id),
                    str(row.channel_id),
                    str(row.kind),
                    int(listing.id),
                    int(listing.vinted_id) if listing.vinted_id is not None else None,
                    listing.brand,
                    (
                        listing.published_at.isoformat()
                        if listing.published_at is not None
                        else None
                    ),
                    built_payloads[listing.id],
                )
            )

    if not payload_jobs:
        return 0

    posted_listing_ids: list[int] = []
    sent_row_ids: list[int] = []
    failed_row_ids: list[int] = []
    delay = float(settings.discord_post_delay_seconds or 0.0)

    with DiscordNotifier(settings) as notifier:
        for index, (
            row_id,
            channel_id,
            kind,
            listing_id,
            vinted_id,
            brand,
            published_at,
            payload,
        ) in enumerate(payload_jobs):
            try:
                notifier.post_message(channel_id, payload)
                sent_row_ids.append(row_id)
                if kind == KIND_BRAND:
                    posted_listing_ids.append(listing_id)
                log.info(
                    "discord_outbox_sent",
                    outbox_id=row_id,
                    kind=kind,
                    channel_id=channel_id,
                    listing_id=listing_id,
                    vinted_id=vinted_id,
                    brand=brand,
                    published_at=published_at,
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

    log.info(
        "discord_outbox_drip",
        sent=len(sent_row_ids),
        pending_left=pending_left,
        newest_age_seconds=newest_age,
        max_messages=drip_cap,
    )
    if newest_age is not None and newest_age > 30 and pending_left > 0:
        log.warning(
            "discord_outbox_lagging",
            newest_age_seconds=newest_age,
            pending_left=pending_left,
        )
    return len(sent_row_ids)


class DiscordFlushWorker(threading.Thread):
    """Thread unique : drip newest-first continu (anti-vagues, anti-backlog)."""

    def __init__(
        self,
        *,
        poll_seconds: float = 0.4,
        buffer_seconds: float = 0.2,
        max_messages: int = 5,
        name: str = "discord-flush",
    ) -> None:
        super().__init__(name=name, daemon=True)
        self.poll_seconds = max(0.15, float(poll_seconds))
        self.buffer_seconds = max(0.0, float(buffer_seconds))
        self.max_messages = max(1, int(max_messages))
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        log.info(
            "discord_flush_worker_start",
            poll_seconds=self.poll_seconds,
            buffer_seconds=self.buffer_seconds,
            max_messages=self.max_messages,
        )
        try:
            purge_stale_discord_outbox()
        except Exception as exc:  # noqa: BLE001
            log.warning("discord_outbox_purge_failed", error=str(exc)[:200])
        while not self._stop.is_set():
            try:
                flush_discord_outbox(
                    buffer_seconds=self.buffer_seconds,
                    max_messages=self.max_messages,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("discord_flush_worker_failed", error=str(exc)[:200])
            self._stop.wait(self.poll_seconds)
        try:
            for _ in range(50):
                sent = flush_discord_outbox(buffer_seconds=0.0, max_messages=10)
                if sent <= 0:
                    break
        except Exception as exc:  # noqa: BLE001
            log.warning("discord_flush_drain_failed", error=str(exc)[:160])
        log.info("discord_flush_worker_stopped")
