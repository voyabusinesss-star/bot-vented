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
    build_listing_payload,
    build_listing_preview_payload,
    pick_diverse_preview_listing,
)
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

OUTBOX_STATUS_PENDING = "pending"
OUTBOX_STATUS_SENT = "sent"
OUTBOX_STATUS_FAILED = "failed"
OUTBOX_STATUS_SKIPPED = "skipped"
KIND_BRAND = "brand"
KIND_ALL = "all"
KIND_PREVIEW = "preview"
_OUTBOX_DEDUP_STATUSES = (
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_SENT,
)
_MIN_DISCORD_POST_DELAY_SECONDS = 0.45


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
    from vinted_bot.notify.discord import (
        all_vetement_mirror_exclude_channels,
        channel_allows_listing,
        get_deal_evaluation,
        listing_is_shoe,
        route_channel,
        sanitize_discord_channel_id,
        should_mirror_listing_to_all_vetement,
    )

    settings = get_settings()
    deal = None
    try:
        deal = get_deal_evaluation(listing)
    except Exception:  # noqa: BLE001
        deal = None
    is_shoe = listing_is_shoe(listing, deal=deal)

    brand_channel_id = route_channel(
        listing.brand,
        settings.brand_channel_map(),
        sneaker_map=settings.sneaker_channel_map(),
        is_shoe=is_shoe,
    )
    sneaker_ids = set(settings.sneaker_channel_map().values())
    if not channel_allows_listing(
        channel_id=brand_channel_id,
        is_shoe=is_shoe,
        sneaker_channel_ids=sneaker_ids,
    ):
        return None, None, is_shoe

    all_channel = sanitize_discord_channel_id(settings.discord_channel_all)
    mirror_all = should_mirror_listing_to_all_vetement(
        listing.brand,
        is_shoe=is_shoe,
        brand_channel_id=brand_channel_id,
        sneaker_channel_ids=sneaker_ids,
        exclude_brand_channel_ids=all_vetement_mirror_exclude_channels(settings),
        settings=settings,
    )
    return brand_channel_id, (all_channel if mirror_all else None), is_shoe


def listing_ids_with_inflight_outbox(listing_ids: Sequence[int]) -> set[int]:
    """Listings avec outbox brand/all pending ou failed (anti double enqueue)."""
    if not listing_ids:
        return set()
    with session_scope() as session:
        rows = session.scalars(
            select(DiscordOutbox.listing_id)
            .where(DiscordOutbox.listing_id.in_(list(listing_ids)))
            .where(DiscordOutbox.kind.in_([KIND_BRAND, KIND_ALL]))
            .where(
                DiscordOutbox.status.in_(
                    [OUTBOX_STATUS_PENDING, OUTBOX_STATUS_FAILED]
                )
            )
        ).all()
    return set(int(x) for x in rows)


def pending_brand_listing_ids(listing_ids: Sequence[int]) -> set[int]:
    """Alias — pending/failed brand + all."""
    return listing_ids_with_inflight_outbox(listing_ids)


def _listing_discord_outbox_settled(session: Any, listing_id: int) -> bool:
    """Plus de file brand/all en attente pour cette annonce."""
    busy = session.scalar(
        select(DiscordOutbox.id)
        .where(DiscordOutbox.listing_id == listing_id)
        .where(DiscordOutbox.kind.in_([KIND_BRAND, KIND_ALL]))
        .where(
            DiscordOutbox.status.in_([OUTBOX_STATUS_PENDING, OUTBOX_STATUS_FAILED])
        )
        .limit(1)
    )
    return not busy


def _outbox_row_exists(
    session: Any,
    *,
    listing_id: int,
    channel_id: str,
    kind: str,
) -> bool:
    """True si cette annonce a déjà une row outbox (pending, failed ou sent)."""
    exists = session.scalar(
        select(DiscordOutbox.id)
        .where(DiscordOutbox.listing_id == listing_id)
        .where(DiscordOutbox.channel_id == channel_id)
        .where(DiscordOutbox.kind == kind)
        .where(DiscordOutbox.status.in_(_OUTBOX_DEDUP_STATUSES))
        .limit(1)
    )
    return bool(exists)


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
                if _outbox_row_exists(
                    session,
                    listing_id=int(listing.id),
                    channel_id=channel_id,
                    kind=kind,
                ):
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


