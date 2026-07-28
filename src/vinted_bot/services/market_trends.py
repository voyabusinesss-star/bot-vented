"""Radar tendances multi-marché — signaux, scoring /100, cycle de vie."""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

import yaml
from sqlalchemy import select

from vinted_bot.db.models import Listing
from vinted_bot.db.session import session_scope
from vinted_bot.notify.discord import normalize_brand
from vinted_bot.services.market_entities import load_keyword_defs
from vinted_bot.services.market_trends_filter import (
    extract_commercial_phrases,
    is_commercially_relevant_phrase,
    is_generic_token,
    normalize_token,
)
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"

# Axes de mouvement (Niveau 1) vs objets (contexte)
_MOVEMENT_KINDS = frozenset(
    {"style", "era", "movement", "tech", "material_premium", "resale_keyword", "licence"}
)
_OBJECT_KINDS = frozenset({"object", "brand_object", "product"})
_CONTEXT_KINDS = _MOVEMENT_KINDS  # style/ère/licence… pour qualifier un objet


@dataclass(slots=True, frozen=True)
class TopicDef:
    slug: str
    display_name: str
    kind: str
    aliases: tuple[str, ...]
    standalone_ok: bool = False


@dataclass(slots=True)
class TrendThresholds:
    min_samples_short: int = 4
    min_samples_long: int = 6
    volume_surge_ratio: float = 1.8
    volume_drop_ratio: float = 0.45
    price_up_pct: float = 12.0
    price_down_pct: float = 12.0
    scarcity_active_max: int = 12
    scarcity_new_7d_min: int = 4
    new_entity_long_max: int = 8
    new_entity_short_min: int = 5
    saturation_active_min: int = 40
    min_token_len: int = 4
    min_token_listings: int = 4
    min_publish_score: float = 48.0
    max_trends_posted: int = 8
    lookback_days: int = 90
    exclude_topic_kinds: tuple[str, ...] = ("color",)
    vague_alone_slugs: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class TrendTrigger:
    code: str
    label: str
    detail: str


@dataclass(slots=True, frozen=True)
class MarketTrend:
    entity_type: str  # macro | topic (mouvement de marché uniquement)
    entity_key: str
    display_name: str
    title: str  # ex. "Explosion du style Y2K"
    strength: float  # score IA /100
    direction: str  # up | down | emerging | scarce | saturated
    lifecycle: str  # emergence | growth | peak | decline | saturation
    importance: str  # critical | high | growing | weak
    triggers: tuple[TrendTrigger, ...]
    count_1d: int
    count_7d: int
    count_30d: int
    count_90d: int
    active_count: int
    price_median_7d: float | None
    price_median_30d: float | None
    price_change_pct: float | None
    disappeared_7d: int
    median_ttl_7d_hours: float | None
    median_ttl_30d_hours: float | None
    rotation_change_pct: float | None
    stock_change_pct: float | None
    popularity_change_pct: float | None
    gauge_growth: float
    gauge_rentabilite: float
    gauge_rarity: float
    gauge_demand: float
    gauge_saturation: float
    continuation_pct: float
    confidence_label: str
    sample_titles: tuple[str, ...]
    ai_analysis: tuple[str, ...]
    associated_niches: tuple[str, ...]  # Niveau 2 — niches qui expliquent le mouvement
    related: tuple[str, ...]  # alias / compat
    opportunity: str
    why_it_matters: str
    recommendation: str  # buy | watch | wait | avoid
    recommendation_detail: str
    badges: tuple[str, ...]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _normalize_text(value: str | None) -> str:
    return normalize_token(value)


def _alias_pattern(alias: str) -> re.Pattern[str]:
    cleaned = _normalize_text(alias).strip()
    if not cleaned:
        return re.compile(r"(?!)")
    escaped = re.escape(cleaned).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


