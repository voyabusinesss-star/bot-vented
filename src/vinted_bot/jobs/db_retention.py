"""Rétention DB : volume Railway 500 Mo — ne jamais saturer.

- Purge adaptative (normal → serré → critique → urgence)
- raw_json slim à l'écriture (slim_listing_raw_json)
- Tables market/niches incluses (niche_snapshots, opportunity_history, trends)
- Worker 24/7 sur bot-scrape + passe throttle sur bot-detector
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any

from sqlalchemy import delete, func, select, text

from vinted_bot.db.models import (
    DiscordOutbox,
    Listing,
    ListingObservation,
    NicheSnapshot,
    OpportunityHistory,
    PrivateAlertOutbox,
    ScrapeRun,
    TrendSnapshot,
    UserFilterAlert,
)
from vinted_bot.db.repositories import get_checkpoint, set_checkpoint
from vinted_bot.db.session import get_engine, session_scope
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

_CHECKPOINT_LAST_RUN = "db_retention:last_completed_at"
_BATCH_SIZE = 800

# Volume Railway fixe 500 Mo — marge pour WAL/recovery Postgres (~60 Mo).
DEFAULT_VOLUME_MB = 500
DEFAULT_TARGET_MB = 320
DEFAULT_WARN_MB = 380
DEFAULT_CRITICAL_MB = 420
DEFAULT_EMERGENCY_MB = 445


class RetentionTier(str, Enum):
    NORMAL = "normal"
    TIGHT = "tight"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    tier: RetentionTier
    listings_keep_hours: float
    raw_json_keep_hours: float
    outbox_keep_hours: float
    observations_keep_hours: float
    scrape_runs_keep_hours: float
    entities_keep_hours: float
    filter_alerts_keep_days: float
    private_outbox_keep_hours: float
    niche_snapshots_keep_days: float
    opportunity_posted_keep_days: float
    opportunity_unposted_keep_hours: float
    trend_snapshots_keep_days: float
    null_posted_raw_json: bool
    reclaim_to_target: bool


def _volume_bytes() -> int:
    from vinted_bot.config import get_settings

    mb = int(getattr(get_settings(), "db_volume_mb", DEFAULT_VOLUME_MB) or DEFAULT_VOLUME_MB)
    return max(100, mb) * 1024 * 1024


def _target_bytes() -> int:
    from vinted_bot.config import get_settings

    mb = int(getattr(get_settings(), "db_retention_target_mb", DEFAULT_TARGET_MB) or DEFAULT_TARGET_MB)
    return min(_volume_bytes() - 80 * 1024 * 1024, max(50, mb) * 1024 * 1024)


def retention_tier(size_bytes: int | None) -> RetentionTier:
    if size_bytes is None:
        return RetentionTier.NORMAL
    vol = _volume_bytes()
    warn = int(vol * DEFAULT_WARN_MB / DEFAULT_VOLUME_MB)
    critical = int(vol * DEFAULT_CRITICAL_MB / DEFAULT_VOLUME_MB)
    emergency = int(vol * DEFAULT_EMERGENCY_MB / DEFAULT_VOLUME_MB)
    if size_bytes >= emergency:
        return RetentionTier.EMERGENCY
    if size_bytes >= critical:
        return RetentionTier.CRITICAL
    if size_bytes >= warn:
        return RetentionTier.TIGHT
    return RetentionTier.NORMAL


def retention_policy_for_tier(tier: RetentionTier) -> RetentionPolicy:
    if tier == RetentionTier.EMERGENCY:
        return RetentionPolicy(
            tier=tier,
            listings_keep_hours=6.0,
            raw_json_keep_hours=0.25,
            outbox_keep_hours=0.5,
            observations_keep_hours=0.25,
            scrape_runs_keep_hours=6.0,
            entities_keep_hours=6.0,
            filter_alerts_keep_days=1.0,
            private_outbox_keep_hours=6.0,
            niche_snapshots_keep_days=2.0,
            opportunity_posted_keep_days=2.0,
            opportunity_unposted_keep_hours=12.0,
            trend_snapshots_keep_days=3.0,
            null_posted_raw_json=True,
            reclaim_to_target=True,
        )
    if tier == RetentionTier.CRITICAL:
        return RetentionPolicy(
            tier=tier,
            listings_keep_hours=12.0,
            raw_json_keep_hours=0.5,
            outbox_keep_hours=1.0,
            observations_keep_hours=0.5,
            scrape_runs_keep_hours=12.0,
            entities_keep_hours=12.0,
            filter_alerts_keep_days=2.0,
            private_outbox_keep_hours=12.0,
            niche_snapshots_keep_days=5.0,
            opportunity_posted_keep_days=5.0,
            opportunity_unposted_keep_hours=24.0,
            trend_snapshots_keep_days=7.0,
            null_posted_raw_json=True,
            reclaim_to_target=True,
        )
    if tier == RetentionTier.TIGHT:
        return RetentionPolicy(
            tier=tier,
            listings_keep_hours=18.0,
            raw_json_keep_hours=0.75,
            outbox_keep_hours=1.5,
            observations_keep_hours=0.75,
            scrape_runs_keep_hours=18.0,
            entities_keep_hours=18.0,
            filter_alerts_keep_days=3.0,
            private_outbox_keep_hours=18.0,
            niche_snapshots_keep_days=7.0,
            opportunity_posted_keep_days=7.0,
            opportunity_unposted_keep_hours=36.0,
            trend_snapshots_keep_days=10.0,
            null_posted_raw_json=True,
            reclaim_to_target=False,
        )
    return RetentionPolicy(
        tier=RetentionTier.NORMAL,
        listings_keep_hours=24.0,
        raw_json_keep_hours=1.0,
        outbox_keep_hours=2.0,
        observations_keep_hours=1.0,
        scrape_runs_keep_hours=24.0,
        entities_keep_hours=24.0,
        filter_alerts_keep_days=3.0,
        private_outbox_keep_hours=24.0,
        niche_snapshots_keep_days=10.0,
        opportunity_posted_keep_days=10.0,
        opportunity_unposted_keep_hours=48.0,
        trend_snapshots_keep_days=14.0,
        null_posted_raw_json=True,
        reclaim_to_target=False,
    )


def slim_listing_raw_json(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Ne garde que le strict nécessaire aux embeds Discord (pas le JSON Vinted complet)."""
    if not isinstance(raw, dict):
        return None
    slim: dict[str, Any] = {}
    for key in (
        "id",
        "title",
        "price",
        "currency",
        "brand_title",
        "size_title",
        "status",
        "status_id",
        "status_title",
        "url",
        "path",
        "photo",
        "photos",
        "favourite_count",
        "view_count",
        "created_at_ts",
        "user",
    ):
        if key in raw:
            slim[key] = raw[key]
    user = slim.get("user")
    if isinstance(user, dict):
        slim["user"] = {
            k: user.get(k)
            for k in ("id", "login", "username", "photo")
            if k in user
        }
    photos = slim.get("photos")
    if isinstance(photos, list) and len(photos) > 3:
        slim["photos"] = photos[:3]
    return slim or None


