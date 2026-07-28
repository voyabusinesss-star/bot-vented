"""Analyse continue des tendances + rapport quotidien Discord."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import select

from vinted_bot.config import get_settings
from vinted_bot.db.models import TrendSnapshot
from vinted_bot.db.repositories import (
    get_checkpoint,
    list_trend_history,
    list_trend_snapshots_for_date,
    set_checkpoint,
    upsert_trend_snapshot,
)
from vinted_bot.db.session import session_scope
from vinted_bot.notify.discord import DiscordNotifier, sanitize_discord_channel_id
from vinted_bot.services.market_embeds import (
    build_daily_trend_card_embed,
    build_daily_trends_board_embed,
)
from vinted_bot.services.market_trends import MarketTrend, detect_market_trends
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

PARIS = ZoneInfo("Europe/Paris")
CHECKPOINT_PREFIX = "market:daily:tendances:"
ANALYZE_LIMIT = 40
ANALYZE_MIN_SCORE = 32.0
DEFAULT_DAILY_MAX = 8
DEFAULT_REPORT_HOUR = 8


@dataclass(slots=True, frozen=True)
class HistoryPoint:
    """Point d'historique détaché de la session SQLAlchemy."""

    strength: float
    price_median_7d: float | None


@dataclass(slots=True, frozen=True)
class DailyTrendItem:
    trend: MarketTrend
    rank: int
    medal: str
    headline: str
    ai_narrative: str
    event_badges: tuple[str, ...]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today_paris() -> date:
    return datetime.now(PARIS).date()


def _checkpoint_key(day: date) -> str:
    return f"{CHECKPOINT_PREFIX}{day.isoformat()}"


def trend_to_payload(trend: MarketTrend) -> dict[str, Any]:
    niches = list(trend.associated_niches or trend.related or ())
    return {
        "title": trend.title,
        "ai_analysis": list(trend.ai_analysis),
        "associated_niches": niches,
        "related": niches,
        "badges": list(trend.badges),
        "sample_titles": list(trend.sample_titles),
        "opportunity": trend.opportunity,
        "why_it_matters": trend.why_it_matters,
        "recommendation_detail": trend.recommendation_detail,
        "confidence_label": trend.confidence_label,
        "median_ttl_7d_hours": trend.median_ttl_7d_hours,
        "median_ttl_30d_hours": trend.median_ttl_30d_hours,
    }


def persist_trend_analysis(
    *,
    limit: int = ANALYZE_LIMIT,
    min_score: float = ANALYZE_MIN_SCORE,
    snapshot_day: date | None = None,
) -> list[MarketTrend]:
    """Phase 1 — analyse continue : détecte et historise sans publier."""
    day = snapshot_day or _today_paris()
    trends = detect_market_trends(
        limit=limit,
        min_score=min_score,
        include_weak=True,
    )
    if not trends:
        log.info("trend_analysis_empty", day=day.isoformat())
        return []

    with session_scope() as session:
        for trend in trends:
            upsert_trend_snapshot(
                session,
                snapshot_date=day,
                entity_type=trend.entity_type,
                entity_key=trend.entity_key,
                display_name=trend.display_name,
                strength=trend.strength,
                direction=trend.direction,
                lifecycle=trend.lifecycle,
                importance=trend.importance,
                recommendation=trend.recommendation,
                count_1d=trend.count_1d,
                count_7d=trend.count_7d,
                count_30d=trend.count_30d,
                count_90d=trend.count_90d,
                active_count=trend.active_count,
                disappeared_7d=trend.disappeared_7d,
                price_median_7d=trend.price_median_7d,
                price_median_30d=trend.price_median_30d,
                price_change_pct=trend.price_change_pct,
                rotation_change_pct=trend.rotation_change_pct,
                stock_change_pct=trend.stock_change_pct,
                popularity_change_pct=trend.popularity_change_pct,
                continuation_pct=trend.continuation_pct,
                gauge_growth=trend.gauge_growth,
                gauge_rentabilite=trend.gauge_rentabilite,
                gauge_rarity=trend.gauge_rarity,
                gauge_demand=trend.gauge_demand,
                gauge_saturation=trend.gauge_saturation,
                triggers=[
                    {"code": t.code, "label": t.label, "detail": t.detail}
                    for t in trend.triggers
                ],
                payload=trend_to_payload(trend),
            )
    log.info(
        "trend_analysis_persisted",
        day=day.isoformat(),
        count=len(trends),
        top_score=trends[0].strength if trends else 0,
    )
    return trends