@lru_cache(maxsize=1)
def load_trend_config() -> tuple[TrendThresholds, tuple[TopicDef, ...]]:
    path = CONFIG_DIR / "market_trends.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}
    exclude_kinds = tuple(
        str(x) for x in (defaults.get("exclude_topic_kinds") or ["color"])
    )
    vague_alone = tuple(
        str(x).strip().lower()
        for x in (defaults.get("vague_alone_slugs") or [])
        if str(x).strip()
    )
    thr = TrendThresholds(
        min_samples_short=int(defaults.get("min_samples_short", 4)),
        min_samples_long=int(defaults.get("min_samples_long", 6)),
        volume_surge_ratio=float(defaults.get("volume_surge_ratio", 1.8)),
        volume_drop_ratio=float(defaults.get("volume_drop_ratio", 0.45)),
        price_up_pct=float(defaults.get("price_up_pct", 12.0)),
        price_down_pct=float(defaults.get("price_down_pct", 12.0)),
        scarcity_active_max=int(defaults.get("scarcity_active_max", 12)),
        scarcity_new_7d_min=int(defaults.get("scarcity_new_7d_min", 4)),
        new_entity_long_max=int(defaults.get("new_entity_long_max", 8)),
        new_entity_short_min=int(defaults.get("new_entity_short_min", 5)),
        saturation_active_min=int(defaults.get("saturation_active_min", 40)),
        min_token_len=int(defaults.get("min_token_len", 4)),
        min_token_listings=int(defaults.get("min_token_listings", 4)),
        min_publish_score=float(defaults.get("min_publish_score", 48.0)),
        max_trends_posted=int(defaults.get("max_trends_posted", 8)),
        lookback_days=int(defaults.get("lookback_days", 90)),
        exclude_topic_kinds=exclude_kinds,
        vague_alone_slugs=vague_alone,
    )
    topics: list[TopicDef] = []
    for row in raw.get("topics") or []:
        if not isinstance(row, dict) or not row.get("slug"):
            continue
        kind = str(row.get("kind") or "topic")
        if kind in thr.exclude_topic_kinds:
            continue
        aliases = [str(a) for a in (row.get("aliases") or []) if str(a).strip()]
        topics.append(
            TopicDef(
                slug=str(row["slug"]).strip().lower(),
                display_name=str(row.get("display_name") or row["slug"]),
                kind=kind,
                aliases=tuple(aliases),
                standalone_ok=bool(row.get("standalone_ok", False)),
            )
        )
    # Keywords catalogue : uniquement styles / matières (pas couleurs)
    for kw in load_keyword_defs():
        kind = kw.kind or "keyword"
        if kind in thr.exclude_topic_kinds or kind == "color":
            continue
        if kind not in _MOVEMENT_KINDS and kind not in {"keyword", "style"}:
            continue
        topics.append(
            TopicDef(
                slug=f"kw_{kw.slug}",
                display_name=kw.display_name,
                kind=kind if kind in _MOVEMENT_KINDS else "style",
                aliases=kw.aliases,
                standalone_ok=kind in {"style", "resale_keyword", "material_premium"},
            )
        )
    topics.sort(key=lambda t: max((len(a) for a in t.aliases), default=0), reverse=True)
    return thr, tuple(topics)


def match_topics(title: str | None, topics: Sequence[TopicDef]) -> list[TopicDef]:
    hay = f" {_normalize_text(title)} "
    found: list[TopicDef] = []
    seen: set[str] = set()
    for topic in topics:
        for alias in topic.aliases:
            if _alias_pattern(alias).search(hay):
                if topic.slug not in seen:
                    found.append(topic)
                    seen.add(topic.slug)
                break
    return found


def extract_free_tokens(
    title: str | None,
    *,
    min_len: int = 4,
) -> list[str]:
    """Compat tests — délègue aux phrases commerciales (bigrams+)."""
    phrases = extract_commercial_phrases(title)
    out: list[str] = []
    for etype, key, _display in phrases:
        if etype == "phrase":
            out.append(key)
        elif etype in {"topic", "model", "keyword"}:
            out.append(key)
    norm = _normalize_text(title)
    parts = [p for p in norm.split() if len(p) >= min_len and not is_generic_token(p)]
    for i in range(len(parts) - 1):
        big = f"{parts[i]}_{parts[i+1]}"
        if is_commercially_relevant_phrase(f"{parts[i]} {parts[i+1]}") and big not in out:
            out.append(big)
    return out[:12]


@dataclass(slots=True, frozen=True)
class _LiteListing:
    title: str
    brand: str | None
    price_cents: int | None
    size: str | None
    is_active: bool
    first_seen_at: datetime | None
    published_at: datetime | None
    scraped_at: datetime | None
    last_seen_at: datetime | None
    disappeared_at: datetime | None


@dataclass
class _EntityBucket:
    entity_type: str
    entity_key: str
    display_name: str
    listings: list[_LiteListing] = field(default_factory=list)


def _seen_at(listing: _LiteListing) -> datetime | None:
    return (
        _as_aware(listing.first_seen_at)
        or _as_aware(listing.published_at)
        or _as_aware(listing.scraped_at)
        or _as_aware(listing.last_seen_at)
    )


def _median_price(listings: Iterable[_LiteListing]) -> float | None:
    prices = [
        l.price_cents / 100.0
        for l in listings
        if l.price_cents is not None and l.price_cents > 0
    ]
    if not prices:
        return None
    return float(statistics.median(prices))


def _median_ttl_hours(
    listings: Iterable[_LiteListing],
    *,
    now: datetime,
) -> float | None:
    hours: list[float] = []
    for listing in listings:
        start = _seen_at(listing)
        end = _as_aware(listing.disappeared_at)
        if end is None and not listing.is_active:
            end = _as_aware(listing.last_seen_at) or now
        if start is None or end is None or end <= start:
            continue
        hours.append((end - start).total_seconds() / 3600.0)
    if len(hours) < 2:
        return None
    return float(statistics.median(hours))


