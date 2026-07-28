"""Moteur d'intelligence de marché : agrégats, score, classements Discord."""

from __future__ import annotations

import math
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence  # noqa: F401 — Sequence used by niches publish

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from vinted_bot.config import get_settings, sanitize_discord_channel_id
from vinted_bot.db.models import Listing, NicheSnapshot
from vinted_bot.db.repositories import (
    get_checkpoint,
    set_checkpoint,
    upsert_niche_snapshot,
)
from vinted_bot.db.session import session_scope
from vinted_bot.notify.discord import DiscordNotifier, normalize_brand
from vinted_bot.services.listing_reconcile import reconcile_stale_listings
from vinted_bot.services.daily_trends_report import persist_trend_analysis
from vinted_bot.services.multi_angle import aggregate_engagement
from vinted_bot.services.market_embeds import (
    NicheCard,
    WindowPoint,
    build_leaderboard_embed,
    build_niche_dashboard_embed,
    build_stats_dashboard_embed,
)
from vinted_bot.services.opportunity_engine import (
    MAX_OPPORTUNITIES_POSTED,
    MIN_NICHE_LISTINGS,
    PUBLISH_MIN_SCORE,
    Opportunity,
    board_hash_unchanged,
    build_opportunity_embed,
    build_pepite_from_opportunity_embed,
    filter_publishable_opportunities,
    is_granular_niche,
    mark_board_hash,
    mark_opportunities_posted,
    select_opportunities,
    _is_recently_posted_key,
    _load_recently_posted_keys,
)
from vinted_bot.services.market_entities import (
    brand_saturation_penalty,
    ensure_market_catalog,
    backfill_listing_entities,
    is_analyzable_listing,
    is_market_domain,
)
from vinted_bot.utils.logging import get_logger

# Limite cartes premium par salon (anti-flood Discord)
PREMIUM_CARDS_PER_CHANNEL = 5

log = get_logger(__name__)

WINDOWS = ("1d", "7d", "30d", "90d")
WINDOW_DAYS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}
VINTED_FEE_PCT = 6.0
MIN_MARGIN_NET_PCT = 35.0
MIN_LISTINGS = 5
MIN_DISAPPEARED_FOR_VELOCITY = 2
DEFAULT_LOOP_INTERVAL = 900.0
RANKING_COOLDOWN_HOURS = 6.0
PEPITE_MIN_SCORE = 55.0
PEPITE_MIN_MARGIN_PCT = 35.0
PEPITE_MAX_PER_CYCLE = 8
PEPITE_COOLDOWN_HOURS = 72.0


