"""Embeds Discord premium — dashboard d'intelligence de marché."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

ENGINE_VERSION = "market-intel/3.0"

# Couleurs embed
COLOR_GREEN = 0x2ECC71
COLOR_YELLOW = 0xF1C40F
COLOR_ORANGE = 0xE67E22
COLOR_RED = 0xE74C3C
COLOR_BLUE = 0x3498DB
COLOR_GOLD = 0xF39C12
COLOR_PURPLE = 0x9B59B6

CATEGORY_EMOJI: dict[str, str] = {
    "chaussure": "👟",
    "dunk": "👟",
    "air_force_1": "👟",
    "hoodie": "🧥",
    "sweat": "🧥",
    "pull": "🧶",
    "veste": "🧥",
    "pantalon": "👖",
    "short": "🩳",
    "polo": "👕",
    "tshirt": "👕",
    "chemise": "👔",
}

SPARK_BARS = "▁▂▃▄▅▆▇█"


@dataclass(slots=True, frozen=True)
class WindowPoint:
    window: str
    listing_count: int
    price_median_cents: int | None
    median_ttl_days: float | None
    score: float | None
    new_listings: int
    margin_proxy_pct: float | None
    disappeared_count: int = 0


@dataclass(slots=True, frozen=True)
class NicheCard:
    """Données normalisées pour un embed dashboard."""

    niche_key: str
    brand_slug: str
    model_slug: str | None
    category_slug: str | None
    keyword_flags: str
    score: float
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
    volume_7d: int
    volume_30d: int
    rank: int | None = None
    windows: tuple[WindowPoint, ...] = ()
    sample_size: int = 0
    url: str | None = None
    photo_url: str | None = None
    # Mode pépite (annonce unitaire)
    ask_cents: int | None = None
    size: str | None = None
    title: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def pretty_slug(value: str | None) -> str:
    if not value:
        return "—"
    return value.replace("_", " ").title()


def format_eur_cents(cents: int | None) -> str:
    if cents is None:
        return "—"
    return f"{cents / 100:.0f} €"


def score_color(score: float) -> int:
    if score >= 90:
        return COLOR_GREEN
    if score >= 70:
        return COLOR_YELLOW
    if score >= 50:
        return COLOR_ORANGE
    return COLOR_RED


def score_stars(score: float) -> str:
    filled = max(0, min(5, round(score / 20.0)))
    return "⭐" * filled + "☆" * (5 - filled)


def category_emoji(category: str | None, *, trending: bool = False) -> str:
    if trending:
        return "🔥"
    if category and category in CATEGORY_EMOJI:
        return CATEGORY_EMOJI[category]
    return "💎"


def gauge(value_0_100: float, *, width: int = 10) -> str:
    pct = max(0.0, min(100.0, float(value_0_100)))
    filled = int(round((pct / 100.0) * width))
    filled = max(0, min(width, filled))
    return f"{'█' * filled}{'░' * (width - filled)} {pct:.0f} %"


def sparkline(values: Sequence[float | None], *, width: int = 8) -> str:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return "—"
    # Pad / resample to width
    if len(nums) == 1:
        series = nums * width
    elif len(nums) >= width:
        step = (len(nums) - 1) / (width - 1)
        series = [nums[int(round(i * step))] for i in range(width)]
    else:
        # Linear interpolate
        series = []
        for i in range(width):
            pos = i * (len(nums) - 1) / (width - 1)
            lo = int(pos)
            hi = min(len(nums) - 1, lo + 1)
            frac = pos - lo
            series.append(nums[lo] * (1 - frac) + nums[hi] * frac)
    lo_v, hi_v = min(series), max(series)
    span = hi_v - lo_v if hi_v > lo_v else 1.0
    chars = []
    for v in series:
        idx = int(round(((v - lo_v) / span) * (len(SPARK_BARS) - 1)))
        chars.append(SPARK_BARS[max(0, min(len(SPARK_BARS) - 1, idx))])
    return "".join(chars)


def arrow_for_change(current: float | None, previous: float | None) -> str:
    if current is None or previous is None or previous == 0:
        return "➡️"
    delta = (current - previous) / abs(previous)
    if delta > 0.05:
        return "⬆️"
    if delta < -0.05:
        return "⬇️"
    return "➡️"


def pct_change(current: float | None, previous: float | None) -> str:
    if current is None or previous is None or previous == 0:
        return "—"
    delta = ((current - previous) / abs(previous)) * 100.0
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.0f} %"


def _window_map(card: NicheCard) -> dict[str, WindowPoint]:
    return {w.window: w for w in card.windows}


def compute_gauges(card: NicheCard) -> dict[str, float]:
    score = card.score
    margin = max(0.0, min(100.0, (card.margin_proxy_pct or 0.0) * 0.7))
    # Popularité via volume 7j relatif
    pop = max(0.0, min(100.0, (card.volume_7d / max(1.0, card.volume_30d / 3.0)) * 45.0 + min(40.0, card.listing_count)))
    # Rareté inverse volume / sellers
    if card.listing_count <= 15 and card.unique_sellers <= 8:
        rarity = 90.0
    elif card.listing_count <= 40:
        rarity = 70.0
    elif card.listing_count <= 100:
        rarity = 45.0
    else:
        rarity = 20.0
    # Confiance : sample + disparitions
    confidence = 40.0
    confidence += min(30.0, card.sample_size / 3.0)
    confidence += min(20.0, card.disappeared_count * 4.0)
    if card.median_ttl_days is not None:
        confidence += 10.0
    confidence = min(100.0, confidence)
    # Concurrence (plus haut = pire)
    competition = max(
        0.0,
        min(100.0, (card.unique_sellers * 8.0) + (card.listing_count * 0.35)),
    )
    # Rentabilité alignée score/marge
    rentabilite = max(0.0, min(100.0, score * 0.55 + margin * 0.45))
    return {
        "rentabilite": rentabilite,
        "popularite": pop,
        "rarete": rarity,
        "confiance": confidence,
        "concurrence": competition,
    }


def build_badges(card: NicheCard, gauges: dict[str, float]) -> list[str]:
    badges: list[str] = []
    if gauges["rarete"] >= 80:
        badges.append("💎 Rare")
    if card.volume_7d >= max(5, int(card.volume_30d * 0.35)):
        badges.append("🔥 Très recherché")
    if card.median_ttl_days is not None and card.median_ttl_days <= 5:
        badges.append("⚡ Rotation rapide")
    w = _window_map(card)
    w1, w7 = w.get("1d"), w.get("7d")
    w30 = w.get("30d")
    if w7 and w30 and (w7.new_listings or 0) >= max(3, int((w30.new_listings or 1) * 0.4)):
        badges.append("📈 En forte hausse")
    if w1 and (w1.new_listings or 0) >= 3 and (w30 is None or (w30.listing_count or 0) < 20):
        badges.append("🆕 Nouvelle niche")
    if card.rank is not None and card.rank <= 10:
        badges.append("👑 Top 10")
    if (card.margin_proxy_pct or 0) >= 50:
        badges.append("💰 Forte marge")
    if w7 and w30 and (w7.score or 0) > (w30.score or 0) * 1.15:
        badges.append("🚀 Tendance")
    if gauges["concurrence"] <= 35:
        badges.append("🟢 Faible concurrence")
    elif gauges["concurrence"] >= 70:
        badges.append("🔴 Marché saturé")
    return badges[:8]


def build_analysis(card: NicheCard, gauges: dict[str, float]) -> list[str]:
    lines: list[str] = []
    w = _window_map(card)
    w7, w30 = w.get("7d"), w.get("30d")
    if card.volume_7d >= max(4, int(card.volume_30d * 0.3)):
        lines.append("La demande est en forte augmentation depuis plusieurs jours.")
    elif card.volume_7d <= max(1, int(card.volume_30d * 0.1)):
        lines.append("La demande ralentit sur les 7 derniers jours.")
    if card.listing_count <= 20:
        lines.append("Le nombre d'annonces reste faible — offre limitée.")
    elif card.listing_count >= 80:
        lines.append("L'offre est abondante sur cette niche.")
    if card.median_ttl_days is not None and card.median_ttl_days <= 5:
        lines.append("Les annonces disparaissent rapidement (bonne liquidité).")
    elif card.median_ttl_days is not None and card.median_ttl_days >= 20:
        lines.append("La rotation est lente — risque de stock prolongé.")
    if w7 and w30 and w7.price_median_cents and w30.price_median_cents:
        if w7.price_median_cents > w30.price_median_cents * 1.05:
            lines.append("Le prix moyen continue d'augmenter.")
        elif w7.price_median_cents < w30.price_median_cents * 0.95:
            lines.append("Les prix moyens baissent — prudence sur la revente.")
    if (card.margin_proxy_pct or 0) >= 45:
        lines.append("Le potentiel de marge est supérieur à la moyenne.")
    elif (card.margin_proxy_pct or 0) < 35:
        lines.append("La marge estimée est encore trop juste après frais.")
    if gauges["concurrence"] <= 35:
        lines.append("Le marché n'est pas encore saturé.")
    elif gauges["concurrence"] >= 70:
        lines.append("La concurrence est déjà élevée sur cette niche.")
    if not lines:
        lines.append("Les signaux sont mitigés — surveiller l'évolution 7j.")
    return lines[:6]


def build_risk(card: NicheCard, gauges: dict[str, float]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    risk_score = 0
    if gauges["concurrence"] >= 70:
        risk_score += 2
        reasons.append("beaucoup de concurrence")
    if card.listing_count >= 100:
        risk_score += 1
        reasons.append("marché saturé")
    if card.median_ttl_days is not None and card.median_ttl_days >= 18:
        risk_score += 2
        reasons.append("rotation lente")
    w = _window_map(card)
    w7, w30 = w.get("7d"), w.get("30d")
    if w7 and w30 and w7.price_median_cents and w30.price_median_cents:
        vol = abs(w7.price_median_cents - w30.price_median_cents) / max(
            1.0, w30.price_median_cents
        )
        if vol > 0.25:
            risk_score += 1
            reasons.append("prix instables")
    if gauges["confiance"] < 50:
        risk_score += 1
        reasons.append("données encore limitées")
    if risk_score >= 3:
        return "🔴 Élevé", reasons or ["signaux défavorables"]
    if risk_score >= 1:
        return "🟡 Moyen", reasons or ["quelques points de vigilance"]
    return "🟢 Faible", reasons or ["profil équilibré"]


def build_saturation(card: NicheCard, gauges: dict[str, float]) -> tuple[str, list[str]]:
    if gauges["concurrence"] >= 70 or card.listing_count >= 120:
        level = "🔴 Saturé"
        details = [
            f"{card.listing_count} annonces",
            "Forte concurrence",
        ]
        w = _window_map(card)
        w7, w30 = w.get("7d"), w.get("30d")
        if w7 and w30 and w7.price_median_cents and w30.price_median_cents:
            if w7.price_median_cents < w30.price_median_cents:
                details.append("Baisse progressive des prix")
        return level, details
    if gauges["concurrence"] <= 35 and card.listing_count <= 40:
        return "🟢 Faible", [
            f"{card.listing_count} annonces disponibles",
            "Demande élevée" if card.volume_7d >= 5 else "Offre encore limitée",
        ]
    return "🟡 Modérée", [
        f"{card.listing_count} annonces",
        f"{card.unique_sellers} vendeurs actifs",
    ]


def build_recommendation(
    card: NicheCard,
    gauges: dict[str, float],
    risk_label: str,
) -> tuple[str, list[str]]:
    margin = card.margin_proxy_pct or 0.0
    if (
        card.score >= 70
        and margin >= 40
        and gauges["concurrence"] <= 45
        and not risk_label.startswith("🔴")
    ):
        return "Acheter immédiatement", [
            "Très bon potentiel",
            "Faible concurrence" if gauges["concurrence"] <= 35 else "Concurrence acceptable",
            "Marge élevée",
        ]
    if card.score >= 55 and margin >= 35 and not risk_label.startswith("🔴"):
        return "Surveiller / acheter sélectif", [
            "Bon potentiel si prix sous la médiane",
            "Vérifier taille et état",
            f"Score {card.score:.0f}/100",
        ]
    if gauges["concurrence"] >= 70 or margin < 30:
        return "Éviter cette niche", [
            "Concurrence importante" if gauges["concurrence"] >= 70 else "Marge insuffisante",
            "Le potentiel de bénéfice est faible",
        ]
    return "Attendre quelques jours", [
        "Le marché est actuellement trop cher"
        if margin < 40
        else "Signaux encore instables",
        "Revoir après nouvelles observations",
    ]


def success_rate_estimate(card: NicheCard) -> str:
    if card.listing_count <= 0:
        return "—"
    rate = min(
        95.0,
        (card.disappeared_count / max(1, card.listing_count)) * 100.0
        + (10.0 if (card.median_ttl_days or 99) <= 7 else 0.0),
    )
    if card.disappeared_count < 2:
        return f"~{max(25.0, rate * 0.6):.0f} % (estim.)"
    return f"~{rate:.0f} %"


def niche_display_name(card: NicheCard) -> str:
    if card.model_slug:
        return f"{pretty_slug(card.brand_slug)} {pretty_slug(card.model_slug)}"
    if card.category_slug:
        return f"{pretty_slug(card.brand_slug)} · {pretty_slug(card.category_slug)}"
    return pretty_slug(card.brand_slug)


def build_niche_dashboard_embed(
    card: NicheCard,
    *,
    kind: str = "niche",
) -> dict[str, Any]:
    """Embed dashboard complet (philosophie 4 questions)."""
    gauges = compute_gauges(card)
    badges = build_badges(card, gauges)
    analysis = build_analysis(card, gauges)
    risk_label, risk_reasons = build_risk(card, gauges)
    sat_label, sat_details = build_saturation(card, gauges)
    reco_title, reco_points = build_recommendation(card, gauges, risk_label)
    wmap = _window_map(card)

    emoji = category_emoji(
        card.category_slug,
        trending=("🚀" in " ".join(badges)) or ("📈" in " ".join(badges)),
    )
    title_name = niche_display_name(card)
    if kind == "pepite" and card.title:
        title_name = (card.title[:80] + "…") if len(card.title) > 80 else card.title

    subtitle_parts = [
        pretty_slug(card.category_slug) if card.category_slug else "Marché",
        pretty_slug(card.brand_slug),
    ]
    if card.model_slug:
        subtitle_parts.append(pretty_slug(card.model_slug))

    # Prix / evolution pour sparklines
    price_series = [
        (wmap[w].price_median_cents / 100.0)
        if w in wmap and wmap[w].price_median_cents
        else None
        for w in ("90d", "30d", "7d", "1d")
    ]
    pop_series = [
        float(wmap[w].new_listings) if w in wmap else None
        for w in ("90d", "30d", "7d", "1d")
    ]
    rot_series = []
    for w in ("90d", "30d", "7d", "1d"):
        if w in wmap and wmap[w].median_ttl_days is not None:
            # Inverse TTL for "faster = higher"
            rot_series.append(30.0 / max(1.0, wmap[w].median_ttl_days))
        else:
            rot_series.append(None)

    w1, w7, w30, w90 = wmap.get("1d"), wmap.get("7d"), wmap.get("30d"), wmap.get("90d")
    price_now = (w1 or w7 or w30 or w90)
    price_prev = w7 if w1 else w30

    ask = card.ask_cents
    resell = card.price_median_cents
    if ask and resell and ask > 0:
        gain_eur = (resell - ask) / 100.0
        gain_line = f"**+{gain_eur:.0f} €** (~{((resell - ask) / ask) * 100:.0f} %)"
    elif card.price_p25_cents and resell:
        gain_eur = (resell - card.price_p25_cents) / 100.0
        gain_line = (
            f"**+{gain_eur:.0f} €** estimés "
            f"(achat ~{format_eur_cents(card.price_p25_cents)} → "
            f"revente ~{format_eur_cents(resell)})"
        )
    else:
        gain_line = f"Marge proxy **~{(card.margin_proxy_pct or 0):.0f} %**"

    ttl_label = (
        f"{card.median_ttl_days:.1f} j" if card.median_ttl_days is not None else "—"
    )

    description_parts = [
        f"{emoji} **{title_name}**",
        " · ".join(subtitle_parts),
        "",
        f"**Score IA**\n**{card.score:.0f} / 100**\n{score_stars(card.score)}",
        "",
        " ".join(f"`{b}`" for b in badges) if badges else "`📊 Analyse`",
        "",
        f"**Combien puis-je gagner ?**\n{gain_line}",
    ]
    description = "\n".join(description_parts)[:3900]

    fields: list[dict[str, Any]] = [
        {
            "name": "💶 Prix médian",
            "value": format_eur_cents(card.price_median_cents),
            "inline": True,
        },
        {
            "name": "📈 Évolution 7j",
            "value": (
                f"{arrow_for_change(price_now.price_median_cents if price_now else None, price_prev.price_median_cents if price_prev else None)} "
                f"{pct_change(price_now.price_median_cents if price_now else None, price_prev.price_median_cents if price_prev else None)}"
            ),
            "inline": True,
        },
        {
            "name": "⚡ Rotation",
            "value": ttl_label,
            "inline": True,
        },
        {
            "name": "📦 Annonces",
            "value": str(card.listing_count),
            "inline": True,
        },
        {
            "name": "💰 Marge estimée",
            "value": f"~{(card.margin_proxy_pct or 0):.0f} %",
            "inline": True,
        },
        {
            "name": "🎯 Taux réussite",
            "value": success_rate_estimate(card),
            "inline": True,
        },
        {
            "name": "📉 Prix min",
            "value": format_eur_cents(card.price_min_cents),
            "inline": True,
        },
        {
            "name": "📈 Prix max",
            "value": format_eur_cents(card.price_max_cents),
            "inline": True,
        },
        {
            "name": "👥 Vendeurs",
            "value": str(card.unique_sellers),
            "inline": True,
        },
        {
            "name": "📊 Jauges",
            "value": (
                f"Rentabilité\n`{gauge(gauges['rentabilite'])}`\n"
                f"Popularité\n`{gauge(gauges['popularite'])}`\n"
                f"Rareté\n`{gauge(gauges['rarete'])}`\n"
                f"Confiance IA\n`{gauge(gauges['confiance'])}`\n"
                f"Concurrence\n`{gauge(gauges['concurrence'])}`"
            )[:1024],
            "inline": False,
        },
        {
            "name": "🧠 Analyse IA",
            "value": "\n".join(f"• {line}" for line in analysis)[:1024],
            "inline": False,
        },
        {
            "name": "⏱️ Comparaison temporelle",
            "value": _temporal_table(wmap)[:1024],
            "inline": False,
        },
        {
            "name": "📉 Mini tendances",
            "value": (
                f"Prix `{sparkline(price_series)}`\n"
                f"Popularité `{sparkline(pop_series)}`\n"
                f"Rotation `{sparkline(rot_series)}`"
            ),
            "inline": False,
        },
        {
            "name": f"⚠️ Risque · {risk_label}",
            "value": "\n".join(f"• {r}" for r in risk_reasons)[:1024],
            "inline": True,
        },
        {
            "name": f"🚦 Saturation · {sat_label}",
            "value": "\n".join(sat_details)[:1024],
            "inline": True,
        },
        {
            "name": f"🎯 Recommandation · {reco_title}",
            "value": "\n".join(f"• {p}" for p in reco_points)[:1024],
            "inline": False,
        },
    ]
    if card.size:
        fields.insert(
            6,
            {"name": "📏 Taille", "value": card.size, "inline": True},
        )

    embed: dict[str, Any] = {
        "title": f"{emoji} {title_name}"[:256],
        "description": description,
        "color": score_color(card.score),
        "fields": fields[:25],
        "footer": {
            "text": (
                f"Dernière analyse · n={card.sample_size or card.listing_count} · "
                f"confiance {gauges['confiance']:.0f}% · {ENGINE_VERSION}"
            )[:2048]
        },
        "timestamp": _utcnow().isoformat(),
    }
    if card.url:
        embed["url"] = card.url
    if card.photo_url:
        embed["image"] = {"url": card.photo_url}
    return embed


def _temporal_table(wmap: dict[str, WindowPoint]) -> str:
    rows = ["```", "Fenêtre   Prix   Evol   Rot    Vol"]
    order = [("1d", "24h"), ("7d", "7j"), ("30d", "30j"), ("90d", "90j")]
    prev_price: float | None = None
    # iterate chronological for arrows: 90 -> 30 -> 7 -> 1
    chrono_prices: dict[str, float | None] = {}
    for key, _ in reversed(order):
        wp = wmap.get(key)
        chrono_prices[key] = (
            wp.price_median_cents / 100.0 if wp and wp.price_median_cents else None
        )
    # For each display row, compare to next longer window
    compare_to = {"1d": "7d", "7d": "30d", "30d": "90d", "90d": None}
    for key, label in order:
        wp = wmap.get(key)
        if not wp:
            rows.append(f"{label:<8} —      —     —      —")
            continue
        price = f"{wp.price_median_cents/100:.0f}€" if wp.price_median_cents else "—"
        ref_key = compare_to[key]
        ref = wmap.get(ref_key) if ref_key else None
        evol = arrow_for_change(
            wp.price_median_cents,
            ref.price_median_cents if ref else None,
        )
        rot = f"{wp.median_ttl_days:.0f}j" if wp.median_ttl_days is not None else "—"
        vol = str(wp.listing_count)
        rows.append(f"{label:<8} {price:<6} {evol:<5} {rot:<6} {vol}")
        prev_price = chrono_prices.get(key)
    rows.append("```")
    _ = prev_price
    return "\n".join(rows)


def build_leaderboard_embed(
    *,
    title: str,
    lines: Sequence[str],
    color: int = COLOR_BLUE,
    footer: str | None = None,
) -> dict[str, Any]:
    """Liste compacte (complément aux cartes dashboard)."""
    body = "\n".join(lines) if lines else "_Pas assez de données_"
    return {
        "title": title[:256],
        "description": body[:3900],
        "color": color,
        "footer": {
            "text": (footer or f"Classement · {ENGINE_VERSION}")[:2048]
        },
        "timestamp": _utcnow().isoformat(),
    }


def _fmt_signed_pct(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.0f} %"


def _reco_label(code: str) -> str:
    return {
        "buy": "🟢 Acheter / rechercher maintenant",
        "watch": "🟡 Surveiller",
        "wait": "🟠 Attendre",
        "avoid": "🔴 Éviter",
    }.get(code, "🟡 Surveiller")


def _lifecycle_label(code: str) -> str:
    return {
        "emergence": "🌱 Émergence — la tendance commence à apparaître",
        "growth": "📈 Croissance — la demande augmente fortement",
        "peak": "🔥 Pic — très populaire, risque de saturation",
        "decline": "📉 Déclin — la demande baisse",
        "saturation": "💀 Saturation — trop d'offre, faible potentiel",
    }.get(code, "📊 Cycle en cours d'évaluation")


def build_trend_alert_embed(trend: Any) -> dict[str, Any]:
    """Embed radar tendances — format Bloomberg marché Vinted."""
    score = float(getattr(trend, "strength", 0) or 0)
    lifecycle = str(getattr(trend, "lifecycle", "") or "")
    importance = str(getattr(trend, "importance", "") or "")
    recommendation = str(getattr(trend, "recommendation", "watch") or "watch")

    color = {
        "critical": COLOR_RED,
        "high": COLOR_GOLD,
        "growing": COLOR_GREEN,
        "weak": COLOR_BLUE,
    }.get(importance, score_color(score))
    if lifecycle == "saturation":
        color = COLOR_ORANGE
    elif lifecycle == "emergence":
        color = COLOR_PURPLE

    type_label = {
        "brand": "Marque",
        "topic": "Licence / objet / thème",
        "model": "Modèle",
        "combo": "Niche combinée",
        "keyword": "Mot-clé",
        "phrase": "Signal découvert",
    }.get(getattr(trend, "entity_type", ""), "Signal marché")

    badges = list(getattr(trend, "badges", ()) or [])
    badge_line = " ".join(f"`{b}`" for b in badges) if badges else "`📊 Radar`"

    price_med = getattr(trend, "price_median_7d", None)
    price_line = f"{price_med:.0f} €" if price_med is not None else "—"

    description = "\n".join(
        [
            f"**{trend.display_name}**",
            f"{type_label} · {_lifecycle_label(lifecycle).split('—')[0].strip()}",
            "",
            badge_line,
            "",
            f"**Score IA**\n**{score:.0f} / 100**\n{score_stars(score)}",
            "",
            f"**Évolution**\n{getattr(trend, 'why_it_matters', '—')}",
        ]
    )

    ai_lines = list(getattr(trend, "ai_analysis", ()) or [])
    if not ai_lines:
        ai_lines = [getattr(trend, "why_it_matters", "Signal composite détecté")]

    related = list(getattr(trend, "related", ()) or [])
    related_block = (
        "\n".join(f"• {name}" for name in related[:4])
        if related
        else "• Élargir via modèles / licences proches dans les annonces similaires"
    )

    samples = list(getattr(trend, "sample_titles", ()) or [])
    sample_block = "\n".join(f"• {t}" for t in samples[:3]) or "—"

    triggers = list(getattr(trend, "triggers", ()) or ())
    trigger_lines = [
        f"• **{t.label}** — {t.detail}" for t in triggers[:4]
    ] or ["• Signal composite détecté"]

    fields: list[dict[str, Any]] = [
        {
            "name": "📊 Comparaison temporelle",
            "value": (
                f"Prix : `{_fmt_signed_pct(getattr(trend, 'price_change_pct', None))}`\n"
                f"Rotation : `{_fmt_signed_pct(getattr(trend, 'rotation_change_pct', None))}`\n"
                f"Stock : `{_fmt_signed_pct(getattr(trend, 'stock_change_pct', None))}`\n"
                f"Popularité : `{_fmt_signed_pct(getattr(trend, 'popularity_change_pct', None))}`\n"
                f"Fenêtres : 24h `{trend.count_1d}` · 7j `{trend.count_7d}` · "
                f"30j `{trend.count_30d}` · 90j `{trend.count_90d}`"
            )[:1024],
            "inline": False,
        },
        {
            "name": "📦 Stats clés",
            "value": (
                f"Actives `{trend.active_count}` · "
                f"Disparitions 7j `{trend.disappeared_7d}`\n"
                f"Prix médian 7j **{price_line}**"
                + (
                    f" · 30j {trend.price_median_30d:.0f} €"
                    if getattr(trend, "price_median_30d", None) is not None
                    else ""
                )
                + (
                    f"\nTTL médian 7j `{trend.median_ttl_7d_hours:.1f}h`"
                    if getattr(trend, "median_ttl_7d_hours", None) is not None
                    else ""
                )
            )[:1024],
            "inline": False,
        },
        {
            "name": "📈 Jauges",
            "value": (
                f"Croissance\n`{gauge(getattr(trend, 'gauge_growth', 0))}`\n"
                f"Rentabilité\n`{gauge(getattr(trend, 'gauge_rentabilite', 0))}`\n"
                f"Rareté\n`{gauge(getattr(trend, 'gauge_rarity', 0))}`\n"
                f"Demande\n`{gauge(getattr(trend, 'gauge_demand', 0))}`\n"
                f"Saturation\n`{gauge(getattr(trend, 'gauge_saturation', 0))}`"
            )[:1024],
            "inline": False,
        },
        {
            "name": "🧠 Analyse IA",
            "value": (
                "Pourquoi cette tendance apparaît :\n"
                + "\n".join(f"• {line}" for line in ai_lines)
            )[:1024],
            "inline": False,
        },
        {
            "name": "🔄 Cycle de vie",
            "value": _lifecycle_label(lifecycle)[:1024],
            "inline": True,
        },
        {
            "name": "📊 Continuation",
            "value": (
                f"**{getattr(trend, 'continuation_pct', 0):.0f} %**\n"
                f"{getattr(trend, 'confidence_label', 'Confiance moyenne')}"
            )[:1024],
            "inline": True,
        },
        {
            "name": "💡 Opportunité",
            "value": str(getattr(trend, "opportunity", "—"))[:1024],
            "inline": False,
        },
        {
            "name": "🔗 À surveiller aussi",
            "value": related_block[:1024],
            "inline": False,
        },
        {
            "name": "📟 Signaux",
            "value": "\n".join(trigger_lines)[:1024],
            "inline": False,
        },
        {
            "name": "🔎 Exemples",
            "value": sample_block[:1024],
            "inline": False,
        },
        {
            "name": "🎯 Recommandation",
            "value": (
                f"**{_reco_label(recommendation)}**\n"
                f"{getattr(trend, 'recommendation_detail', '')}"
            )[:1024],
            "inline": False,
        },
    ]

    return {
        "title": f"🔥 {trend.display_name}"[:256],
        "description": description[:3900],
        "color": color,
        "fields": fields,
        "footer": {
            "text": (
                f"Radar Vinted · {trend.entity_type}:{trend.entity_key} · "
                f"{ENGINE_VERSION}"
            )[:2048]
        },
        "timestamp": _utcnow().isoformat(),
    }


def _rotation_label(rotation_change_pct: float | None) -> str:
    if rotation_change_pct is None:
        return "—"
    # TTL plus bas = plus rapide ; -50% ≈ x2
    if rotation_change_pct <= -45:
        return "x2 plus rapide"
    if rotation_change_pct <= -25:
        return "nettement plus rapide"
    if rotation_change_pct >= 25:
        return "plus lente"
    return _fmt_signed_pct(rotation_change_pct)


def build_daily_trends_board_embed(
    items: Sequence[Any],
    *,
    day: Any = None,
) -> dict[str, Any]:
    """Synthèse TOP mouvements de marché du jour."""
    day_label = str(day) if day is not None else _utcnow().date().isoformat()
    lines: list[str] = []
    for item in items:
        trend = getattr(item, "trend", item)
        medal = getattr(item, "medal", "•")
        headline = getattr(item, "headline", None) or getattr(
            trend, "title", trend.display_name
        )
        niches = list(
            getattr(trend, "associated_niches", ())
            or getattr(trend, "related", ())
            or ()
        )
        niche_preview = niches[0] if niches else "—"
        lines.append(
            f"{medal} **{headline}**\n"
            f"Score `{getattr(trend, 'strength', 0):.0f}/100` · "
            f"niche phare : {niche_preview}"
        )
    body = "\n\n".join(lines) if lines else "_Aucun mouvement qualitatif aujourd'hui_"
    return {
        "title": "🔥 TOP TENDANCES DU JOUR",
        "description": (
            f"Rapport du **{day_label}** — radar des **mouvements** de marché.\n"
            f"Pas de produits unitaires (→ salon Pépites).\n\n"
            f"{body}"
        )[:3900],
        "color": COLOR_RED,
        "footer": {
            "text": f"Mouvements marché · max {len(items)} · {ENGINE_VERSION}"[:2048]
        },
        "timestamp": _utcnow().isoformat(),
    }


def build_daily_trend_card_embed(item: Any) -> dict[str, Any]:
    """Carte mouvement de marché + niches associées."""
    trend = getattr(item, "trend", item)
    medal = getattr(item, "medal", "🔥")
    headline = getattr(item, "headline", None) or getattr(
        trend, "title", trend.display_name
    )
    narrative = getattr(item, "ai_narrative", "") or getattr(
        trend, "why_it_matters", "—"
    )
    events = list(getattr(item, "event_badges", ()) or ())
    niches = list(
        getattr(trend, "associated_niches", ())
        or getattr(trend, "related", ())
        or ()
    )
    medals = ("🥇", "🥈", "🥉", "4.", "5.")
    niche_lines = [
        f"{medals[i] if i < len(medals) else f'{i+1}.'} {name}"
        for i, name in enumerate(niches[:5])
    ] or ["• Niches encore en consolidation"]

    lifecycle = str(getattr(trend, "lifecycle", "") or "")
    sat = float(getattr(trend, "gauge_saturation", 0) or 0)
    if sat >= 65:
        concurrence = "Élevée"
    elif sat >= 40:
        concurrence = "Modérée"
    else:
        concurrence = "Faible"

    reco = _reco_label(str(getattr(trend, "recommendation", "watch")))
    fields = [
        {
            "name": "📊 Évolution du marché",
            "value": (
                f"Demande : `{_fmt_signed_pct(getattr(trend, 'popularity_change_pct', None))}`\n"
                f"Prix moyen : `{_fmt_signed_pct(getattr(trend, 'price_change_pct', None))}`\n"
                f"Rotation : `{_rotation_label(getattr(trend, 'rotation_change_pct', None))}`\n"
                f"Offre : `{_fmt_signed_pct(getattr(trend, 'stock_change_pct', None))}`"
            )[:1024],
            "inline": False,
        },
        {
            "name": "🧠 Analyse IA",
            "value": f'"{narrative}"'[:1024],
            "inline": False,
        },
        {
            "name": "🔎 Niches associées",
            "value": "\n".join(niche_lines)[:1024],
            "inline": False,
        },
        {
            "name": "🎯 Opportunité",
            "value": (
                f"Potentiel : **{getattr(trend, 'strength', 0):.0f}/100**\n"
                f"Concurrence : **{concurrence}**\n"
                f"Cycle : **{lifecycle or '—'}**\n"
                f"{getattr(trend, 'opportunity', '')}\n\n"
                f"**{reco}**\n{getattr(trend, 'recommendation_detail', '')}"
            )[:1024],
            "inline": False,
        },
    ]
    event_line = " ".join(f"`{e}`" for e in events) if events else "`📊 Mouvement`"
    return {
        "title": f"{medal} {headline}"[:256],
        "description": (
            f"**Mouvement :** {trend.display_name}\n"
            f"**Score :** `{getattr(trend, 'strength', 0):.0f}/100` "
            f"{score_stars(float(getattr(trend, 'strength', 0) or 0))}\n"
            f"{event_line}"
        )[:3900],
        "color": score_color(float(getattr(trend, "strength", 0) or 0)),
        "fields": fields,
        "footer": {
            "text": (
                f"Radar marché · macro:{trend.entity_key} · {ENGINE_VERSION}"
            )[:2048]
        },
        "timestamp": _utcnow().isoformat(),
    }


def build_stats_dashboard_embed(stats: dict[str, Any]) -> dict[str, Any]:
    brand_lines = [
        f"`{i:02d}` **{pretty_slug(n)}** · `{s:.0f}/100` · vol {c}"
        for i, (n, s, c) in enumerate((stats.get("top_brands") or [])[:5], 1)
    ]
    cat_lines = [
        f"`{i:02d}` **{pretty_slug(n)}** · `{s:.0f}/100`"
        for i, (n, s, _) in enumerate((stats.get("top_categories") or [])[:5], 1)
    ]
    description = (
        f"**Que se passe-t-il ?** Marché Vinted — vue d'ensemble\n\n"
        f"📦 **{stats.get('listings_total', 0)}** annonces analysées "
        f"({stats.get('listings_active', 0)} actives)\n"
        f"🆕 **{stats.get('listings_new_24h', 0)}** nouvelles / 24h\n"
        f"🏷️ **{stats.get('brands', 0)}** marques · "
        f"👟 **{stats.get('models', 0)}** modèles · "
        f"💎 **{stats.get('niches_scored', 0)}** niches scorées\n"
    )
    fields = [
        {
            "name": "🏆 Top marques",
            "value": "\n".join(brand_lines) or "—",
            "inline": False,
        },
        {
            "name": "📂 Catégories dynamiques",
            "value": "\n".join(cat_lines) or "—",
            "inline": False,
        },
        {
            "name": "🎯 Action",
            "value": (
                "Ouvre **Pépites** pour les achats immédiats, "
                "**Tendances** pour anticiper, "
                "**Classements** pour les meilleures niches."
            ),
            "inline": False,
        },
    ]
    return {
        "title": "📈 Tableau de bord marché",
        "description": description[:3900],
        "color": COLOR_PURPLE,
        "fields": fields,
        "footer": {"text": f"Statistiques · {ENGINE_VERSION}"},
        "timestamp": _utcnow().isoformat(),
    }