def _preview_channel_id() -> str | None:
    settings = get_settings()
    return sanitize_discord_channel_id(
        getattr(settings, "discord_channel_bot_preview", "") or ""
    )


def enqueue_bot_preview_from_candidates(
    candidates: Sequence[Listing],
    *,
    settings: Any | None = None,
) -> bool:
    """Enqueue 1 aperçu bot (kind=preview) — flush worker poste avec drip."""
    import time

    from vinted_bot.notify import discord as discord_preview

    cfg = settings or get_settings()
    if not bool(getattr(cfg, "bot_preview_via_outbox", True)):
        return False
    channel_id = _preview_channel_id()
    if not channel_id:
        log.info("discord_bot_preview_skipped", reason="channel_not_configured")
        return False
    if not candidates:
        return False

    interval = float(getattr(cfg, "bot_preview_interval_seconds", 150.0) or 150.0)
    mono_now = time.monotonic()
    last_raw = getattr(discord_preview, "_last_bot_preview_post_at", 0.0) or 0.0
    last_at = float(last_raw) if isinstance(last_raw, (int, float)) else 0.0
    if last_at and (mono_now - last_at) < interval:
        log.info(
            "discord_bot_preview_skipped",
            reason="interval",
            wait_seconds=round(interval - (mono_now - last_at), 1),
            candidates=len(candidates),
        )
        return False

    with session_scope() as session:
        pending_preview = session.scalar(
            select(DiscordOutbox.id)
            .where(DiscordOutbox.kind == KIND_PREVIEW)
            .where(DiscordOutbox.status == OUTBOX_STATUS_PENDING)
            .limit(1)
        )
        if pending_preview:
            log.info("discord_bot_preview_skipped", reason="outbox_pending")
            return False

    listing = pick_diverse_preview_listing(list(candidates))
    if listing is None:
        log.info("discord_bot_preview_skipped", reason="no_candidate")
        return False

    utc_now = _utcnow()
    published = _as_aware(listing.published_at) or _as_aware(listing.first_seen_at) or utc_now
    if _is_stale_published_at(published, _max_listing_age_minutes()):
        log.info(
            "discord_bot_preview_skipped",
            reason="stale",
            vinted_id=listing.vinted_id,
        )
        return False

    with session_scope() as session:
        exists = session.scalar(
            select(DiscordOutbox.id)
            .where(DiscordOutbox.listing_id == listing.id)
            .where(DiscordOutbox.channel_id == channel_id)
            .where(DiscordOutbox.kind == KIND_PREVIEW)
            .where(DiscordOutbox.status.in_([OUTBOX_STATUS_PENDING, OUTBOX_STATUS_SENT]))
            .limit(1)
        )
        if exists:
            log.info("discord_bot_preview_skipped", reason="already_queued")
            return False
        session.add(
            DiscordOutbox(
                listing_id=listing.id,
                channel_id=channel_id,
                published_at=published,
                enqueued_at=utc_now,
                status=OUTBOX_STATUS_PENDING,
                kind=KIND_PREVIEW,
            )
        )
    discord_preview._last_bot_preview_post_at = mono_now
    brand = discord_preview._preview_brand_key(listing)
    tag = (
        f"{'shoe' if discord_preview._preview_is_shoe(listing) else 'cloth'}:{brand}"
    )
    discord_preview._recent_preview_brands = (
        discord_preview._recent_preview_brands + [tag]
    )[-discord_preview._RECENT_PREVIEW_BRANDS_MAX :]
    vid = int(getattr(listing, "vinted_id", 0) or 0)
    if vid:
        discord_preview._recent_preview_vinted_ids = (
            discord_preview._recent_preview_vinted_ids + [vid]
        )[-discord_preview._RECENT_PREVIEW_IDS_MAX :]
    log.info(
        "discord_bot_preview_enqueued",
        listing_id=listing.id,
        vinted_id=listing.vinted_id,
        brand=listing.brand,
        channel_id=channel_id,
    )
    return True