@dataclass(slots=True, frozen=True)
class NicheMetrics:
    niche_key: str
    window: str
    brand_slug: str
    model_slug: str | None
    category_slug: str | None
    keyword_flags: str
    listing_count: int
    new_listings: int
    disappeared_count: int
    unique_sellers: int
    price_min_cents: int | None
    price_max_cents: int | None
    price_mean_cents: int | None
    price_median_cents: int | None
    price_p25_cents: int | None
    median_ttl_days: float | None
    margin_proxy_pct: float | None
    score: float | None
    metrics: dict[str, Any]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _percentile(sorted_values: Sequence[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    idx = max(0, min(len(sorted_values) - 1, int(len(sorted_values) * pct) - 1))
    return float(sorted_values[idx])


def niche_key_for_listing(listing: Listing) -> str:
    brand = normalize_brand(listing.brand) or "inconnu"
    model = listing.model_slug or ""
    category = listing.category_slug or ""
    kws = listing.keyword_slugs if isinstance(listing.keyword_slugs, list) else []
    flags = "+".join(str(k) for k in kws[:3])
    return f"{brand}|{model}|{category}|{flags}"


def compute_niche_score(
    *,
    margin_proxy_pct: float | None,
    median_ttl_days: float | None,
    new_listings: int,
    unique_sellers: int,
    brand_slug: str | None,
    model_slug: str | None,
    volume_7d: int,
    volume_30d: int,
    disappeared_count: int,
    listing_count: int = 0,
) -> float | None:
    """Score opportunité /100 — autonome, pas un classement des plus vendus.

    Favorise marge + liquidité + niches sous-exploitées (volume moyen).
    Les gros volumes tendance sont pénalisés (marché saturé / déjà chassé).
    """
    if margin_proxy_pct is None:
        return None
    margin_clipped = max(0.0, min(150.0, float(margin_proxy_pct)))
    net_margin = margin_clipped - VINTED_FEE_PCT
    if net_margin < MIN_MARGIN_NET_PCT:
        return None
    volume_signal = max(new_listings, volume_30d, listing_count)
    if volume_signal < MIN_LISTINGS:
        return None

    # Marge : 35% net → 0 pts, 100%+ net → 35 pts
    margin_pts = max(0.0, min(35.0, ((net_margin - MIN_MARGIN_NET_PCT) / 65.0) * 35.0))

    if median_ttl_days is not None and disappeared_count >= MIN_DISAPPEARED_FOR_VELOCITY:
        ttl = max(1.0, min(30.0, float(median_ttl_days)))
        velocity_pts = max(0.0, min(25.0, (30.0 / ttl) * (25.0 / 30.0)))
    elif disappeared_count >= MIN_DISAPPEARED_FOR_VELOCITY:
        velocity_pts = 12.0
    else:
        # Pas de signal liquidité → faible contribution (évite un faux milieu)
        velocity_pts = 3.0

    # Sweet spot sous-exploité : assez d'échantillons, pas une tendance saturée
    n = float(listing_count or volume_signal)
    if 5 <= n <= 25:
        exploit_pts = 22.0
    elif 26 <= n <= 40:
        exploit_pts = 14.0
    elif 41 <= n <= 70:
        exploit_pts = 6.0
    else:
        # Mega-volume = niche déjà chassée / trop concurrentielle
        exploit_pts = 1.0

    # Concentration vendeurs — léger bonus, pas un chase volume
    seller_ratio = float(unique_sellers) / max(1.0, n)
    if 0.15 <= seller_ratio <= 0.55:
        structure_pts = 10.0
    elif seller_ratio < 0.15:
        structure_pts = 4.0  # trop concentré
    else:
        structure_pts = 6.0

    # Croissance douce uniquement sur petits/moyens clusters (pas chase trend)
    if volume_30d > 0:
        trend_ratio = volume_7d / max(1.0, volume_30d / 4.0)
    else:
        trend_ratio = 1.0
    if n <= 40 and trend_ratio >= 1.15:
        demand_pts = max(0.0, min(8.0, ((trend_ratio - 1.0) / 1.5) * 8.0))
    else:
        demand_pts = 0.0

    total = margin_pts + velocity_pts + exploit_pts + structure_pts + demand_pts
    saturation = brand_saturation_penalty(brand_slug, model_slug)
    total = total / max(1.0, saturation)
    return round(max(0.0, min(100.0, total)), 1)


def _bucket_listings(
    listings: Iterable[Listing],
) -> dict[str, list[Listing]]:
    """Agrège toutes les annonces analysables — multi-catégories, pas mode-only."""
    buckets: dict[str, list[Listing]] = defaultdict(list)
    for listing in listings:
        if not is_analyzable_listing(listing.title, brand=listing.brand):
            continue
        buckets[niche_key_for_listing(listing)].append(listing)
    return buckets


def _metrics_for_bucket(
    niche_key: str,
    window: str,
    listings: Sequence[Listing],
    *,
    cutoff: datetime,
    volume_7d: int,
    volume_30d: int,
) -> NicheMetrics | None:
    parts = niche_key.split("|")
    brand = parts[0] if parts else "inconnu"
    model = parts[1] if len(parts) > 1 and parts[1] else None
    category = parts[2] if len(parts) > 2 and parts[2] else None
    flags = parts[3] if len(parts) > 3 else ""

    # Fenêtre stricte : présence ou disparition dans la fenêtre (pas toutes les actives).
    in_window: list[Listing] = []
    for listing in listings:
        first = _as_aware(listing.first_seen_at) or _as_aware(listing.scraped_at)
        last = (
            _as_aware(listing.last_seen_at)
            or _as_aware(listing.scraped_at)
            or first
        )
        disappeared_at = _as_aware(listing.disappeared_at)
        if last and last >= cutoff:
            in_window.append(listing)
        elif first and first >= cutoff:
            in_window.append(listing)
        elif disappeared_at and disappeared_at >= cutoff:
            in_window.append(listing)

    if len(in_window) < 1:
        return None

    prices = sorted(
        float(l.price_cents)
        for l in in_window
        if l.price_cents is not None and l.price_cents > 0
    )
    active_prices = sorted(
        float(l.price_cents)
        for l in in_window
        if l.is_active and l.price_cents is not None and l.price_cents > 0
    )
    price_pool = active_prices or prices

    disappeared = [
        l
        for l in in_window
        if (not l.is_active)
        and _as_aware(l.disappeared_at)
        and _as_aware(l.disappeared_at) >= cutoff
    ]
    ttl_days: list[float] = []
    for listing in disappeared:
        start = _as_aware(listing.published_at) or _as_aware(listing.first_seen_at)
        end = _as_aware(listing.disappeared_at)
        if start and end and end >= start:
            ttl_days.append((end - start).total_seconds() / 86400.0)

    median_cents = int(statistics.median(price_pool)) if price_pool else None
    p25 = _percentile(price_pool, 0.25)
    p25_cents = int(p25) if p25 is not None else None
    p75 = _percentile(price_pool, 0.75)
    p75_cents = int(p75) if p75 is not None else None
    margin_pct = None
    if median_cents and p25_cents and p25_cents > 0:
        # Marge proxy : écart médiane vs P25 / P25 (achat bas → revente médiane)
        margin_pct = ((median_cents - p25_cents) / p25_cents) * 100.0

    sellers = {l.seller_id for l in in_window if l.seller_id is not None}
    new_listings = 0
    for listing in in_window:
        born = _as_aware(listing.first_seen_at) or _as_aware(listing.scraped_at)
        if born is not None and born >= cutoff:
            new_listings += 1
    median_ttl = statistics.median(ttl_days) if ttl_days else None
    engagement = aggregate_engagement(in_window)
    active_count = sum(1 for l in in_window if l.is_active)

    score = compute_niche_score(
        margin_proxy_pct=margin_pct,
        median_ttl_days=median_ttl,
        new_listings=new_listings,
        unique_sellers=max(1, len(sellers)),
        brand_slug=brand,
        model_slug=model,
        volume_7d=volume_7d,
        volume_30d=volume_30d,
        disappeared_count=len(disappeared),
        listing_count=len(in_window),
    )

    return NicheMetrics(
        niche_key=niche_key,
        window=window,
        brand_slug=brand,
        model_slug=model,
        category_slug=category,
        keyword_flags=flags,
        listing_count=len(in_window),
        new_listings=new_listings,
        disappeared_count=len(disappeared),
        unique_sellers=len(sellers),
        price_min_cents=int(min(price_pool)) if price_pool else None,
        price_max_cents=int(max(price_pool)) if price_pool else None,
        price_mean_cents=int(statistics.mean(price_pool)) if price_pool else None,
        price_median_cents=median_cents,
        price_p25_cents=p25_cents,
        median_ttl_days=round(median_ttl, 2) if median_ttl is not None else None,
        margin_proxy_pct=round(margin_pct, 2) if margin_pct is not None else None,
        score=score,
        metrics={
            "score_max": 100,
            "price_p75_cents": p75_cents,
            "net_margin_pct": (
                round(margin_pct - VINTED_FEE_PCT, 2) if margin_pct is not None else None
            ),
            "velocity_proxy": (
                round(1.0 / max(1.0, min(30.0, median_ttl)), 4)
                if median_ttl is not None
                else None
            ),
            "volume_7d": volume_7d,
            "volume_30d": volume_30d,
            "liquidity_observed": len(disappeared) >= 2 or median_ttl is not None,
            "active_count": active_count,
            "favourite_sum": round(engagement["favourite_sum"], 1),
            "favourite_avg": round(engagement["favourite_avg"], 2),
            "view_sum": round(engagement["view_sum"], 1),
            "view_avg": round(engagement["view_avg"], 2),
        },
    )


def compute_all_snapshots(*, lookback_days: int = 90) -> list[NicheMetrics]:
    """Calcule et persiste les snapshots pour 1d/7d/30d/90d."""
    now = _utcnow()
    since = now - timedelta(days=lookback_days)
    with session_scope() as session:
        ensure_market_catalog(session)
        backfill_listing_entities(session, limit=4000)
        listings = list(
            session.scalars(
                select(Listing)
                .where(
                    (Listing.first_seen_at >= since)
                    | (Listing.last_seen_at >= since)
                    | (Listing.is_active.is_(True))
                )
                .limit(20000)
            ).all()
        )
        buckets = _bucket_listings(listings)

        # Volumes par niche pour trend
        vol7: dict[str, int] = defaultdict(int)
        vol30: dict[str, int] = defaultdict(int)
        c7 = now - timedelta(days=7)
        c30 = now - timedelta(days=30)
        for key, group in buckets.items():
            for listing in group:
                seen = _as_aware(listing.first_seen_at) or _as_aware(listing.scraped_at)
                if seen and seen >= c7:
                    vol7[key] += 1
                if seen and seen >= c30:
                    vol30[key] += 1

        results: list[NicheMetrics] = []
        for window in WINDOWS:
            days = WINDOW_DAYS[window]
            cutoff = now - timedelta(days=days)
            for key, group in buckets.items():
                metrics = _metrics_for_bucket(
                    key,
                    window,
                    group,
                    cutoff=cutoff,
                    volume_7d=vol7.get(key, 0),
                    volume_30d=vol30.get(key, 0),
                )
                if metrics is None or metrics.listing_count < MIN_NICHE_LISTINGS:
                    continue
                upsert_niche_snapshot(
                    session,
                    niche_key=metrics.niche_key,
                    window=metrics.window,
                    brand_slug=metrics.brand_slug,
                    model_slug=metrics.model_slug,
                    category_slug=metrics.category_slug,
                    keyword_flags=metrics.keyword_flags or None,
                    listing_count=metrics.listing_count,
                    new_listings=metrics.new_listings,
                    disappeared_count=metrics.disappeared_count,
                    unique_sellers=metrics.unique_sellers,
                    price_min_cents=metrics.price_min_cents,
                    price_max_cents=metrics.price_max_cents,
                    price_mean_cents=metrics.price_mean_cents,
                    price_median_cents=metrics.price_median_cents,
                    price_p25_cents=metrics.price_p25_cents,
                    median_ttl_days=metrics.median_ttl_days,
                    margin_proxy_pct=metrics.margin_proxy_pct,
                    score=metrics.score,
                    metrics=metrics.metrics,
                )
                results.append(metrics)

    results.sort(key=lambda m: (m.score or 0.0), reverse=True)
    log.info("market_snapshots_computed", count=len(results))
    return results


def _label_niche(m: NicheMetrics | NicheSnapshot) -> str:
    brand = (getattr(m, "brand_slug", None) or "?").replace("_", " ").title()
    model = getattr(m, "model_slug", None)
    category = getattr(m, "category_slug", None)
    parts = [brand]
    if model:
        parts.append(str(model).replace("_", " ").title())
    if category:
        parts.append(str(category).replace("_", " ").title())
    flags = getattr(m, "keyword_flags", None)
    if flags:
        parts.append(str(flags))
    return " · ".join(parts)


def _detach_snapshot(row: NicheSnapshot) -> NicheSnapshot:
    """Copie les attributs pour usage hors session (évite DetachedInstanceError)."""
    return NicheSnapshot(
        niche_key=row.niche_key,
        window=row.window,
        brand_slug=row.brand_slug,
        model_slug=row.model_slug,
        category_slug=row.category_slug,
        keyword_flags=row.keyword_flags,
        listing_count=row.listing_count,
        new_listings=row.new_listings,
        disappeared_count=row.disappeared_count,
        unique_sellers=row.unique_sellers,
        price_min_cents=row.price_min_cents,
        price_max_cents=row.price_max_cents,
        price_mean_cents=row.price_mean_cents,
        price_median_cents=row.price_median_cents,
        price_p25_cents=row.price_p25_cents,
        median_ttl_days=row.median_ttl_days,
        margin_proxy_pct=row.margin_proxy_pct,
        score=row.score,
        metrics=dict(row.metrics) if isinstance(row.metrics, dict) else row.metrics,
        computed_at=row.computed_at,
    )


def top_snapshots(
    *,
    window: str = "30d",
    limit: int = 15,
    scored_only: bool = True,
) -> list[NicheSnapshot]:
    with session_scope() as session:
        stmt = select(NicheSnapshot).where(NicheSnapshot.window == window)
        if scored_only:
            stmt = stmt.where(NicheSnapshot.score.is_not(None))
        stmt = stmt.order_by(NicheSnapshot.score.desc().nullslast()).limit(limit)
        return [_detach_snapshot(row) for row in session.scalars(stmt).all()]


def discovery_candidate_snapshots(
    *,
    window: str = "30d",
    limit: int = 200,
) -> list[NicheSnapshot]:
    """Candidats pour le détecteur — autonome, pas seulement les plus vendus.

    Mixe niches sous-exploitées (volume moyen + marge) et scores opportunité,
    sans dépendre d'un classement « tendances / bestsellers ».
    """
    with session_scope() as session:
        stmt = select(NicheSnapshot).where(
            NicheSnapshot.window == window,
            NicheSnapshot.score.is_not(None),
            NicheSnapshot.listing_count >= MIN_NICHE_LISTINGS,
        )
        rows = [_detach_snapshot(r) for r in session.scalars(stmt).all()]

    def _discovery_rank(s: NicheSnapshot) -> float:
        n = int(s.listing_count or 0)
        margin = float(s.margin_proxy_pct or 0.0)
        base = float(s.score or 0.0)
        # Boost volume moyen (inexploité) — pénalité mega-volume
        if MIN_NICHE_LISTINGS <= n <= 28:
            vol_adj = 18.0
        elif 29 <= n <= 45:
            vol_adj = 6.0
        elif n > 70:
            vol_adj = -22.0
        else:
            vol_adj = -6.0
        return base + vol_adj + min(18.0, margin * 0.12)

    # Priorité 1 : clusters moyens à forte marge (affaires inexploitées)
    mid = [
        r
        for r in rows
        if MIN_NICHE_LISTINGS <= int(r.listing_count or 0) <= 40
    ]
    mid.sort(
        key=lambda r: (
            float(r.margin_proxy_pct or 0.0),
            float(r.score or 0.0),
        ),
        reverse=True,
    )
    # Priorité 2 : ranking découverte global
    ranked = sorted(rows, key=_discovery_rank, reverse=True)

    seen: set[str] = set()
    out: list[NicheSnapshot] = []
    half = max(40, limit // 2)
    for pool in (mid[:half], ranked):
        for snap in pool:
            if snap.niche_key in seen:
                continue
            seen.add(snap.niche_key)
            out.append(snap)
            if len(out) >= limit:
                return out
    return out


def top_by_dimension(
    *,
    dimension: str,
    window: str = "30d",
    limit: int = 10,
) -> list[tuple[str, float, int]]:
    """Agrège score moyen par marque / modèle / keyword."""
    with session_scope() as session:
        rows = [
            _detach_snapshot(r)
            for r in session.scalars(
                select(NicheSnapshot).where(NicheSnapshot.window == window)
            ).all()
        ]
    agg: dict[str, list[float]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if dimension == "brand":
            key = row.brand_slug or ""
        elif dimension == "model":
            key = row.model_slug or ""
        elif dimension == "keyword":
            key = (row.keyword_flags or "").split("+")[0] if row.keyword_flags else ""
        elif dimension == "category":
            key = row.category_slug or ""
        else:
            key = ""
        if not key:
            continue
        counts[key] += row.listing_count or 0
        if row.score is not None:
            agg[key].append(float(row.score))
    ranked = [
        (key, statistics.mean(scores), counts[key])
        for key, scores in agg.items()
        if scores
    ]
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:limit]


def emerging_niches(*, limit: int = 10) -> list[NicheSnapshot]:
    """Niches dont le score 7d dépasse nettement le 30d."""
    with session_scope() as session:
        rows7 = {
            r.niche_key: _detach_snapshot(r)
            for r in session.scalars(
                select(NicheSnapshot).where(NicheSnapshot.window == "7d")
            ).all()
        }
        rows30 = {
            r.niche_key: _detach_snapshot(r)
            for r in session.scalars(
                select(NicheSnapshot).where(NicheSnapshot.window == "30d")
            ).all()
        }
    emerging: list[tuple[float, NicheSnapshot]] = []
    for key, r7 in rows7.items():
        r30 = rows30.get(key)
        if r7.score is None:
            continue
        base = r30.score if r30 and r30.score is not None else 0.0
        boost = r7.score - base
        if r7.new_listings >= 3 and (base == 0 or r7.score >= base * 1.25):
            emerging.append((boost, r7))
    emerging.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in emerging[:limit]]


def build_rankings_embed(
    *,
    title: str,
    lines: Sequence[str],
    footer: str = "Market intel · score /100 · liquidité = disparitions (proxy)",
    color: int = 0x3498DB,
) -> dict[str, Any]:
    return build_leaderboard_embed(
        title=title, lines=lines, color=color, footer=footer
    )


def load_niche_windows(niche_key: str) -> tuple[WindowPoint, ...]:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(NicheSnapshot).where(NicheSnapshot.niche_key == niche_key)
            ).all()
        )
        points = [
            WindowPoint(
                window=r.window,
                listing_count=r.listing_count or 0,
                price_median_cents=r.price_median_cents,
                median_ttl_days=r.median_ttl_days,
                score=r.score,
                new_listings=r.new_listings or 0,
                margin_proxy_pct=r.margin_proxy_pct,
                disappeared_count=r.disappeared_count or 0,
            )
            for r in rows
        ]
    return tuple(points)