def get_database_size_bytes() -> int | None:
    try:
        with session_scope() as session:
            value = session.execute(
                text("SELECT pg_database_size(current_database())")
            ).scalar()
            return int(value) if value is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning("db_size_check_failed", error=str(exc)[:160])
        return None


def log_db_disk_usage(*, context: str = "retention", tier: RetentionTier | None = None) -> int | None:
    size = get_database_size_bytes()
    if size is None:
        return None
    mb = round(size / (1024 * 1024), 1)
    vol_mb = _volume_bytes() // (1024 * 1024)
    target_mb = _target_bytes() // (1024 * 1024)
    resolved_tier = tier or retention_tier(size)
    payload = {
        "context": context,
        "tier": resolved_tier.value,
        "db_bytes": size,
        "db_mb": mb,
        "volume_mb": vol_mb,
        "target_mb": target_mb,
    }
    if resolved_tier == RetentionTier.EMERGENCY:
        log.error("db_disk_emergency", **payload)
    elif resolved_tier == RetentionTier.CRITICAL:
        log.error("db_disk_critical", **payload)
    elif resolved_tier == RetentionTier.TIGHT:
        log.warning("db_disk_warning", **payload)
    else:
        log.info("db_disk_ok", **payload)
    return size


def _null_raw_json_batches(*, cutoff: datetime, max_batches: int = 40) -> int:
    nulled = 0
    for _ in range(max_batches):
        with session_scope() as session:
            res = session.execute(
                text(
                    """
                    UPDATE listings
                    SET raw_json = NULL
                    WHERE id IN (
                      SELECT id FROM listings
                      WHERE raw_json IS NOT NULL
                        AND first_seen_at IS NOT NULL
                        AND first_seen_at < :cutoff
                      ORDER BY id
                      LIMIT 200
                    )
                    """
                ),
                {"cutoff": cutoff},
            )
            n = int(res.rowcount or 0)
        nulled += n
        if n == 0:
            break
    return nulled


