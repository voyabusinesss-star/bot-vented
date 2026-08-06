"""Rétention DB : garde le volume Postgres 500Mo sous contrôle.

- Purge périodique (thread daemon sur bot-scrape)
- raw_json slim à l'écriture (voir slim_listing_raw_json)
- VACUUM après purge
- Alerte log si DB > seuil (défaut 400 Mo)
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, text, update

from vinted_bot.db.models import (
    DiscordOutbox,
    Listing,
    ListingObservation,
    ScrapeRun,
)
from vinted_bot.db.session import get_engine, session_scope
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

# Dédup Discord : garder les rows listings (sans gros JSON) ~2 jours.
# Un item Vinted raremeent revient avec le même ID après ça.
LISTINGS_KEEP_HOURS = 48.0
# Toast JSONB : inutile dès que Discord a posté / embed plus besoin.
RAW_JSON_KEEP_HOURS = 1.0
OUTBOX_KEEP_HOURS = 2.0
OBSERVATIONS_KEEP_HOURS = 1.0
SCRAPE_RUNS_KEEP_HOURS = 24.0
ENTITIES_KEEP_HOURS = 24.0
BATCH_SIZE = 800
# Volume Railway = 500 Mo → alerte avant saturation.
DB_WARN_BYTES = 400 * 1024 * 1024
DB_CRITICAL_BYTES = 450 * 1024 * 1024


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


def log_db_disk_usage(*, context: str = "retention") -> int | None:
    """Log taille DB + warning/critical avant saturation volume 500Mo."""
    size = get_database_size_bytes()
    if size is None:
        return None
    mb = round(size / (1024 * 1024), 1)
    payload = {
        "context": context,
        "db_bytes": size,
        "db_mb": mb,
        "volume_mb": 500,
        "warn_mb": DB_WARN_BYTES // (1024 * 1024),
    }
    if size >= DB_CRITICAL_BYTES:
        log.error("db_disk_critical", **payload)
    elif size >= DB_WARN_BYTES:
        log.warning("db_disk_warning", **payload)
    else:
        log.info("db_disk_ok", **payload)
    return size


def run_db_retention_once() -> dict[str, int]:
    """Purge une passe : vieux listings, raw_json, outbox, obs, vacuum."""
    now = datetime.now(timezone.utc)
    listings_cutoff = now - timedelta(hours=LISTINGS_KEEP_HOURS)
    raw_cutoff = now - timedelta(hours=RAW_JSON_KEEP_HOURS)
    outbox_cutoff = now - timedelta(hours=OUTBOX_KEEP_HOURS)
    obs_cutoff = now - timedelta(hours=OBSERVATIONS_KEEP_HOURS)
    runs_cutoff = now - timedelta(hours=SCRAPE_RUNS_KEEP_HOURS)
    entities_cutoff = now - timedelta(hours=ENTITIES_KEEP_HOURS)

    stats = {
        "listings_deleted": 0,
        "raw_nulled": 0,
        "outbox_deleted": 0,
        "observations_deleted": 0,
        "photos_deleted": 0,
        "entities_deleted": 0,
        "scrape_runs_deleted": 0,
        "db_mb_before": 0,
        "db_mb_after": 0,
    }

    before = get_database_size_bytes()
    if before is not None:
        stats["db_mb_before"] = int(before // (1024 * 1024))

    with session_scope() as session:
        # Null raw_json par petits lots (évite de lock toute la table)
        raw_nulled = 0
        for _ in range(30):
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
                {"cutoff": raw_cutoff},
            )
            n = int(res.rowcount or 0)
            raw_nulled += n
            if n == 0:
                break
        stats["raw_nulled"] = raw_nulled

        res = session.execute(
            delete(DiscordOutbox).where(
                (DiscordOutbox.status != "pending")
                | (DiscordOutbox.enqueued_at < outbox_cutoff)
            )
        )
        stats["outbox_deleted"] = int(res.rowcount or 0)

        res = session.execute(
            delete(ListingObservation).where(
                ListingObservation.observed_at < obs_cutoff
            )
        )
        stats["observations_deleted"] = int(res.rowcount or 0)

        res = session.execute(
            delete(ScrapeRun).where(ScrapeRun.started_at < runs_cutoff)
        )
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

    deleted = 0
    for _ in range(60):
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
                        < listings_cutoff
                    )
                    .order_by(Listing.id)
                    .limit(BATCH_SIZE)
                ).all()
            )
            if not ids:
                break
            session.execute(delete(Listing).where(Listing.id.in_(ids)))
            deleted += len(ids)
        if len(ids) < BATCH_SIZE:
            break
    stats["listings_deleted"] = deleted

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

    try:
        eng = get_engine()
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            # Tables mortes post-corruption (libèrent le volume 500Mo)
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
                "discord_outbox",
                "scrape_runs",
            ):
                try:
                    conn.execute(text(f"VACUUM (ANALYZE) {table}"))
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        log.warning("db_retention_vacuum_failed", error=str(exc)[:160])

    after = log_db_disk_usage(context="retention")
    if after is not None:
        stats["db_mb_after"] = int(after // (1024 * 1024))

    log.info("db_retention_done", **stats)
    return stats


class DbRetentionWorker:
    """Thread daemon : purge périodique anti-DiskFull."""

    def __init__(self, *, interval_seconds: float = 300.0) -> None:
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
            listings_keep_hours=LISTINGS_KEEP_HOURS,
            raw_keep_hours=RAW_JSON_KEEP_HOURS,
            warn_mb=DB_WARN_BYTES // (1024 * 1024),
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
            self._stop.wait(self.interval_seconds)
            if self._stop.is_set():
                break
            try:
                run_db_retention_once()
            except Exception as exc:  # noqa: BLE001
                log.exception("db_retention_cycle_failed", error=str(exc)[:200])