def snapshot_to_card(
    snap: NicheSnapshot,
    *,
    rank: int | None = None,
    windows: tuple[WindowPoint, ...] | None = None,
) -> NicheCard:
    metrics = snap.metrics if isinstance(snap.metrics, dict) else {}
    win = windows if windows is not None else load_niche_windows(snap.niche_key)
    return NicheCard(
        niche_key=snap.niche_key,
        brand_slug=snap.brand_slug or "inconnu",
        model_slug=snap.model_slug,
        category_slug=snap.category_slug,
        keyword_flags=snap.keyword_flags or "",
        score=float(snap.score or 0.0),
        listing_count=snap.listing_count or 0,
        new_listings=snap.new_listings or 0,
        disappeared_count=snap.disappeared_count or 0,
        unique_sellers=snap.unique_sellers or 0,
        price_min_cents=snap.price_min_cents,
        price_max_cents=snap.price_max_cents,
        price_mean_cents=snap.price_mean_cents,
        price_median_cents=snap.price_median_cents,
        price_p25_cents=snap.price_p25_cents,
        median_ttl_days=snap.median_ttl_days,
        margin_proxy_pct=snap.margin_proxy_pct,
        volume_7d=int(metrics.get("volume_7d") or snap.new_listings or 0),
        volume_30d=int(metrics.get("volume_30d") or snap.listing_count or 0),
        rank=rank,
        windows=win,
        sample_size=snap.listing_count or 0,
    )


