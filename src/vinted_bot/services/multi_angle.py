"""Analyse multi-angle du marché (demande, offre, prix, comportement, etc.).

Compare les fenêtres 1d/7d/30d pour détecter les nouveaux signaux.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from vinted_bot.db.models import Listing, NicheSnapshot


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _pct_delta(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return ((curr - prev) / abs(prev)) * 100.0


def extract_engagement(listing: Listing | Any) -> tuple[int, int]:
    """Retourne (favourite_count, view_count) depuis raw_json Vinted."""
    raw = listing.raw_json if isinstance(getattr(listing, "raw_json", None), dict) else {}
    fav = raw.get("favourite_count")
    views = raw.get("view_count")
    try:
        fav_i = int(fav) if fav is not None else 0
    except (TypeError, ValueError):
        fav_i = 0
    try:
        view_i = int(views) if views is not None else 0
    except (TypeError, ValueError):
        view_i = 0
    return max(0, fav_i), max(0, view_i)


def aggregate_engagement(listings: Sequence[Listing | Any]) -> dict[str, float]:
    favs: list[int] = []
    views: list[int] = []
    for listing in listings:
        f, v = extract_engagement(listing)
        if f > 0 or v > 0:
            favs.append(f)
            views.append(v)
        else:
            # Compte aussi les zéros pour moyenne réaliste
            favs.append(0)
            views.append(0)
    n = len(listings) or 1
    fav_sum = float(sum(favs))
    view_sum = float(sum(views))
    return {
        "favourite_sum": fav_sum,
        "favourite_avg": fav_sum / n,
        "view_sum": view_sum,
        "view_avg": view_sum / n,
        "engagement_listings": float(len(listings)),
    }


@dataclass(slots=True, frozen=True)
class AngleScore:
    key: str
    label: str
    score: float
    summary: str


@dataclass(slots=True, frozen=True)
class MultiAngleReport:
    demand: AngleScore
    supply: AngleScore
    price: AngleScore
    behavioral: AngleScore
    emerging: AngleScore
    profitability: AngleScore
    anomaly: AngleScore
    signals: tuple[str, ...]
    composite: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "demand": self.demand.score,
            "supply": self.supply.score,
            "price": self.price.score,
            "behavioral": self.behavioral.score,
            "emerging": self.emerging.score,
            "profitability": self.profitability.score,
            "anomaly": self.anomaly.score,
            "composite": self.composite,
            "signals": list(self.signals),
        }

    def embed_block(self) -> str:
        lines = [
            f"📥 Demande `{self.demand.score:.0f}` — {self.demand.summary}",
            f"📦 Offre `{self.supply.score:.0f}` — {self.supply.summary}",
            f"💶 Prix `{self.price.score:.0f}` — {self.price.summary}",
            f"⚡ Comportement `{self.behavioral.score:.0f}` — {self.behavioral.summary}",
            f"🆕 Émergent `{self.emerging.score:.0f}` — {self.emerging.summary}",
            f"📈 Rentabilité `{self.profitability.score:.0f}` — {self.profitability.summary}",
            f"🚨 Anomalies `{self.anomaly.score:.0f}` — {self.anomaly.summary}",
        ]
        if self.signals:
            lines.append("Signaux : " + " · ".join(self.signals[:4]))
        return "\n".join(lines)[:1024]


def _win_attr(windows: Mapping[str, Any], window: str, attr: str, default: float = 0.0) -> float:
    w = windows.get(window)
    if w is None:
        return default
    value = getattr(w, attr, None)
    if value is None and isinstance(w, Mapping):
        value = w.get(attr)
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def compute_multi_angle(
    snap: NicheSnapshot | Any,
    *,
    windows: Mapping[str, Any] | None = None,
    engagement: Mapping[str, float] | None = None,
    obscure_brand: bool = False,
) -> MultiAngleReport:
    """Calcule les 7 angles + signaux de changement vs historique."""
    w = windows or {}
    eng = engagement or {}
    metrics = snap.metrics if isinstance(getattr(snap, "metrics", None), dict) else {}

    listing_count = float(snap.listing_count or 0)
    disappeared = float(snap.disappeared_count or 0)
    sellers = float(snap.unique_sellers or 0)
    new_listings = float(snap.new_listings or 0)
    margin = float(snap.margin_proxy_pct or 0)
    ttl = float(snap.median_ttl_days) if snap.median_ttl_days is not None else None
    median = float(snap.price_median_cents or 0) / 100.0
    p25 = float(snap.price_p25_cents or 0) / 100.0
    mean = float(snap.price_mean_cents or 0) / 100.0 if getattr(snap, "price_mean_cents", None) else median

    fav_avg = float(eng.get("favourite_avg") or metrics.get("favourite_avg") or 0)
    view_avg = float(eng.get("view_avg") or metrics.get("view_avg") or 0)
    active_count = float(
        eng.get("active_count")
        or metrics.get("active_count")
        or listing_count
    )

    vol1 = _win_attr(w, "1d", "new_listings", new_listings)
    vol7 = _win_attr(w, "7d", "new_listings", new_listings)
    vol30 = _win_attr(w, "30d", "new_listings", max(new_listings, listing_count))
    n7 = _win_attr(w, "7d", "listing_count", listing_count)
    n30 = _win_attr(w, "30d", "listing_count", listing_count)
    med7 = _win_attr(w, "7d", "price_median_cents", snap.price_median_cents or 0)
    med30 = _win_attr(w, "30d", "price_median_cents", snap.price_median_cents or 0)
    dis7 = _win_attr(w, "7d", "disappeared_count", disappeared)
    # WindowPoint may not have disappeared — fallback snap
    if dis7 == 0 and hasattr(w.get("7d"), "disappeared_count"):
        dis7 = float(getattr(w["7d"], "disappeared_count") or 0)

    expected7 = max(1.0, vol30 / 4.0)
    demand_ratio = vol7 / expected7
    dis_ratio = disappeared / max(1.0, listing_count)
    stock_delta = _pct_delta(n7, n30 / 4.0 if n30 else None)
    price_delta = _pct_delta(med7 if med7 else None, med30 if med30 else None)

    # --- Demande ---
    demand_score = _clamp(
        min(35.0, demand_ratio * 18.0)
        + min(30.0, dis_ratio * 70.0)
        + min(20.0, fav_avg * 4.0)
        + min(15.0, view_avg * 0.8)
    )
    demand_bits: list[str] = []
    if dis_ratio >= 0.15:
        demand_bits.append(f"disparitions {dis_ratio*100:.0f}%")
    if fav_avg >= 2:
        demand_bits.append(f"fav ~{fav_avg:.1f}")
    if demand_ratio >= 1.3:
        demand_bits.append("flux 7j élevé")
    demand = AngleScore(
        "demand",
        "Demande",
        round(demand_score, 1),
        " · ".join(demand_bits) or "demande modérée",
    )

    # --- Offre ---
    if listing_count <= 8:
        rarity = 85.0
    elif listing_count <= 20:
        rarity = 65.0
    elif listing_count <= 40:
        rarity = 45.0
    else:
        rarity = 25.0
    competition = _clamp(sellers * 5.0 + active_count * 0.4)
    supply_score = _clamp(
        rarity * 0.55
        + (100.0 - competition) * 0.35
        + (10.0 if (stock_delta is not None and stock_delta < -15) else 0.0)
    )
    supply_bits = [f"stock {int(active_count)}", f"{int(sellers)} vendeurs"]
    if stock_delta is not None and abs(stock_delta) >= 20:
        supply_bits.append(f"stock {stock_delta:+.0f}% vs tendance")
    supply = AngleScore(
        "supply",
        "Offre",
        round(supply_score, 1),
        " · ".join(supply_bits),
    )

    # --- Prix ---
    spread_ok = 50.0
    if median > 0 and mean > 0:
        spread_ok = _clamp(100.0 - abs(mean - median) / median * 80.0)
    margin_pts = _clamp(margin * 0.65)
    price_trend_pts = 50.0
    if price_delta is not None:
        # Baisse de prix = opportunité achat ; hausse = valeur qui monte
        if price_delta <= -8:
            price_trend_pts = 70.0
        elif price_delta >= 10:
            price_trend_pts = 62.0
        else:
            price_trend_pts = 50.0
    price_score = _clamp(margin_pts * 0.5 + spread_ok * 0.25 + price_trend_pts * 0.25)
    price_bits = []
    if median:
        price_bits.append(f"médiane {median:.0f}€")
    if p25:
        price_bits.append(f"P25 {p25:.0f}€")
    if price_delta is not None:
        price_bits.append(f"Δprix 7j {price_delta:+.0f}%")
    price = AngleScore(
        "price",
        "Prix",
        round(price_score, 1),
        " · ".join(price_bits) or "prix stables",
    )

    # --- Comportemental (attention rapide) ---
    attention = fav_avg * 3.0 + view_avg * 0.5
    ttl_boost = 0.0
    if ttl is not None and ttl <= 3 and disappeared >= 2:
        ttl_boost = 25.0
    elif ttl is not None and ttl <= 7 and disappeared >= 2:
        ttl_boost = 12.0
    behavioral_score = _clamp(min(55.0, attention * 2.0) + ttl_boost + min(20.0, vol1 * 4.0))
    beh_bits = []
    if attention >= 5:
        beh_bits.append("forte attention (fav/vues)")
    if ttl_boost:
        beh_bits.append(f"TTL court ~{ttl:.1f}j")
    if vol1 >= 3:
        beh_bits.append("nouveautés 24h")
    behavioral = AngleScore(
        "behavioral",
        "Comportement",
        round(behavioral_score, 1),
        " · ".join(beh_bits) or "attention normale",
    )

    # --- Tendances émergentes ---
    emerge_score = _clamp(
        min(40.0, max(0.0, (demand_ratio - 1.0) * 35.0))
        + (18.0 if obscure_brand else 0.0)
        + min(25.0, (vol7 / max(1.0, listing_count)) * 40.0)
        + (15.0 if (stock_delta is not None and stock_delta > 25 and demand_ratio >= 1.2) else 0.0)
    )
    em_bits = []
    if demand_ratio >= 1.4:
        em_bits.append("accélération flux")
    if obscure_brand:
        em_bits.append("marque/produit peu connu")
    if vol7 >= 5 and listing_count <= 25:
        em_bits.append("niche en formation")
    emerging = AngleScore(
        "emerging",
        "Émergent",
        round(emerge_score, 1),
        " · ".join(em_bits) or "pas de signal émergent fort",
    )

    # --- Rentabilité ---
    buy_ease = 70.0 if 5 <= listing_count <= 35 else (40.0 if listing_count < 5 else 50.0)
    profit_score = _clamp(
        _clamp(margin * 0.7) * 0.45
        + buy_ease * 0.25
        + demand_score * 0.15
        + (100.0 - competition) * 0.15
    )
    profit_bits = []
    if margin >= 40:
        profit_bits.append(f"marge ~{margin:.0f}%")
    profit_bits.append("achat " + ("facile" if buy_ease >= 60 else "sélectif"))
    profitability = AngleScore(
        "profitability",
        "Rentabilité",
        round(profit_score, 1),
        " · ".join(profit_bits),
    )

    # --- Anomalies (sous-évaluation) ---
    gap = ((median - p25) / median * 100.0) if median > 0 and p25 > 0 else 0.0
    anomaly_score = _clamp(
        min(50.0, gap * 0.9)
        + (20.0 if margin >= 55 else 0.0)
        + (15.0 if price_delta is not None and price_delta <= -10 else 0.0)
        + (10.0 if demand_score >= 55 and listing_count <= 20 else 0.0)
    )
    an_bits = []
    if gap >= 25:
        an_bits.append(f"écart P25/médiane {gap:.0f}%")
    if price_delta is not None and price_delta <= -10:
        an_bits.append("prix en baisse")
    if margin >= 55:
        an_bits.append("fort potentiel revente")
    anomaly = AngleScore(
        "anomaly",
        "Anomalies",
        round(anomaly_score, 1),
        " · ".join(an_bits) or "pas d'anomalie nette",
    )

    # --- Signaux de changement (vs historique) ---
    signals: list[str] = []
    if demand_ratio >= 1.5:
        signals.append("📈 demande en hausse")
    if dis_ratio >= 0.25 and disappeared >= 3:
        signals.append("🔥 liquidité forte")
    if price_delta is not None and price_delta <= -12:
        signals.append("⬇️ prix en baisse")
    if price_delta is not None and price_delta >= 12:
        signals.append("⬆️ prix en hausse")
    if stock_delta is not None and stock_delta <= -25:
        signals.append("🧊 stock qui se raréfie")
    if emerge_score >= 60:
        signals.append("🆕 signal émergent")
    if anomaly_score >= 60:
        signals.append("💎 sous-évaluation")
    if behavioral_score >= 65:
        signals.append("⚡ attention rapide")
    if fav_avg >= 3:
        signals.append(f"❤️ fav avg {fav_avg:.1f}")

    composite = round(
        _clamp(
            demand.score * 0.18
            + supply.score * 0.12
            + price.score * 0.16
            + behavioral.score * 0.12
            + emerging.score * 0.12
            + profitability.score * 0.18
            + anomaly.score * 0.12
        ),
        1,
    )

    return MultiAngleReport(
        demand=demand,
        supply=supply,
        price=price,
        behavioral=behavioral,
        emerging=emerging,
        profitability=profitability,
        anomaly=anomaly,
        signals=tuple(signals),
        composite=composite,
    )
