"""Réconciliation : marque les annonces absentes comme disparues (proxy vente)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from vinted_bot.db.models import Listing
from vinted_bot.db.repositories import (
    backfill_listing_presence_signals,
    mark_listings_disappeared,
)
from vinted_bot.db.session import session_scope
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def reconcile_stale_listings(
    *,
    max_age_hours: float = 48.0,
    limit: int = 5000,
    backfill: bool = True,
) -> int:
    """Marque inactives les annonces actives non revues depuis max_age_hours.

    Proxy de liquidité : une disparition n'est pas forcément une vente
    (retrait vendeur possible), mais reste le signal disponible via catalog.

    Horloge de présence : COALESCE(last_seen_at, scraped_at, updated_at)
    pour ne pas bloquer le reconcile quand last_seen_at est null.
    """
    cutoff = _utcnow() - timedelta(hours=max(1.0, max_age_hours))
    with session_scope() as session:
        if backfill:
            stats = backfill_listing_presence_signals(session, limit=limit)
            if any(stats.values()):
                log.info("listing_presence_backfill", **stats)
        presence = func.coalesce(
            Listing.last_seen_at, Listing.scraped_at, Listing.updated_at
        )
        stmt = (
            select(Listing.id)
            .where(Listing.is_active.is_(True))
            .where(presence.is_not(None))
            .where(presence < cutoff)
            .order_by(presence.asc())
            .limit(max(1, limit))
        )
        ids = list(session.scalars(stmt).all())
        marked = mark_listings_disappeared(
            session, ids, source_query="reconcile:stale"
        )
    log.info(
        "listings_reconciled",
        marked=marked,
        max_age_hours=max_age_hours,
        cutoff=cutoff.isoformat(),
    )
    return marked