def brand_or_model_card(
    *,
    slug: str,
    score: float,
    volume: int,
    dimension: str,
    rank: int,
) -> NicheCard:
    """Carte synthétique marque/modèle à partir des agrégats."""
    best: NicheSnapshot | None = None
    with session_scope() as session:
        col = (
            NicheSnapshot.brand_slug
            if dimension == "brand"
            else NicheSnapshot.model_slug
        )
        rows = list(
            session.scalars(
                select(NicheSnapshot)
                .where(NicheSnapshot.window == "30d")
                .where(col == slug)
                .order_by(NicheSnapshot.score.desc().nullslast())
                .limit(1)
            ).all()
        )
        if rows:
            best = _detach_snapshot(rows[0])
    if best is not None:
        card = snapshot_to_card(best, rank=rank)
        return NicheCard(
            niche_key=card.niche_key,
            brand_slug=slug if dimension == "brand" else card.brand_slug,
            model_slug=slug if dimension == "model" else card.model_slug,
            category_slug=card.category_slug,
            keyword_flags=card.keyword_flags,
            score=score,
            listing_count=max(volume, card.listing_count),
            new_listings=card.new_listings,
            disappeared_count=card.disappeared_count,
            unique_sellers=card.unique_sellers,
            price_min_cents=card.price_min_cents,
            price_max_cents=card.price_max_cents,
            price_mean_cents=card.price_mean_cents,
            price_median_cents=card.price_median_cents,
            price_p25_cents=card.price_p25_cents,
            median_ttl_days=card.median_ttl_days,
            margin_proxy_pct=card.margin_proxy_pct,
            volume_7d=card.volume_7d,
            volume_30d=max(volume, card.volume_30d),
            rank=rank,
            windows=card.windows,
            sample_size=max(volume, card.sample_size),
        )
    return NicheCard(
        niche_key=f"{slug}|",
        brand_slug=slug if dimension == "brand" else "inconnu",
        model_slug=slug if dimension == "model" else None,
        category_slug=None,
        keyword_flags="",
        score=score,
        listing_count=volume,
        new_listings=volume,
        disappeared_count=0,
        unique_sellers=max(1, volume // 3),
        price_min_cents=None,
        price_max_cents=None,
        price_mean_cents=None,
        price_median_cents=None,
        price_p25_cents=None,
        median_ttl_days=None,
        margin_proxy_pct=None,
        volume_7d=max(1, volume // 4),
        volume_30d=volume,
        rank=rank,
        windows=(),
        sample_size=volume,
    )


def _chunk_lines(lines: Sequence[str], *, size: int = 25) -> list[list[str]]:
    chunks: list[list[str]] = []
    buf: list[str] = []
    for line in lines:
        buf.append(line)
        if len(buf) >= size:
            chunks.append(buf)
            buf = []
    if buf:
        chunks.append(buf)
    return chunks or [[]]


def _format_eur_cents(cents: int | None) -> str:
    if cents is None:
        return "?"
    return f"{cents / 100:.0f}€"


def _format_score(score: float | None) -> str:
    if score is None:
        return "?"
    return f"{score:.0f}/100"


def _pretty_slug(value: str | None) -> str:
    if not value:
        return "?"
    return value.replace("_", " ").title()


def _ranking_checkpoint_key(kind: str) -> str:
    return f"market:rank:{kind}"


def _was_recently_posted(kind: str, *, cooldown_hours: float) -> bool:
    with session_scope() as session:
        data = get_checkpoint(session, _ranking_checkpoint_key(kind))
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


def _mark_ranking_posted(kind: str) -> None:
    with session_scope() as session:
        set_checkpoint(
            session,
            _ranking_checkpoint_key(kind),
            {"posted_at": _utcnow().isoformat()},
        )


def _channel(settings: Any, *field_names: str) -> str:
    """Premier canal configuré parmi les champs (fallback inclus)."""
    for name in field_names:
        value = sanitize_discord_channel_id(getattr(settings, name, "") or "")
        if value:
            return value
    return ""


def compute_dashboard_stats() -> dict[str, Any]:
    """Résumé global pour #statistiques."""
    now = _utcnow()
    day_ago = now - timedelta(days=1)
    with session_scope() as session:
        listings = list(session.scalars(select(Listing).limit(25000)).all())
        snaps30 = [
            _detach_snapshot(r)
            for r in session.scalars(
                select(NicheSnapshot).where(NicheSnapshot.window == "30d")
            ).all()
        ]
        brands = {
            normalize_brand(l.brand)
            for l in listings
            if l.brand and is_market_domain(l.title, brand=l.brand)
        }
        brands.discard("")
        models = {l.model_slug for l in listings if l.model_slug}
        new_today = sum(
            1
            for l in listings
            if (_as_aware(l.first_seen_at) or _as_aware(l.scraped_at) or now)
            >= day_ago
        )
        active = sum(1 for l in listings if l.is_active)
        listings_total = len(listings)
        niches_count = len(snaps30)
        niches_scored = sum(1 for s in snaps30 if s.score is not None)
    top_brands = top_by_dimension(dimension="brand", window="30d", limit=5)
    top_cats = top_by_dimension(dimension="category", window="30d", limit=5)
    emerging = emerging_niches(limit=5)
    return {
        "listings_total": listings_total,
        "listings_active": active,
        "listings_new_24h": new_today,
        "brands": len(brands),
        "models": len(models),
        "niches": niches_count,
        "niches_scored": niches_scored,
        "top_brands": top_brands,
        "top_categories": top_cats,
        "emerging": emerging,
    }


def build_stats_embed(stats: dict[str, Any]) -> dict[str, Any]:
    return build_stats_dashboard_embed(stats)


@dataclass(slots=True, frozen=True)
class PepiteAlert:
    vinted_id: int
    title: str
    url: str
    price_cents: int
    brand: str | None
    model_slug: str | None
    category_slug: str | None
    size: str | None
    photo_url: str | None
    niche_score: float
    pepite_score: float
    resell_cents: int
    margin_proxy_pct: float
    niche_label: str


def _listing_photo_url(listing: Listing) -> str | None:
    for photo in listing.photos or []:
        if photo.url:
            return photo.url
    raw = listing.raw_json if isinstance(listing.raw_json, dict) else {}
    photo = raw.get("photo") or {}
    if isinstance(photo, dict) and photo.get("url"):
        return str(photo["url"])
    return None


def find_pepites(*, limit: int = PEPITE_MAX_PER_CYCLE) -> list[PepiteAlert]:
    """Annonces actives sous P25 d'une niche granulaire scorée → pépites."""
    snaps = {
        s.niche_key: s
        for s in top_snapshots(window="30d", limit=200, scored_only=True)
        if s.score is not None
        and s.score >= PEPITE_MIN_SCORE
        and (s.margin_proxy_pct or 0) >= PEPITE_MIN_MARGIN_PCT
        and s.price_p25_cents
        and s.price_median_cents
        and is_granular_niche(s)
    }
    if not snaps:
        return []
    candidates: list[PepiteAlert] = []
    with session_scope() as session:
        active = list(
            session.scalars(
                select(Listing)
                .options(selectinload(Listing.photos))
                .where(Listing.is_active.is_(True))
                .where(Listing.price_cents.is_not(None))
                .order_by(Listing.last_seen_at.desc().nullslast())
                .limit(4000)
            )
            .unique()
            .all()
        )
        for listing in active:
            if not is_market_domain(listing.title, brand=listing.brand):
                continue
            key = niche_key_for_listing(listing)
            snap = snaps.get(key)
            if snap is None or listing.price_cents is None:
                continue
            if listing.price_cents > int(snap.price_p25_cents or 0):
                continue
            median = float(snap.price_median_cents or 0)
            if median <= 0:
                continue
            est_margin = ((median - listing.price_cents) / listing.price_cents) * 100.0
            if est_margin < PEPITE_MIN_MARGIN_PCT:
                continue
            pepite_score = min(
                100.0,
                float(snap.score or 0) * 0.65 + min(35.0, est_margin * 0.25),
            )
            candidates.append(
                PepiteAlert(
                    vinted_id=listing.vinted_id,
                    title=listing.title or "",
                    url=listing.url,
                    price_cents=int(listing.price_cents),
                    brand=listing.brand,
                    model_slug=listing.model_slug or snap.model_slug,
                    category_slug=listing.category_slug or snap.category_slug,
                    size=listing.size,
                    photo_url=_listing_photo_url(listing),
                    niche_score=float(snap.score or 0),
                    pepite_score=round(pepite_score, 1),
                    resell_cents=int(snap.price_median_cents or 0),
                    margin_proxy_pct=round(est_margin, 1),
                    niche_label=_label_niche(snap),
                )
            )
    candidates.sort(key=lambda x: x.pepite_score, reverse=True)
    return candidates[:limit]


def build_pepite_embed(alert: PepiteAlert) -> dict[str, Any]:
    """Pépite liée explicitement à sa niche source."""
    return build_pepite_from_opportunity_embed(
        title=alert.title,
        url=alert.url,
        price_cents=alert.price_cents,
        resell_cents=alert.resell_cents,
        margin_pct=alert.margin_proxy_pct,
        photo_url=alert.photo_url,
        niche_name=alert.niche_label,
        niche_score=alert.niche_score,
        size=alert.size,
    )


def post_pepites_to_discord(*, force: bool = False) -> int:
    settings = get_settings()
    # Jamais de fallback vers #détecteur-niches : ce salon = études, pas annonces.
    channel = _channel(settings, "discord_channel_pepites")
    if not channel or not settings.discord_bot_token.strip():
        if not channel:
            log.warning(
                "pepites_channel_missing",
                hint="Définis DISCORD_CHANNEL_PEPITES (pas de fallback niches)",
            )
        return 0
    pepites = find_pepites()
    posted = 0
    with DiscordNotifier(settings) as notifier:
        for alert in pepites:
            kind = f"pepite:{alert.vinted_id}"
            if not force and _was_recently_posted(
                kind, cooldown_hours=PEPITE_COOLDOWN_HOURS
            ):
                continue
            try:
                notifier.post_embed(channel, build_pepite_embed(alert))
            except RuntimeError as exc:
                msg = str(exc)
                if "10003" in msg or "Unknown Channel" in msg:
                    log.warning(
                        "discord_pepites_channel_unknown",
                        channel_tail=channel[-4:],
                        error=msg[:160],
                    )
                    break
                raise
            _mark_ranking_posted(kind)
            posted += 1
            time.sleep(max(0.2, settings.discord_post_delay_seconds))
    log.info("market_pepites_posted", count=posted)
    return posted


def post_rankings_to_discord(*, force: bool = False) -> int:
    """Publie des cartes dashboard premium + classements compacts."""
    settings = get_settings()
    if not settings.discord_bot_token.strip():
        log.warning("market_intel_discord_missing")
        return 0

    # Pas de fallback vers #niches pour les salons secondaires (évite le flood).
    ch_marques = _channel(settings, "discord_channel_marques")
    ch_modeles = _channel(settings, "discord_channel_modeles")
    ch_classements = _channel(settings, "discord_channel_classements")
    ch_stats = _channel(settings, "discord_channel_statistiques")
    ch_niches = _channel(settings, "discord_channel_niches")
    if not any([ch_marques, ch_modeles, ch_classements, ch_stats, ch_niches]):
        log.warning("market_intel_discord_channels_missing")
        return 0

    jobs: list[tuple[str, str, dict[str, Any]]] = []

    def _add_cards(
        kind: str,
        channel: str,
        cards: Sequence[NicheCard],
        *,
        card_kind: str = "niche",
    ) -> None:
        for i, card in enumerate(cards):
            jobs.append(
                (
                    kind if i == 0 else f"{kind}:{i}",
                    channel,
                    build_niche_dashboard_embed(card, kind=card_kind),
                )
            )

    # #marques — top cartes + leaderboard
    if ch_marques and (
        force
        or not _was_recently_posted("ch:marques", cooldown_hours=RANKING_COOLDOWN_HOURS)
    ):
        brands = top_by_dimension(dimension="brand", window="30d", limit=40)
        cards = [
            brand_or_model_card(
                slug=n, score=s, volume=c, dimension="brand", rank=i
            )
            for i, (n, s, c) in enumerate(brands[:PREMIUM_CARDS_PER_CHANNEL], 1)
        ]
        _add_cards("ch:marques", ch_marques, cards)
        lines = [
            f"**{i}.** {_pretty_slug(n)} — `{s:.0f}/100` · vol {c}"
            for i, (n, s, c) in enumerate(brands, 1)
        ]
        jobs.append(
            (
                "ch:marques:board",
                ch_marques,
                build_rankings_embed(
                    title="📋 Classement marques (30j)",
                    lines=lines[:40],
                    color=0x1ABC9C,
                ),
            )
        )

    # #modeles
    if ch_modeles and (
        force
        or not _was_recently_posted("ch:modeles", cooldown_hours=RANKING_COOLDOWN_HOURS)
    ):
        models = top_by_dimension(dimension="model", window="30d", limit=40)
        cards = [
            brand_or_model_card(
                slug=n, score=s, volume=c, dimension="model", rank=i
            )
            for i, (n, s, c) in enumerate(models[:PREMIUM_CARDS_PER_CHANNEL], 1)
        ]
        _add_cards("ch:modeles", ch_modeles, cards)
        lines = [
            f"**{i}.** {_pretty_slug(n)} — `{s:.0f}/100` · vol {c}"
            for i, (n, s, c) in enumerate(models, 1)
        ]
        jobs.append(
            (
                "ch:modeles:board",
                ch_modeles,
                build_rankings_embed(
                    title="📋 Classement modèles (30j)",
                    lines=lines[:40],
                    color=0xE67E22,
                ),
            )
        )

    # Tendances Discord retirées — le cœur produit est #détecteur-niches

    # #classements — top niches dashboard + boards
    if ch_classements and (
        force
        or not _was_recently_posted(
            "ch:classements", cooldown_hours=RANKING_COOLDOWN_HOURS
        )
    ):
        combos = [
            row
            for row in top_snapshots(window="30d", limit=80)
            if is_granular_niche(row)
        ][:40]
        cards = [
            snapshot_to_card(row, rank=i)
            for i, row in enumerate(combos[:PREMIUM_CARDS_PER_CHANNEL], 1)
        ]
        _add_cards("ch:classements", ch_classements, cards)
        jobs.append(
            (
                "ch:classements:board",
                ch_classements,
                build_rankings_embed(
                    title="🏆 Top niches (30j)",
                    lines=[
                        f"**{i}.** {_label_niche(row)} — `{_format_score(row.score)}` · "
                        f"marge ~{row.margin_proxy_pct or 0:.0f}%"
                        for i, row in enumerate(combos, 1)
                    ][:40],
                    color=0xF39C12,
                ),
            )
        )
        keywords = top_by_dimension(dimension="keyword", window="30d", limit=25)
        jobs.append(
            (
                "ch:classements:kw",
                ch_classements,
                build_rankings_embed(
                    title="🏷️ Top mots-clés",
                    lines=[
                        f"**{i}.** {_pretty_slug(n)} — `{s:.0f}/100`"
                        for i, (n, s, _) in enumerate(keywords, 1)
                    ],
                    color=0xF39C12,
                ),
            )
        )

    # #statistiques
    if ch_stats and (
        force
        or not _was_recently_posted("ch:stats", cooldown_hours=RANKING_COOLDOWN_HOURS)
    ):
        jobs.append(
            ("ch:stats", ch_stats, build_stats_embed(compute_dashboard_stats()))
        )

    # #détecteur-niches — publication déléguée (filtre opportunités intéressantes)
    niches_posted = 0
    if ch_niches:
        niches_posted = post_interesting_niches_to_discord(force=force)

    posted = 0
    if not jobs:
        pepites_posted = post_pepites_to_discord(force=force)
        return niches_posted + pepites_posted

    failed_channels: set[str] = set()
    with DiscordNotifier(settings) as notifier:
        for kind, channel, embed in jobs:
            if channel in failed_channels:
                continue
            try:
                notifier.post_embed(channel, embed)
                posted += 1
            except RuntimeError as exc:
                msg = str(exc)
                if "10003" in msg or "Unknown Channel" in msg:
                    log.warning(
                        "discord_unknown_channel_skipped",
                        kind=kind,
                        channel_tail=channel[-4:],
                        error=msg[:160],
                    )
                    failed_channels.add(channel)
                    continue
                raise
            time.sleep(max(0.25, settings.discord_post_delay_seconds))
    for key in (
        "ch:marques",
        "ch:modeles",
        "ch:classements",
        "ch:stats",
    ):
        if any(k == key or k.startswith(key + ":") for k, _, _ in jobs):
            # Ne marque le cooldown que si au moins un post de ce kind a réussi
            if any(
                (k == key or k.startswith(key + ":"))
                and ch not in failed_channels
                for k, ch, _ in jobs
            ):
                _mark_ranking_posted(key)

    pepites_posted = post_pepites_to_discord(force=force)
    log.info(
        "market_rankings_posted",
        count=posted,
        niches=niches_posted,
        pepites=pepites_posted,
        failed_channels=len(failed_channels),
    )
    return posted + niches_posted + pepites_posted


def post_interesting_niches_to_discord(
    *,
    opportunities: Sequence[Opportunity] | None = None,
    force: bool = False,
    prefer_keys: set[str] | None = None,
) -> int:
    """Publie dans #détecteur-niches uniquement des opportunités intéressantes.

    Règles :
    - score ≥ PUBLISH_MIN_SCORE
    - nouvelle niche / nouveau signal (prefer_keys) OU force OU board changé hors cooldown
    - silence total si rien d'intéressant
    """
    settings = get_settings()
    channel = _channel(settings, "discord_channel_niches")
    if not channel or not settings.discord_bot_token.strip():
        return 0

    raw = (
        list(opportunities)
        if opportunities is not None
        else select_opportunities(limit=MAX_OPPORTUNITIES_POSTED)
    )
    publishable = filter_publishable_opportunities(
        raw, min_score=PUBLISH_MIN_SCORE
    )
    if not publishable:
        log.info(
            "niches_hub_skipped_no_interesting",
            scanned=len(raw),
            min_score=PUBLISH_MIN_SCORE,
        )
        return 0

    prefer = prefer_keys or set()
    posted_map = _load_recently_posted_keys()
    from vinted_bot.services.opportunity_engine import (
        _is_recently_posted_name,
        _load_recently_posted_names,
    )

    posted_names = _load_recently_posted_names()
    # Exclusion dure : jamais la même analyse / le même nom récemment
    fresh = [
        op
        for op in publishable
        if not _is_recently_posted_key(op.niche_key, posted_map)
        and not _is_recently_posted_name(op.name, posted_names)
    ]
    if prefer:
        signaled = [op for op in fresh if op.niche_key in prefer]
        others = [op for op in fresh if op.niche_key not in prefer]
        fresh = signaled + others

    if force:
        to_post = (fresh or publishable)[:MAX_OPPORTUNITIES_POSTED]
    elif fresh:
        to_post = fresh[:MAX_OPPORTUNITIES_POSTED]
    else:
        log.info(
            "niches_hub_skipped_nothing_new",
            publishable=len(publishable),
            fresh=0,
            prefer=len(prefer),
        )
        return 0

    jobs: list[tuple[str, dict[str, Any]]] = [
        (f"ch:niches-hub:{i}" if i else "ch:niches-hub", build_opportunity_embed(op))
        for i, op in enumerate(to_post)
    ]

    posted = 0
    failed = False
    with DiscordNotifier(settings) as notifier:
        for kind, embed in jobs:
            try:
                notifier.post_embed(channel, embed)
                posted += 1
            except RuntimeError as exc:
                msg = str(exc)
                if "10003" in msg or "Unknown Channel" in msg:
                    log.warning(
                        "discord_unknown_channel_skipped",
                        kind=kind,
                        channel_tail=channel[-4:],
                        error=msg[:160],
                    )
                    failed = True
                    break
                raise
            time.sleep(max(0.25, settings.discord_post_delay_seconds))

    if posted and not failed:
        _mark_ranking_posted("ch:niches-hub")
        mark_opportunities_posted(to_post)
        mark_board_hash(to_post)
        try:
            from vinted_bot.db.repositories import record_opportunity_history
            from vinted_bot.db.session import session_scope

            with session_scope() as session:
                for op in to_post:
                    record_opportunity_history(
                        session,
                        niche_key=op.niche_key,
                        name=op.name,
                        score=op.score,
                        lifecycle=op.lifecycle,
                        confidence=op.confidence,
                        niche_type=op.niche_type,
                        brand_slug=op.brand_slug,
                        model_slug=op.model_slug,
                        category_slug=op.category_slug,
                        signals=op.signals,
                        payload={"posted_channel": "niches"},
                        posted=True,
                    )
        except Exception as exc:  # noqa: BLE001
            log.warning("opportunity_history_posted_failed", error=str(exc)[:160])
    log.info(
        "niches_interesting_posted",
        count=posted,
        niches=len(to_post),
        min_score=PUBLISH_MIN_SCORE,
    )
    return posted


def run_market_intel_cycle(
    *,
    post_discord: bool = True,
    reconcile: bool = True,
    stale_hours: float = 48.0,
    force_discord: bool = False,
) -> dict[str, Any]:
    marked = 0
    if reconcile:
        marked = reconcile_stale_listings(max_age_hours=stale_hours)
    snapshots = compute_all_snapshots()
    scored = sum(1 for s in snapshots if s.score is not None)

    # Historique macro (DB) — plus de publication salon tendances
    try:
        trend_rows = persist_trend_analysis()
        trends_saved = len(trend_rows)
    except Exception as exc:  # noqa: BLE001
        log.exception("trend_analysis_error", error=str(exc))
        trends_saved = 0

    posted = 0
    if post_discord:
        posted = post_rankings_to_discord(force=force_discord)

    summary = {
        "reconciled": marked,
        "snapshots": len(snapshots),
        "scored": scored,
        "trends_saved": trends_saved,
        "discord_posted": posted,
        "daily_trends_posted": 0,
    }
    log.info("market_intel_cycle_done", **summary)
    return summary


def run_market_intel_loop(
    *,
    interval_seconds: float | None = None,
    post_discord: bool = True,
) -> None:
    interval = interval_seconds if interval_seconds is not None else DEFAULT_LOOP_INTERVAL
    log.info("market_intel_loop_start", interval_seconds=interval)
    while True:
        try:
            run_market_intel_cycle(post_discord=post_discord)
        except Exception as exc:  # noqa: BLE001
            log.exception("market_intel_cycle_error", error=str(exc))
        time.sleep(max(60.0, interval))