def _pct_delta(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None or baseline == 0:
        return None
    return ((current - baseline) / abs(baseline)) * 100.0


def _lifecycle_for(
    *,
    direction: str,
    codes: set[str],
    volume_ratio: float,
    active: int,
    n7: int,
    n90: int,
    gauge_saturation: float,
) -> str:
    if "saturation" in codes or gauge_saturation >= 70:
        return "saturation"
    if direction == "down" or "volume_drop" in codes:
        return "decline"
    if direction == "emerging" or "new_entity" in codes:
        return "emergence"
    if gauge_saturation >= 55 and volume_ratio >= 1.5 and active >= max(20, n7):
        return "peak"
    if volume_ratio >= 2.2 and n90 >= 15 and active >= 25:
        return "peak"
    if direction in {"up", "scarce"} or "volume_surge" in codes or "price_up" in codes:
        return "growth"
    return "growth"


def _importance_for(score: float, lifecycle: str) -> str:
    if score >= 85 or (lifecycle == "emergence" and score >= 70):
        return "critical"
    if score >= 70:
        return "high"
    if score >= 55 or lifecycle in {"emergence", "growth"}:
        return "growing"
    return "weak"


def _recommendation_for(
    *,
    score: float,
    lifecycle: str,
    gauge_saturation: float,
    gauge_demand: float,
    codes: set[str],
) -> tuple[str, str]:
    if lifecycle == "saturation" or (gauge_saturation >= 75 and "price_down" in codes):
        return (
            "avoid",
            "Trop d'offre / saturation — potentiel de marge faible.",
        )
    if lifecycle == "decline" and score < 60:
        return (
            "wait",
            "Demande en retrait — attendre un plancher ou un rebond confirmé.",
        )
    if (
        score >= 70
        and lifecycle in {"emergence", "growth"}
        and gauge_saturation <= 45
        and gauge_demand >= 55
    ):
        return (
            "buy",
            "Signaux favorables — rechercher / acheter sous la médiane maintenant.",
        )
    if score >= 55 and lifecycle in {"emergence", "growth", "peak"}:
        if lifecycle == "peak" or gauge_saturation >= 55:
            return (
                "wait",
                "Pic ou saturation naissante — être sélectif, éviter les prix hauts.",
            )
        return (
            "watch",
            "Mouvement réel — surveiller les entrées sous médiane.",
        )
    if "scarcity" in codes and score >= 50:
        return (
            "buy",
            "Stock bas + rotation — sniper les bonnes pièces rapidement.",
        )
    return (
        "watch",
        "Signal à confirmer — élargir la veille sans forcer l'achat.",
    )


def _ai_analysis(
    *,
    display_name: str,
    triggers: Sequence[TrendTrigger],
    lifecycle: str,
    price_change_pct: float | None,
    rotation_change_pct: float | None,
    stock_change_pct: float | None,
    popularity_change_pct: float | None,
    continuation_pct: float,
) -> tuple[str, ...]:
    lines: list[str] = []
    codes = {t.code for t in triggers}
    if "volume_surge" in codes or (popularity_change_pct or 0) > 20:
        lines.append("Hausse de la demande estimée (volume d'annonces similaires)")
    if "velocity" in codes or (rotation_change_pct is not None and rotation_change_pct < -20):
        lines.append("Rotation accélérée — les annonces disparaissent plus vite")
    if "scarcity" in codes or (stock_change_pct is not None and stock_change_pct < -20):
        lines.append("Baisse du stock disponible / rareté relative")
    if "price_up" in codes or (price_change_pct or 0) >= 8:
        lines.append("Augmentation des prix médians sur la fenêtre courte")
    if "price_down" in codes:
        lines.append("Correction des prix — possible fenêtre d'achat ou saturation")
    if "saturation" in codes:
        lines.append("Offre excessive — concurrence élevée, marge sous pression")
    if "new_entity" in codes:
        lines.append("Niche émergente avec peu d'historique — avantage au first-mover")
    if not lines:
        lines.append("Écart inhabituel détecté vs le rythme de référence 30j")

    if lifecycle == "emergence":
        lines.append("Durabilité encore incertaine mais potentiel d'anticipation élevé")
    elif lifecycle == "growth":
        lines.append("Phase de croissance — la tendance semble encore exploitable")
    elif lifecycle == "peak":
        lines.append("Pic de popularité — risque de saturation à court terme")
    elif lifecycle == "decline":
        lines.append("Déclin en cours — opportunité surtout en achat discount")
    else:
        lines.append("Saturation — peu d'intérêt sauf sous-cote extrême")

    lines.append(
        f"Probabilité de continuation estimée ~{continuation_pct:.0f}% "
        f"pour « {display_name} »"
    )
    return tuple(lines[:6])


def _badges_for(
    *,
    lifecycle: str,
    importance: str,
    codes: set[str],
    score: float,
) -> tuple[str, ...]:
    badges: list[str] = []
    life_badge = {
        "emergence": "🌱 Émergence",
        "growth": "📈 Croissance",
        "peak": "🔥 Pic",
        "decline": "📉 Déclin",
        "saturation": "💀 Saturation",
    }.get(lifecycle)
    if life_badge:
        badges.append(life_badge)
    imp_badge = {
        "critical": "🚨 Critique",
        "high": "🔥 Très importante",
        "growing": "📈 En croissance",
        "weak": "👀 Signal faible",
    }.get(importance)
    if imp_badge:
        badges.append(imp_badge)
    if "scarcity" in codes:
        badges.append("💎 Rareté")
    if "new_entity" in codes:
        badges.append("🆕 Niche")
    if score >= 85:
        badges.append("⚡ Alpha")
    return tuple(badges[:5])


def evaluate_entity(
    bucket: _EntityBucket,
    *,
    now: datetime,
    thr: TrendThresholds,
    related: Sequence[str] = (),
) -> MarketTrend | None:
    c1 = now - timedelta(days=1)
    c7 = now - timedelta(days=7)
    c30 = now - timedelta(days=30)
    c90 = now - timedelta(days=90)

    items_1d: list[_LiteListing] = []
    items_7d: list[_LiteListing] = []
    items_30d: list[_LiteListing] = []
    items_90d: list[_LiteListing] = []
    active: list[_LiteListing] = []
    disappeared_7d = 0
    disappeared_30d = 0

    for listing in bucket.listings:
        seen = _seen_at(listing)
        if listing.is_active:
            active.append(listing)
        disappeared_at = _as_aware(listing.disappeared_at)
        if not listing.is_active and disappeared_at is not None:
            if disappeared_at >= c7:
                disappeared_7d += 1
            if disappeared_at >= c30:
                disappeared_30d += 1
        if seen is None:
            continue
        if seen >= c90:
            items_90d.append(listing)
        if seen >= c30:
            items_30d.append(listing)
        if seen >= c7:
            items_7d.append(listing)
        if seen >= c1:
            items_1d.append(listing)

    n1, n7, n30, n90 = (
        len(items_1d),
        len(items_7d),
        len(items_30d),
        len(items_90d),
    )
    if n7 < thr.min_samples_short and n30 < thr.min_samples_long:
        return None

    expected_7 = max(1.0, n30 / 4.0)
    volume_ratio = n7 / expected_7 if expected_7 else 0.0
    popularity_change = (volume_ratio - 1.0) * 100.0

    p7 = _median_price(items_7d)
    p30 = _median_price(items_30d)
    price_change = _pct_delta(p7, p30)

    ttl7 = _median_ttl_hours(items_7d, now=now)
    ttl30 = _median_ttl_hours(items_30d, now=now)
    # TTL plus bas = rotation plus rapide → delta négatif = accélération
    rotation_change = _pct_delta(ttl7, ttl30)

    expected_stock = max(1.0, n30 * 0.35)
    stock_change = ((len(active) - expected_stock) / expected_stock) * 100.0

    triggers: list[TrendTrigger] = []
    direction = "up"

    if n7 >= thr.min_samples_short and volume_ratio >= thr.volume_surge_ratio:
        triggers.append(
            TrendTrigger(
                "volume_surge",
                "Hausse de demande estimée",
                f"Volume 7j ×{volume_ratio:.1f} vs rythme 30j "
                f"({n7} vs ~{expected_7:.0f} attendus).",
            )
        )
        direction = "up"

    if (
        n7 >= thr.min_samples_short
        and volume_ratio <= thr.volume_drop_ratio
        and n30 >= thr.min_samples_long
    ):
        triggers.append(
            TrendTrigger(
                "volume_drop",
                "Chute de volume",
                f"Volume 7j en net retrait (×{volume_ratio:.2f} vs rythme 30j).",
            )
        )
        direction = "down"

    if (
        price_change is not None
        and price_change >= thr.price_up_pct
        and n7 >= thr.min_samples_short
    ):
        triggers.append(
            TrendTrigger(
                "price_up",
                "Hausse des prix",
                f"Prix médian 7j {p7:.0f}€ vs 30j {p30:.0f}€ ({price_change:+.0f}%).",
            )
        )
        direction = "up"

    if (
        price_change is not None
        and price_change <= -thr.price_down_pct
        and n7 >= thr.min_samples_short
    ):
        triggers.append(
            TrendTrigger(
                "price_down",
                "Baisse des prix",
                f"Prix médian 7j {p7:.0f}€ vs 30j {p30:.0f}€ ({price_change:+.0f}%).",
            )
        )
        direction = "down"

    if (
        len(active) <= thr.scarcity_active_max
        and n7 >= thr.scarcity_new_7d_min
        and disappeared_7d >= 2
    ):
        triggers.append(
            TrendTrigger(
                "scarcity",
                "Rareté / stock bas",
                f"Seulement {len(active)} annonces actives, {n7} vues/7j, "
                f"{disappeared_7d} disparitions récentes.",
            )
        )
        direction = "scarce"

    if n90 <= thr.new_entity_long_max and n7 >= thr.new_entity_short_min:
        triggers.append(
            TrendTrigger(
                "new_entity",
                "Nouvelle niche émergente",
                f"Peu d'historique 90j ({n90}) mais {n7} signaux sur 7j.",
            )
        )
        direction = "emerging"

    if disappeared_7d >= max(3, n7 // 3) and n7 >= thr.min_samples_short:
        triggers.append(
            TrendTrigger(
                "velocity",
                "Accélération des disparitions",
                f"{disappeared_7d} disparitions /7j pour {n7} annonces observées.",
            )
        )

    if (
        rotation_change is not None
        and rotation_change <= -35
        and n7 >= thr.min_samples_short
        and ttl7 is not None
        and ttl30 is not None
    ):
        triggers.append(
            TrendTrigger(
                "rotation_fast",
                "Rotation accélérée",
                f"TTL médian {ttl7:.1f}h /7j vs {ttl30:.1f}h /30j "
                f"({rotation_change:+.0f}%).",
            )
        )

    if (
        len(active) >= thr.saturation_active_min
        and n7 >= thr.min_samples_short
        and (
            (price_change is not None and price_change <= -thr.price_down_pct * 0.5)
            or volume_ratio >= 1.2
        )
    ):
        sell_through = disappeared_7d / max(1, n7)
        if sell_through < 0.35 or (
            price_change is not None and price_change < 0
        ):
            triggers.append(
                TrendTrigger(
                    "saturation",
                    "Saturation du marché",
                    f"{len(active)} actives, absorption faible "
                    f"({disappeared_7d} disparitions /7j).",
                )
            )
            direction = "saturated"

    if not triggers:
        return None

    codes = {t.code for t in triggers}

    # --- Jauges & score /100 ---
    gauge_growth = _clamp(
        (volume_ratio - 1.0) * 45.0
        + (min(40.0, price_change) if price_change and price_change > 0 else 0.0)
        + (20.0 if "new_entity" in codes else 0.0)
        + (
            min(25.0, abs(rotation_change) * 0.35)
            if rotation_change and rotation_change < 0
            else 0.0
        )
    )
    gauge_demand = _clamp(
        min(55.0, (disappeared_7d / max(1, n7)) * 100.0)
        + min(35.0, volume_ratio * 18.0)
        + (15.0 if "velocity" in codes or "rotation_fast" in codes else 0.0)
    )
    if len(active) <= 8:
        gauge_rarity = 92.0
    elif len(active) <= thr.scarcity_active_max:
        gauge_rarity = 78.0
    elif len(active) <= 25:
        gauge_rarity = 55.0
    elif len(active) <= 50:
        gauge_rarity = 35.0
    else:
        gauge_rarity = 18.0
    if stock_change < -25:
        gauge_rarity = _clamp(gauge_rarity + 12)

    gauge_saturation = _clamp(
        (len(active) / max(1.0, thr.saturation_active_min)) * 40.0
        + (25.0 if "saturation" in codes else 0.0)
        + (20.0 if price_change is not None and price_change < -8 else 0.0)
        + max(0.0, 15.0 - (disappeared_7d / max(1, n7)) * 30.0)
    )

    margin_proxy = 0.0
    if price_change is not None and price_change > 0:
        margin_proxy += min(40.0, price_change * 1.2)
    margin_proxy += gauge_rarity * 0.25
    margin_proxy += max(0.0, 30.0 - gauge_saturation * 0.3)
    if "price_down" in codes and "scarcity" not in codes:
        margin_proxy *= 0.55
    gauge_rentabilite = _clamp(margin_proxy)

    sample_conf = _clamp(
        25.0
        + min(35.0, n7 * 3.5)
        + min(20.0, n30 * 0.8)
        + min(15.0, disappeared_7d * 3.0)
    )
    stability = _clamp(
        70.0
        - (abs(price_change) * 0.8 if price_change is not None else 15.0)
        + (10.0 if n30 >= thr.min_samples_long else -10.0)
    )
    duration_potential = {
        "emergence": 80.0,
        "growth": 70.0,
        "peak": 40.0,
        "decline": 25.0,
        "saturation": 15.0,
    }

    lifecycle = _lifecycle_for(
        direction=direction,
        codes=codes,
        volume_ratio=volume_ratio,
        active=len(active),
        n7=n7,
        n90=n90,
        gauge_saturation=gauge_saturation,
    )
    duration = duration_potential.get(lifecycle, 50.0)

    strength = _clamp(
        gauge_growth * 0.22
        + gauge_demand * 0.20
        + gauge_rarity * 0.14
        + gauge_rentabilite * 0.16
        + (100.0 - gauge_saturation) * 0.10
        + stability * 0.08
        + duration * 0.05
        + sample_conf * 0.05
    )

    continuation = _clamp(
        40.0
        + gauge_growth * 0.25
        + gauge_demand * 0.15
        + stability * 0.15
        + duration * 0.15
        - gauge_saturation * 0.25
        + (8.0 if lifecycle in {"emergence", "growth"} else 0.0)
        - (12.0 if lifecycle in {"decline", "saturation"} else 0.0),
        5.0,
        97.0,
    )
    if sample_conf >= 70:
        confidence_label = "Confiance élevée"
    elif sample_conf >= 45:
        confidence_label = "Confiance moyenne"
    else:
        confidence_label = "Confiance faible"

    importance = _importance_for(strength, lifecycle)
    recommendation, reco_detail = _recommendation_for(
        score=strength,
        lifecycle=lifecycle,
        gauge_saturation=gauge_saturation,
        gauge_demand=gauge_demand,
        codes=codes,
    )
    opportunity = {
        "buy": "Opportunité d'action — fenêtres d'achat encore ouvertes.",
        "watch": "Opportunité de veille — confirmer sur les prochaines 24–72h.",
        "wait": "Attendre — timing défavorable ou prix trop hauts.",
        "avoid": "Éviter — saturation / faible potentiel de marge.",
    }.get(recommendation, "Surveiller ce signal.")

    titles = tuple(
        (l.title or "")[:80]
        for l in sorted(
            items_7d or items_30d,
            key=lambda x: _seen_at(x) or now,
            reverse=True,
        )[:3]
        if l.title
    )

    ai = _ai_analysis(
        display_name=bucket.display_name,
        triggers=triggers,
        lifecycle=lifecycle,
        price_change_pct=price_change,
        rotation_change_pct=rotation_change,
        stock_change_pct=stock_change,
        popularity_change_pct=popularity_change,
        continuation_pct=continuation,
    )
    badges = _badges_for(
        lifecycle=lifecycle,
        importance=importance,
        codes=codes,
        score=strength,
    )

    niches = tuple(related[:5])
    title = build_macro_trend_title(
        bucket.display_name,
        popularity_change_pct=popularity_change,
        price_change_pct=price_change,
        lifecycle=lifecycle,
        codes=codes,
    )
    return MarketTrend(
        entity_type=bucket.entity_type,
        entity_key=bucket.entity_key,
        display_name=bucket.display_name,
        title=title,
        strength=round(strength, 1),
        direction=direction,
        lifecycle=lifecycle,
        importance=importance,
        triggers=tuple(triggers),
        count_1d=n1,
        count_7d=n7,
        count_30d=n30,
        count_90d=n90,
        active_count=len(active),
        price_median_7d=round(p7, 2) if p7 is not None else None,
        price_median_30d=round(p30, 2) if p30 is not None else None,
        price_change_pct=round(price_change, 1) if price_change is not None else None,
        disappeared_7d=disappeared_7d,
        median_ttl_7d_hours=round(ttl7, 2) if ttl7 is not None else None,
        median_ttl_30d_hours=round(ttl30, 2) if ttl30 is not None else None,
        rotation_change_pct=round(rotation_change, 1)
        if rotation_change is not None
        else None,
        stock_change_pct=round(stock_change, 1),
        popularity_change_pct=round(popularity_change, 1),
        gauge_growth=round(gauge_growth, 1),
        gauge_rentabilite=round(gauge_rentabilite, 1),
        gauge_rarity=round(gauge_rarity, 1),
        gauge_demand=round(gauge_demand, 1),
        gauge_saturation=round(gauge_saturation, 1),
        continuation_pct=round(continuation, 1),
        confidence_label=confidence_label,
        sample_titles=titles,
        ai_analysis=ai,
        associated_niches=niches,
        related=niches,
        opportunity=opportunity,
        why_it_matters=_why_it_matters(triggers, bucket.display_name),
        recommendation=recommendation,
        recommendation_detail=reco_detail,
        badges=badges,
    )


def _why_it_matters(triggers: Sequence[TrendTrigger], display_name: str) -> str:
    if not triggers:
        return f"{display_name} présente un comportement atypique sur la période récente."
    parts = [t.detail for t in triggers[:3]]
    return f"{display_name} : " + " ".join(parts)


def _combo_key(a: str, b: str) -> str:
    parts = sorted([a, b])
    return f"{parts[0]}+{parts[1]}"


def build_macro_trend_title(
    display_name: str,
    *,
    popularity_change_pct: float | None,
    price_change_pct: float | None,
    lifecycle: str,
    codes: set[str],
) -> str:
    """Titre mouvement de marché (pas un produit)."""
    name = display_name.strip()
    pop = popularity_change_pct or 0.0
    if "new_entity" in codes or lifecycle == "emergence":
        return f"Émergence — {name}"
    if pop >= 70 or "volume_surge" in codes and pop >= 40:
        return f"Explosion — {name}"
    if pop >= 25 or "volume_surge" in codes:
        return f"Hausse — {name}"
    if "price_up" in codes or (price_change_pct or 0) >= 12:
        return f"Prix en hausse — {name}"
    if lifecycle == "saturation" or "saturation" in codes:
        return f"Saturation — {name}"
    if lifecycle == "decline" or "volume_drop" in codes:
        return f"Reflux — {name}"
    if "scarcity" in codes:
        return f"Rareté — {name}"
    return f"Mouvement — {name}"


def is_vague_alone(topic: TopicDef, thr: TrendThresholds) -> bool:
    if topic.slug in thr.vague_alone_slugs:
        return True
    if topic.kind in _OBJECT_KINDS and not topic.standalone_ok:
        return True
    if not topic.standalone_ok and topic.kind in {"style"} and topic.slug == "vintage_obj":
        return True
    return False


def extract_niche_labels(
    listing: _LiteListing,
    topic_hits: Sequence[TopicDef],
) -> list[str]:
    """Niveau 2 — niches précises (marque/modèle/segment) pour expliquer un mouvement."""
    labels: list[str] = []
    brand = normalize_brand(listing.brand)
    brand_label = brand.replace("_", " ").title() if brand else None

    models: list[str] = []
    for etype, _key, display in extract_commercial_phrases(listing.title):
        if etype == "model" and display:
            models.append(display)
            if brand_label:
                labels.append(f"{brand_label} {display}")
            else:
                labels.append(display)

    objects = [t for t in topic_hits if t.kind in _OBJECT_KINDS]
    eras_styles = [t for t in topic_hits if t.kind in _CONTEXT_KINDS]
    if brand_label and objects:
        for obj in objects[:2]:
            labels.append(f"{brand_label} · {obj.display_name}")
            for ctx in eras_styles[:1]:
                labels.append(f"{brand_label} {obj.display_name} {ctx.display_name}")
    elif brand_label and eras_styles and not models:
        labels.append(f"{brand_label} · {eras_styles[0].display_name}")

    # Dédup
    seen: set[str] = set()
    out: list[str] = []
    for lab in labels:
        key = lab.strip().lower()
        if len(key) < 5 or key in seen:
            continue
        seen.add(key)
        out.append(lab.strip())
    return out[:6]


def detect_market_trends(
    *,
    limit: int | None = None,
    min_score: float | None = None,
    include_weak: bool = False,
) -> list[MarketTrend]:
    """Détecte les **mouvements de marché** (Niveau 1) + niches associées (Niveau 2).

    Ne publie pas de marques/modèles/produits isolés (réservés aux autres salons).
    """
    thr, topics = load_trend_config()
    max_out = limit if limit is not None else thr.max_trends_posted
    score_floor = (
        float(min_score)
        if min_score is not None
        else (0.0 if include_weak else thr.min_publish_score)
    )
    now = _utcnow()
    since = now - timedelta(days=thr.lookback_days)

    with session_scope() as session:
        listings = list(
            session.scalars(
                select(Listing)
                .where(
                    (Listing.first_seen_at >= since)
                    | (Listing.last_seen_at >= since)
                    | (Listing.is_active.is_(True))
                )
                .limit(25000)
            ).all()
        )
        rows = [
            _LiteListing(
                title=listing.title or "",
                brand=listing.brand,
                price_cents=listing.price_cents,
                size=listing.size,
                is_active=bool(listing.is_active),
                first_seen_at=listing.first_seen_at,
                published_at=listing.published_at,
                scraped_at=listing.scraped_at,
                last_seen_at=listing.last_seen_at,
                disappeared_at=listing.disappeared_at,
            )
            for listing in listings
        ]

    macro_buckets: dict[str, _EntityBucket] = {}
    niche_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def _add_macro(key: str, display: str, listing: _LiteListing) -> str:
        full = f"macro:{key}"
        bucket = macro_buckets.get(full)
        if bucket is None:
            bucket = _EntityBucket("macro", key, display)
            macro_buckets[full] = bucket
        bucket.listings.append(listing)
        return full

    for listing in rows:
        topic_hits = match_topics(listing.title, topics)
        if not topic_hits:
            continue

        movements = [
            t
            for t in topic_hits
            if t.kind in _MOVEMENT_KINDS and not is_vague_alone(t, thr)
        ]
        objects = [t for t in topic_hits if t.kind in _OBJECT_KINDS]
        macros_on_listing: list[str] = []

        # Niveau 1a — mouvements standalone (Y2K, Gorpcore, Années 90…)
        for mov in movements:
            if not mov.standalone_ok:
                continue
            uid = _add_macro(mov.slug, mov.display_name, listing)
            macros_on_listing.append(uid)

        # Niveau 1b — objet + contexte (Sacs Y2K, Déco Années 70…)
        # "Vintage" seul est interdit, mais "Sacs Vintage" est valide.
        for obj in objects:
            for ctx in topic_hits:
                if ctx.kind not in _CONTEXT_KINDS:
                    continue
                if ctx.slug == obj.slug:
                    continue
                ckey = _combo_key(obj.slug, ctx.slug)
                if ctx.kind in {"style", "era", "movement"}:
                    display = f"{obj.display_name} {ctx.display_name}"
                else:
                    display = f"{obj.display_name} × {ctx.display_name}"
                uid = _add_macro(ckey, display, listing)
                macros_on_listing.append(uid)

        # Licence standalone déjà couverte via movements si standalone_ok
        # Niches associées (Niveau 2)
        niches = extract_niche_labels(listing, topic_hits)
        for uid in dict.fromkeys(macros_on_listing):
            for niche in niches:
                niche_counts[uid][niche] += 1

    def _top_niches(uid: str, display: str, *, top: int = 5) -> tuple[str, ...]:
        macro_tokens = set(_normalize_text(display).split()) - {"x", "et", "de", "la"}

        def _rank(item: tuple[str, int]) -> tuple[int, int]:
            name, count = item
            overlap = len(macro_tokens & set(_normalize_text(name).split()))
            return (overlap, count)

        peers = sorted(niche_counts.get(uid, {}).items(), key=_rank, reverse=True)
        names: list[str] = []
        for name, count in peers:
            if count < 2 and len(names) >= 2:
                continue
            if name not in names:
                names.append(name)
            if len(names) >= top:
                break
        if len(names) < 3:
            for name, _count in peers:
                if name not in names:
                    names.append(name)
                if len(names) >= top:
                    break
        return tuple(names)

    trends: list[MarketTrend] = []
    for uid, bucket in macro_buckets.items():
        if len(bucket.listings) < thr.min_token_listings:
            continue
        # Rejeter clés trop vagues (objet seul glissé par erreur)
        key_parts = bucket.entity_key.split("+")
        if len(key_parts) == 1 and key_parts[0] in thr.vague_alone_slugs:
            continue
        niches = _top_niches(uid, bucket.display_name)
        trend = evaluate_entity(bucket, now=now, thr=thr, related=niches)
        if trend is None:
            continue
        # Boost léger si niches riches (mouvement mieux expliqué)
        boost = 1.15 if len(niches) >= 2 else 1.05
        boosted = round(min(100.0, trend.strength * boost), 1)
        lifecycle = trend.lifecycle
        importance = _importance_for(boosted, lifecycle)
        recommendation, reco_detail = _recommendation_for(
            score=boosted,
            lifecycle=lifecycle,
            gauge_saturation=trend.gauge_saturation,
            gauge_demand=trend.gauge_demand,
            codes={t.code for t in trend.triggers},
        )
        badges = _badges_for(
            lifecycle=lifecycle,
            importance=importance,
            codes={t.code for t in trend.triggers},
            score=boosted,
        )
        title = build_macro_trend_title(
            trend.display_name,
            popularity_change_pct=trend.popularity_change_pct,
            price_change_pct=trend.price_change_pct,
            lifecycle=lifecycle,
            codes={t.code for t in trend.triggers},
        )
        trends.append(
            MarketTrend(
                entity_type="macro",
                entity_key=trend.entity_key,
                display_name=trend.display_name,
                title=title,
                strength=boosted,
                direction=trend.direction,
                lifecycle=lifecycle,
                importance=importance,
                triggers=trend.triggers,
                count_1d=trend.count_1d,
                count_7d=trend.count_7d,
                count_30d=trend.count_30d,
                count_90d=trend.count_90d,
                active_count=trend.active_count,
                price_median_7d=trend.price_median_7d,
                price_median_30d=trend.price_median_30d,
                price_change_pct=trend.price_change_pct,
                disappeared_7d=trend.disappeared_7d,
                median_ttl_7d_hours=trend.median_ttl_7d_hours,
                median_ttl_30d_hours=trend.median_ttl_30d_hours,
                rotation_change_pct=trend.rotation_change_pct,
                stock_change_pct=trend.stock_change_pct,
                popularity_change_pct=trend.popularity_change_pct,
                gauge_growth=trend.gauge_growth,
                gauge_rentabilite=trend.gauge_rentabilite,
                gauge_rarity=trend.gauge_rarity,
                gauge_demand=trend.gauge_demand,
                gauge_saturation=trend.gauge_saturation,
                continuation_pct=trend.continuation_pct,
                confidence_label=trend.confidence_label,
                sample_titles=trend.sample_titles,
                ai_analysis=trend.ai_analysis,
                associated_niches=niches,
                related=niches,
                opportunity=trend.opportunity,
                why_it_matters=trend.why_it_matters,
                recommendation=recommendation,
                recommendation_detail=reco_detail,
                badges=badges,
            )
        )

    trends = [
        t
        for t in trends
        if t.strength >= score_floor
        and (include_weak or t.importance != "weak")
    ]
    trends.sort(key=lambda t: t.strength, reverse=True)

    # Dédupliquer les libellés identiques (même mouvement, clés différentes)
    picked: list[MarketTrend] = []
    seen_labels: set[str] = set()
    for trend in trends:
        label = trend.display_name.strip().lower()
        if label in seen_labels:
            continue
        seen_labels.add(label)
        picked.append(trend)
        if len(picked) >= max_out:
            break

    log.info(
        "market_trends_detected",
        candidates=len(trends),
        posted=len(picked),
        listings=len(rows),
        macro_buckets=len(macro_buckets),
    )
    return picked