def _event_badges(trend: MarketTrend) -> tuple[str, ...]:
    codes = {t.code for t in trend.triggers}
    badges: list[str] = []
    if "new_entity" in codes or trend.lifecycle == "emergence":
        badges.append("🆕 Nouvelle niche")
    if "volume_surge" in codes and (trend.popularity_change_pct or 0) >= 80:
        badges.append("🔥 Explosion soudaine")
    elif "volume_surge" in codes:
        badges.append("📈 Hausse de demande")
    if "volume_drop" in codes:
        badges.append("📉 Baisse d'intérêt")
    if "rotation_fast" in codes or "velocity" in codes:
        badges.append("🚀 Accélération rapide")
    if "scarcity" in codes:
        badges.append("💎 Rareté importante")
    if "saturation" in codes or trend.lifecycle == "saturation":
        badges.append("⚠️ Risque de saturation")
    if trend.lifecycle in {"decline", "peak", "growth"} and len(badges) < 3:
        badges.append("🔄 Changement de cycle")
    return tuple(dict.fromkeys(badges))[:4]


def _headline_for(trend: MarketTrend, events: Sequence[str]) -> str:
    """Titre mouvement — jamais un produit précis."""
    if getattr(trend, "title", None):
        return trend.title
    if any("Explosion" in e for e in events):
        return f"Explosion — {trend.display_name}"
    if any("Nouvelle niche" in e for e in events):
        return f"Émergence — {trend.display_name}"
    if any("Hausse" in e for e in events):
        return f"Hausse — {trend.display_name}"
    if trend.lifecycle == "decline":
        return f"Reflux — {trend.display_name}"
    if trend.lifecycle == "saturation":
        return f"Saturation — {trend.display_name}"
    return f"Mouvement — {trend.display_name}"


def build_ai_narrative(
    trend: MarketTrend,
    history: Sequence[HistoryPoint],
) -> str:
    days_tracked = max(1, len(history))
    if history:
        first = history[0]
        score_delta = trend.strength - float(first.strength or 0)
        price0 = first.price_median_7d
        price1 = trend.price_median_7d
        if price0 and price1 and price0 > 0:
            price_bit = (
                f"Le prix médian est passé d'environ {price0:.0f} € à {price1:.0f} €. "
            )
        else:
            price_bit = ""
        stock_bit = ""
        if trend.stock_change_pct is not None:
            if trend.stock_change_pct < -15:
                stock_bit = (
                    "Le stock disponible diminue, ce qui suggère une demande "
                    "supérieure à l'offre. "
                )
            elif trend.stock_change_pct > 25:
                stock_bit = (
                    "L'offre augmente rapidement — surveiller un risque de saturation. "
                )
        progress = (
            f"progression importante depuis {days_tracked} jour(s)"
            if score_delta >= 5 or days_tracked >= 3
            else f"signal suivi sur {days_tracked} jour(s)"
        )
        durable = {
            "emergence": (
                "La niche est encore tôt dans son cycle : potentiel d'anticipation, "
                "mais la durabilité reste à confirmer."
            ),
            "growth": (
                "La dynamique semble encore exploitable si les prix d'entrée "
                "restent sous la médiane."
            ),
            "peak": (
                "Nous approchons d'un pic : intéressante en sélectif, "
                "risque de saturation à court terme."
            ),
            "decline": (
                "L'intérêt faiblit — opportunité surtout en achat discount."
            ),
            "saturation": (
                "Le marché paraît saturé : faible potentiel sauf sous-cote extrême."
            ),
        }.get(trend.lifecycle, "À surveiller sur les prochaines fenêtres 7j/30j.")
        return (
            f"Cette tendance montre une {progress}. "
            f"{price_bit}{stock_bit}"
            f"Score actuel {trend.strength:.0f}/100 "
            f"(continuation estimée ~{trend.continuation_pct:.0f}%). "
            f"{durable}"
        )

    why = " ; ".join(trend.ai_analysis[:3]) if trend.ai_analysis else trend.why_it_matters
    return (
        f"{trend.display_name} présente un signal marché notable "
        f"(score {trend.strength:.0f}/100). {why} "
        f"Recommandation : {trend.recommendation_detail}"
    )


def _priority_score(trend: MarketTrend) -> float:
    """Priorisation rapport : marge, vitesse, rareté, confiance, durée, concurrence."""
    sat_penalty = trend.gauge_saturation * 0.35
    return (
        trend.strength * 0.35
        + trend.gauge_rentabilite * 0.15
        + trend.gauge_growth * 0.15
        + trend.gauge_rarity * 0.12
        + trend.gauge_demand * 0.12
        + trend.continuation_pct * 0.11
        - sat_penalty
    )