def _null_posted_raw_json() -> int:
    with session_scope() as session:
        res = session.execute(
            text(
                """
                UPDATE listings
                SET raw_json = NULL
                WHERE raw_json IS NOT NULL
                  AND discord_posted_at IS NOT NULL
                """
            )
        )
        return int(res.rowcount or 0)


def _purge_niche_snapshots(*, cutoff: datetime, emergency: bool) -> int:
    with session_scope() as session:
        if emergency:
            res = session.execute(
                delete(NicheSnapshot).where(NicheSnapshot.computed_at < cutoff)
            )
        else:
            res = session.execute(
                delete(NicheSnapshot).where(
                    NicheSnapshot.computed_at < cutoff,
                    func.coalesce(NicheSnapshot.score, 0) < 55,
                )
            )
        return int(res.rowcount or 0)


def _purge_opportunity_history(*, posted_cutoff: datetime, unposted_cutoff: datetime) -> int:
    deleted = 0
    with session_scope() as session:
        res = session.execute(
            delete(OpportunityHistory).where(
                OpportunityHistory.posted.is_(True),
                OpportunityHistory.detected_at < posted_cutoff,
            )
        )
        deleted += int(res.rowcount or 0)
        res = session.execute(
            delete(OpportunityHistory).where(
                OpportunityHistory.posted.is_(False),
                OpportunityHistory.detected_at < unposted_cutoff,
            )
        )
        deleted += int(res.rowcount or 0)
    return deleted


def _purge_trend_snapshots(*, cutoff_date: date) -> int:
    with session_scope() as session:
        res = session.execute(
            delete(TrendSnapshot).where(TrendSnapshot.snapshot_date < cutoff_date)
        )
        return int(res.rowcount or 0)


def _delete_old_listings(*, cutoff: datetime, max_batches: int = 80) -> int:
    deleted = 0
    for _ in range(max_batches):
        with session_scope() as session:
            ids = list(
                session.scalars(
                    select(Listing.id)
                    .where(
                        func.coalesce(
                            Listing.first_seen_at,
                            Listing.scraped_at,
                            Listing.updated_at,
                        )
                        < cutoff
                    )
                    .order_by(Listing.id)
                    .limit(_BATCH_SIZE)
                ).all()
            )
            if not ids:
                break
            session.execute(delete(Listing).where(Listing.id.in_(ids)))
            deleted += len(ids)
        if len(ids) < _BATCH_SIZE:
            break
    return deleted


def _reclaim_until_target(*, target_bytes: int, stats: dict[str, int]) -> None:
    """Supprime les listings les plus anciens par vagues jusqu'à la cible."""
    for wave in range(12):
        size = get_database_size_bytes()
        if size is None or size <= target_bytes:
            return
        hours = max(3.0, 24.0 - wave * 2.0)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        extra = _delete_old_listings(cutoff=cutoff, max_batches=20)
        stats["listings_deleted"] += extra
        log.warning(
            "db_retention_reclaim_wave",
            wave=wave + 1,
            hours_kept=hours,
            deleted=extra,
            db_mb=round(size / (1024 * 1024), 1),
            target_mb=target_bytes // (1024 * 1024),
        )
        if extra == 0:
            break
        _vacuum_tables()
        after = get_database_size_bytes()
        if after is not None and after <= target_bytes:
            return


def _vacuum_tables() -> None:
    try:
        eng = get_engine()
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for dead in (
                "listings_dead",
                "listing_entities_broken_old",
                "photos_broken_old",
            ):
                try:
                    conn.execute(text(f"DROP TABLE IF EXISTS {dead} CASCADE"))
                    log.info("db_retention_dropped_orphan", table=dead)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "db_retention_drop_orphan_failed",
                        table=dead,
                        error=str(exc)[:120],
                    )
            for table in (
                "listings",
                "photos",
                "listing_observations",
                "listing_entities",
                "niche_snapshots",
                "opportunity_history",
                "trend_snapshots",
                "discord_outbox",
                "private_alert_outbox",
                "user_filter_alerts",
                "scrape_runs",
            ):
                try:
                    conn.execute(text(f"VACUUM (ANALYZE) {table}"))
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        log.warning("db_retention_vacuum_failed", error=str(exc)[:160])