def requeue_retryable_failed_outbox(
    *,
    retry_after_seconds: float = 120.0,
    limit: int = 50,
) -> int:
    """Remet en pending les failed récents (429 Discord transient)."""
    from datetime import timedelta

    cutoff = _utcnow() - timedelta(seconds=max(30.0, retry_after_seconds))
    requeued = 0
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(DiscordOutbox)
                .where(DiscordOutbox.status == OUTBOX_STATUS_FAILED)
                .where(DiscordOutbox.enqueued_at >= cutoff)
                .order_by(DiscordOutbox.enqueued_at.asc())
                .limit(max(1, int(limit)))
            ).all()
        )
        for row in rows:
            already_sent = session.scalar(
                select(DiscordOutbox.id)
                .where(DiscordOutbox.listing_id == row.listing_id)
                .where(DiscordOutbox.channel_id == row.channel_id)
                .where(DiscordOutbox.kind == row.kind)
                .where(DiscordOutbox.status == OUTBOX_STATUS_SENT)
                .where(DiscordOutbox.id != row.id)
                .limit(1)
            )
            if already_sent:
                row.status = OUTBOX_STATUS_SKIPPED
                continue
            row.status = OUTBOX_STATUS_PENDING
            requeued += 1
    if requeued:
        log.info("discord_outbox_requeued_failed", count=requeued)
    return requeued


def discord_outbox_stats() -> dict[str, Any]:
    """Métriques outbox pour heartbeat scrape."""
    from sqlalchemy import func

    with session_scope() as session:
        pending = int(
            session.scalar(
                select(func.count())
                .select_from(DiscordOutbox)
                .where(DiscordOutbox.status == OUTBOX_STATUS_PENDING)
            )
            or 0
        )
        failed = int(
            session.scalar(
                select(func.count())
                .select_from(DiscordOutbox)
                .where(DiscordOutbox.status == OUTBOX_STATUS_FAILED)
            )
            or 0
        )
        oldest_pub = session.scalar(
            select(DiscordOutbox.published_at)
            .where(DiscordOutbox.status == OUTBOX_STATUS_PENDING)
            .order_by(DiscordOutbox.published_at.asc())
            .limit(1)
        )
    lag_seconds: float | None = None
    if oldest_pub is not None:
        pub = _as_aware(oldest_pub)
        if pub is not None:
            lag_seconds = round((_utcnow() - pub).total_seconds(), 1)
    stats: dict[str, Any] = {
        "outbox_pending": pending,
        "outbox_failed": failed,
    }
    if lag_seconds is not None:
        stats["outbox_lag_seconds"] = lag_seconds
    return stats


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
                if row.kind == KIND_PREVIEW:
                    built_payloads[listing.id] = build_listing_preview_payload(listing)
                else:
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

    sent_row_ids: list[int] = []
    failed_row_ids: list[int] = []
    delay = float(settings.discord_post_delay_seconds or 0.0)
    if delay <= 0:
        # delay=0 → rafales 429 → failed requeue → doublons salon
        delay = _MIN_DISCORD_POST_DELAY_SECONDS

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
                if kind == KIND_PREVIEW:
                    log.info(
                        "discord_bot_preview_posted",
                        outbox_id=row_id,
                        vinted_id=vinted_id,
                        channel_id=channel_id,
                        brand=brand,
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
        if sent_row_ids:
            sent_rows = list(
                session.scalars(
                    select(DiscordOutbox).where(DiscordOutbox.id.in_(sent_row_ids))
                ).all()
            )
            settled_ids = {
                int(row.listing_id)
                for row in sent_rows
                if row.kind in (KIND_BRAND, KIND_ALL)
                and _listing_discord_outbox_settled(session, int(row.listing_id))
            }
            if settled_ids:
                mark_discord_posted(session, list(settled_ids))

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
        requeue_tick = 0
        while not self._stop.is_set():
            requeue_tick += 1
            if requeue_tick >= 30:
                requeue_tick = 0
                try:
                    requeue_retryable_failed_outbox()
                except Exception as exc:  # noqa: BLE001
                    log.warning("discord_outbox_requeue_failed", error=str(exc)[:200])
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