def _snapshot_to_trend(row: TrendSnapshot) -> MarketTrend:
    from vinted_bot.services.market_trends import TrendTrigger

    payload = row.payload or {}
    triggers_raw = row.triggers or []
    triggers = tuple(
        TrendTrigger(
            code=str(t.get("code", "")),
            label=str(t.get("label", "")),
            detail=str(t.get("detail", "")),
        )
        for t in triggers_raw
        if isinstance(t, dict)
    )
    niches = tuple(
        (payload or {}).get("associated_niches")
        or (payload or {}).get("related")
        or ()
    )
    title = str((payload or {}).get("title") or row.display_name)
    return MarketTrend(
        entity_type=row.entity_type,
        entity_key=row.entity_key,
        display_name=row.display_name,
        title=title,
        strength=float(row.strength or 0),
        direction=str(row.direction or "up"),
        lifecycle=str(row.lifecycle or "growth"),
        importance=str(row.importance or "growing"),
        triggers=triggers,
        count_1d=int(row.count_1d or 0),
        count_7d=int(row.count_7d or 0),
        count_30d=int(row.count_30d or 0),
        count_90d=int(row.count_90d or 0),
        active_count=int(row.active_count or 0),
        price_median_7d=row.price_median_7d,
        price_median_30d=row.price_median_30d,
        price_change_pct=row.price_change_pct,
        disappeared_7d=int(row.disappeared_7d or 0),
        median_ttl_7d_hours=(payload or {}).get("median_ttl_7d_hours"),
        median_ttl_30d_hours=(payload or {}).get("median_ttl_30d_hours"),
        rotation_change_pct=row.rotation_change_pct,
        stock_change_pct=row.stock_change_pct,
        popularity_change_pct=row.popularity_change_pct,
        gauge_growth=float(row.gauge_growth or 0),
        gauge_rentabilite=float(row.gauge_rentabilite or 0),
        gauge_rarity=float(row.gauge_rarity or 0),
        gauge_demand=float(row.gauge_demand or 0),
        gauge_saturation=float(row.gauge_saturation or 0),
        continuation_pct=float(row.continuation_pct or 0),
        confidence_label=str((payload or {}).get("confidence_label") or "Confiance moyenne"),
        sample_titles=tuple((payload or {}).get("sample_titles") or ()),
        ai_analysis=tuple((payload or {}).get("ai_analysis") or ()),
        associated_niches=niches,
        related=niches,
        opportunity=str((payload or {}).get("opportunity") or ""),
        why_it_matters=str((payload or {}).get("why_it_matters") or ""),
        recommendation=str(row.recommendation or "watch"),
        recommendation_detail=str(
            (payload or {}).get("recommendation_detail") or ""
        ),
        badges=tuple((payload or {}).get("badges") or ()),
    )