def run_db_retention_once(*, force_tier: RetentionTier | None = None) -> dict[str, Any]:
    """Purge une passe adaptée à la pression disque."""
    now = datetime.now(timezone.utc)
    before = get_database_size_bytes()
    tier = force_tier or retention_tier(before)
    policy = retention_policy_for_tier(tier)

    stats: dict[str, Any] = {
        "tier": tier.value,
        "listings_deleted": 0,
        "raw_nulled": 0,
        "posted_raw_nulled": 0,
        "outbox_deleted": 0,
        "observations_deleted": 0,
        "photos_deleted": 0,
        "entities_deleted": 0,
        "scrape_runs_deleted": 0,
        "filter_alerts_deleted": 0,
        "private_outbox_deleted": 0,
        "niche_snapshots_deleted": 0,
        "opportunity_history_deleted": 0,
        "trend_snapshots_deleted": 0,
        "db_mb_before": int((before or 0) // (1024 * 1024)),
        "db_mb_after": 0,
    }

    listings_cutoff = now - timedelta(hours=policy.listings_keep_hours)
    raw_cutoff = now - timedelta(hours=policy.raw_json_keep_hours)
    outbox_cutoff = now - timedelta(hours=policy.outbox_keep_hours)
    obs_cutoff = now - timedelta(hours=policy.observations_keep_hours)
    runs_cutoff = now - timedelta(hours=policy.scrape_runs_keep_hours)
    entities_cutoff = now - timedelta(hours=policy.entities_keep_hours)
    alerts_cutoff = now - timedelta(days=policy.filter_alerts_keep_days)
    private_outbox_cutoff = now - timedelta(hours=policy.private_outbox_keep_hours)
    niche_cutoff = now - timedelta(days=policy.niche_snapshots_keep_days)
    opp_posted_cutoff = now - timedelta(days=policy.opportunity_posted_keep_days)
    opp_unposted_cutoff = now - timedelta(hours=policy.opportunity_unposted_keep_hours)
    trend_cutoff = now.date() - timedelta(days=int(policy.trend_snapshots_keep_days))

    if policy.null_posted_raw_json:
        stats["posted_raw_nulled"] = _null_posted_raw_json()

    stats["raw_nulled"] = _null_raw_json_batches(cutoff=raw_cutoff)

    with session_scope() as session:
        res = session.execute(
            delete(DiscordOutbox).where(
                (DiscordOutbox.status != "pending")
                | (DiscordOutbox.enqueued_at < outbox_cutoff)
            )
        )
        stats["outbox_deleted"] = int(res.rowcount or 0)

        res = session.execute(
            delete(UserFilterAlert).where(UserFilterAlert.sent_at < alerts_cutoff)
        )
        stats["filter_alerts_deleted"] = int(res.rowcount or 0)

        res = session.execute(
            delete(PrivateAlertOutbox).where(
                (PrivateAlertOutbox.status != "pending")
                | (PrivateAlertOutbox.enqueued_at < private_outbox_cutoff)
            )
        )
        stats["private_outbox_deleted"] = int(res.rowcount or 0)

        res = session.execute(
            delete(ListingObservation).where(ListingObservation.observed_at < obs_cutoff)
        )
        stats["observations_deleted"] = int(res.rowcount or 0)

        res = session.execute(delete(ScrapeRun).where(ScrapeRun.started_at < runs_cutoff))
        stats["scrape_runs_deleted"] = int(res.rowcount or 0)

        try:
            res = session.execute(
                text(
                    """
                    DELETE FROM listing_entities e
                    USING listings l
                    WHERE e.listing_id = l.id
                      AND COALESCE(l.first_seen_at, l.scraped_at, l.updated_at)
                          < :cutoff
                    """
                ),
                {"cutoff": entities_cutoff},
            )
            stats["entities_deleted"] = int(res.rowcount or 0)
        except Exception as exc:  # noqa: BLE001
            log.warning("db_retention_entities_failed", error=str(exc)[:160])

    stats["niche_snapshots_deleted"] = _purge_niche_snapshots(
        cutoff=niche_cutoff,
        emergency=tier == RetentionTier.EMERGENCY,
    )
    stats["opportunity_history_deleted"] = _purge_opportunity_history(
        posted_cutoff=opp_posted_cutoff,
        unposted_cutoff=opp_unposted_cutoff,
    )
    stats["trend_snapshots_deleted"] = _purge_trend_snapshots(cutoff_date=trend_cutoff)

    stats["listings_deleted"] += _delete_old_listings(cutoff=listings_cutoff)

    with session_scope() as session:
        try:
            res = session.execute(
                text(
                    "DELETE FROM photos p WHERE NOT EXISTS "
                    "(SELECT 1 FROM listings l WHERE l.id = p.listing_id)"
                )
            )
            stats["photos_deleted"] = int(res.rowcount or 0)
        except Exception:  # noqa: BLE001
            pass

    _vacuum_tables()

    if policy.reclaim_to_target and before is not None and before > _target_bytes():
        _reclaim_until_target(target_bytes=_target_bytes(), stats=stats)

    after = log_db_disk_usage(context="retention", tier=tier)
    if after is not None:
        stats["db_mb_after"] = int(after // (1024 * 1024))

    try:
        with session_scope() as session:
            set_checkpoint(
                session,
                _CHECKPOINT_LAST_RUN,
                {"ts": time.time(), "tier": tier.value, **stats},
            )
    except Exception:  # noqa: BLE001
        pass

    log.info("db_retention_done", **stats)
    return stats


def retention_due(*, min_interval_seconds: float) -> bool:
    try:
        with session_scope() as session:
            data = get_checkpoint(session, _CHECKPOINT_LAST_RUN) or {}
        raw = data.get("ts") if isinstance(data, dict) else None
        if raw is None:
            return True
        return (time.time() - float(raw)) >= max(30.0, min_interval_seconds)
    except Exception:  # noqa: BLE001
        return True


def run_db_retention_if_due(*, min_interval_seconds: float = 120.0) -> dict[str, Any] | None:
    if not retention_due(min_interval_seconds=min_interval_seconds):
        return None
    return run_db_retention_once()


def worker_interval_for_size(size_bytes: int | None) -> float:
    from vinted_bot.config import get_settings

    base = float(getattr(get_settings(), "db_retention_interval_seconds", 180.0) or 180.0)
    tier = retention_tier(size_bytes)
    if tier == RetentionTier.EMERGENCY:
        return max(60.0, base * 0.33)
    if tier == RetentionTier.CRITICAL:
        return max(90.0, base * 0.5)
    if tier == RetentionTier.TIGHT:
        return max(120.0, base * 0.75)
    return base


_retention_worker: DbRetentionWorker | None = None
_retention_lock = threading.Lock()


def ensure_db_retention_worker(*, interval_seconds: float | None = None) -> DbRetentionWorker:
    global _retention_worker
    with _retention_lock:
        if _retention_worker is not None and _retention_worker.is_alive():
            return _retention_worker
        interval = interval_seconds
        if interval is None:
            from vinted_bot.config import get_settings

            interval = float(
                getattr(get_settings(), "db_retention_interval_seconds", 180.0) or 180.0
            )
        _retention_worker = DbRetentionWorker(interval_seconds=interval)
        _retention_worker.start()
        return _retention_worker


class DbRetentionWorker:
    """Thread daemon : purge périodique anti-DiskFull."""

    def __init__(self, *, interval_seconds: float = 180.0) -> None:
        self.interval_seconds = max(60.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="db-retention",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "db_retention_worker_start",
            interval=self.interval_seconds,
            target_mb=_target_bytes() // (1024 * 1024),
            volume_mb=_volume_bytes() // (1024 * 1024),
        )

    def stop(self) -> None:
        self._stop.set()

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        try:
            run_db_retention_once()
        except Exception as exc:  # noqa: BLE001
            log.exception("db_retention_boot_failed", error=str(exc)[:200])
        while not self._stop.is_set():
            size = get_database_size_bytes()
            wait_s = worker_interval_for_size(size)
            self._stop.wait(wait_s)
            if self._stop.is_set():
                break
            try:
                run_db_retention_once()
            except Exception as exc:  # noqa: BLE001
                log.exception("db_retention_cycle_failed", error=str(exc)[:200])