def select_daily_top_trends(
    *,
    day: date | None = None,
    max_items: int | None = None,
    min_score: float | None = None,
) -> list[DailyTrendItem]:
    """Phase 2 — sélection qualitative pour le rapport du jour."""
    settings = get_settings()
    report_day = day or _today_paris()
    limit = max_items or int(
        getattr(settings, "daily_trends_max", DEFAULT_DAILY_MAX) or DEFAULT_DAILY_MAX
    )
    floor = min_score
    if floor is None:
        from vinted_bot.services.market_trends import load_trend_config

        floor = load_trend_config()[0].min_publish_score

    with session_scope() as session:
        rows = list_trend_snapshots_for_date(
            session, report_day, min_strength=floor, limit=60
        )
        histories: dict[tuple[str, str], list[HistoryPoint]] = {}
        if not rows:
            # Fallback : dernière analyse live si DB vide ce jour
            live = detect_market_trends(limit=limit, min_score=floor)
            candidates = live
        else:
            candidates = []
            for row in rows:
                if row.importance == "weak":
                    continue
                # Uniquement mouvements macro (pas marques / modèles / pépites)
                if row.entity_type not in {"macro", "topic"}:
                    continue
                if row.entity_type == "topic" and row.entity_key in {
                    "sac",
                    "vintage_obj",
                    "chaussure",
                    "bijou",
                    "deco",
                }:
                    continue
                trend = _snapshot_to_trend(row)
                candidates.append(trend)
                since = report_day - timedelta(days=90)
                # Copier les scalaires avant fermeture de session
                histories[(row.entity_type, row.entity_key)] = [
                    HistoryPoint(
                        strength=float(h.strength or 0),
                        price_median_7d=h.price_median_7d,
                    )
                    for h in list_trend_history(
                        session,
                        entity_type=row.entity_type,
                        entity_key=row.entity_key,
                        since=since,
                    )
                ]

    # Déduplication par display_name normalisé
    seen_names: set[str] = set()
    unique: list[MarketTrend] = []
    for trend in sorted(candidates, key=_priority_score, reverse=True):
        key = trend.display_name.strip().lower()
        if key in seen_names:
            continue
        if len(trend.display_name.strip()) < 3:
            continue
        seen_names.add(key)
        unique.append(trend)
        if len(unique) >= limit:
            break

    medals = ("🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
    items: list[DailyTrendItem] = []
    for i, trend in enumerate(unique, 1):
        events = _event_badges(trend)
        hist = histories.get((trend.entity_type, trend.entity_key), [])
        items.append(
            DailyTrendItem(
                trend=trend,
                rank=i,
                medal=medals[i - 1] if i <= len(medals) else f"{i}.",
                headline=_headline_for(trend, events),
                ai_narrative=build_ai_narrative(trend, hist),
                event_badges=events,
            )
        )
    return items


def daily_report_already_posted(day: date | None = None) -> bool:
    report_day = day or _today_paris()
    with session_scope() as session:
        data = get_checkpoint(session, _checkpoint_key(report_day))
    return bool(data and data.get("posted_at"))


def should_post_daily_report(*, force: bool = False) -> bool:
    if force:
        return True
    if daily_report_already_posted():
        return False
    settings = get_settings()
    hour = int(
        getattr(settings, "daily_trends_report_hour", DEFAULT_REPORT_HOUR)
        or DEFAULT_REPORT_HOUR
    )
    now_local = datetime.now(PARIS)
    return now_local.hour >= hour


def _daily_channel(settings: Any) -> str:
    # Pas de fallback vers #détecteur-niches (salon études uniquement).
    for name in (
        "discord_channel_tendances_du_jour",
        "discord_channel_tendances",
    ):
        value = sanitize_discord_channel_id(getattr(settings, name, "") or "")
        if value:
            return value
    return ""


def post_daily_tendances_report(*, force: bool = False) -> int:
    """Publie le rapport quotidien dans #tendances-du-jour (1×/jour)."""
    settings = get_settings()
    if not settings.discord_bot_token.strip():
        log.warning("daily_trends_discord_missing_token")
        return 0
    if not should_post_daily_report(force=force):
        log.info("daily_trends_report_skipped", reason="not_due_or_already_posted")
        return 0

    channel = _daily_channel(settings)
    if not channel:
        log.warning("daily_trends_channel_missing")
        return 0

    day = _today_paris()
    # S'assurer que l'analyse du jour existe (évite un double detect si cycle vient de tourner)
    if latest_trend_entity_count(day=day) == 0:
        persist_trend_analysis(snapshot_day=day)
    items = select_daily_top_trends(day=day)
    if not items:
        log.info("daily_trends_report_empty", day=day.isoformat())
        with session_scope() as session:
            set_checkpoint(
                session,
                _checkpoint_key(day),
                {
                    "posted_at": _utcnow().isoformat(),
                    "count": 0,
                    "empty": True,
                },
            )
        return 0

    posted = 0
    board = build_daily_trends_board_embed(items, day=day)
    with DiscordNotifier(settings) as notifier:
        notifier.post_embed(channel, board)
        posted += 1
        time.sleep(max(0.25, settings.discord_post_delay_seconds))
        for item in items:
            notifier.post_embed(channel, build_daily_trend_card_embed(item))
            posted += 1
            time.sleep(max(0.25, settings.discord_post_delay_seconds))

    with session_scope() as session:
        set_checkpoint(
            session,
            _checkpoint_key(day),
            {
                "posted_at": _utcnow().isoformat(),
                "count": len(items),
                "channel_id": channel,
                "top": [i.trend.display_name for i in items[:5]],
            },
        )
    log.info(
        "daily_trends_report_posted",
        day=day.isoformat(),
        items=len(items),
        messages=posted,
        channel=channel,
    )
    return posted


def latest_trend_entity_count(*, day: date | None = None) -> int:
    report_day = day or _today_paris()
    with session_scope() as session:
        rows = session.scalars(
            select(TrendSnapshot.id).where(TrendSnapshot.snapshot_date == report_day)
        ).all()
    return len(list(rows))
