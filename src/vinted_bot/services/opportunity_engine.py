"""Détecteur de niches — études de marché achat-revente (pas d'annonces unitaires)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from vinted_bot.db.models import Listing, NicheSnapshot
from vinted_bot.db.repositories import get_checkpoint, set_checkpoint
from vinted_bot.db.session import session_scope
from vinted_bot.notify.discord import normalize_brand
from vinted_bot.services.market_embeds import (
    COLOR_GOLD,
    COLOR_GREEN,
    COLOR_ORANGE,
    COLOR_PURPLE,
    COLOR_RED,
    ENGINE_VERSION,
    gauge,
    score_stars,
    _reco_label,
    _utcnow,
)
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

_BROAD_BRANDS = frozenset(
    {
        "nike",
        "adidas",
        "jordan",
        "puma",
        "reebok",
        "zara",
        "h_m",
        "hm",
        "uniqlo",
        "shein",
    }
)
_VAGUE_LABELS = frozenset(
    {
        "sac",
        "vintage",
        "chaussure",
        "chaussures",
        "peluche",
        "nike",
        "adidas",
        "sneakers",
        "vetement",
        "veste",
        "hoodie",
    }
)
# Marques très connues : OK seulement avec modèle précis
_FAMOUS_NEED_MODEL = frozenset(
    {
        "nike",
        "adidas",
        "jordan",
        "yeezy",
        "louis_vuitton",
        "gucci",
        "prada",
        "chanel",
        "hermes",
    }
)

MIN_OPPORTUNITY_SCORE = 55.0
# Seuil publication Discord (pipeline permanent) — uniquement opportunités intéressantes
PUBLISH_MIN_SCORE = 65.0
MAX_OPPORTUNITIES_POSTED = 8
# Une niche = ensemble de produits similaires — jamais 1 seule annonce
MIN_NICHE_LISTINGS = 5
MIN_NICHE_SELLERS = 2
# Publication Discord : échantillon plus solide (pas un micro-cluster)
MIN_PUBLISH_LISTINGS = 8
MIN_BRAND_CATEGORY_N = 8
MIN_PRODUCT_OBJECT_N = 5
POSTED_NICHES_CHECKPOINT = "market:opp:posted_keys"
POSTED_BOARD_HASH_CHECKPOINT = "market:opp:board_hash"
# Ne jamais republier la même niche trop tôt
POSTED_DOWNRANK_HOURS = 96.0
POSTED_NAME_COOLDOWN_HOURS = 72.0
MAX_POSTED_KEYS_KEPT = 200

# Catégories hors mode fashion (aligné market_categories.yaml)
_OBJECT_CATEGORIES = frozenset(
    {
        "sac",
        "peluche",
        "jouet",
        "decoration",
        "electronique",
        "appareil_photo",
        "collection",
        "livre",
        "objet",
        "bijoux",
        "montre",
        "sport",
        "bebe",
        "beaute",
        "musique",
        "jeux_video",
        "maison",
    }
)
_LICENSE_FLAG_TOKENS = frozenset(
    {
        "pokemon",
        "lego",
        "harry_potter",
        "disney",
        "star_wars",
        "sanrio",
        "studio_ghibli",
        "funko",
        "argentique",
        "polaroid",
        "vinyl",
    }
)
# Marques "connues" — pas de boost valeur cachée
_KNOWN_BRANDS = _FAMOUS_NEED_MODEL | _BROAD_BRANDS | frozenset(
    {
        "stone_island",
        "the_north_face",
        "carhartt",
        "ralph_lauren",
        "louis_vuitton",
        "supreme",
        "stussy",
        "lacoste",
        "tommy_hilfiger",
        "moncler",
        "canada_goose",
    }
)


@dataclass(slots=True, frozen=True)
class Opportunity:
    niche_key: str
    name: str
    score: float
    niche_type: str  # high_value | undervalued | hidden | emerging | high_rotation
    niche_type_label: str
    priority: str
    priority_label: str
    badges: tuple[str, ...]
    # Prix
    price_buy_avg_eur: float | None
    price_resell_avg_eur: float | None
    price_max_eur: float | None
    price_buy_max_eur: float | None
    price_resell_target_eur: float | None
    margin_eur: float | None
    margin_pct: float | None
    # Jauges /100
    demand_score: float
    rarity_score: float
    competition_score: float  # haut = pire
    rotation_score: float
    supply_ease_score: float
    price_stability_score: float
    confidence: float
    # Faits marché
    unique_sellers: int
    disappeared_pct: float | None
    median_ttl_days: float | None
    price_p75_eur: float | None
    facts_line: str
    # Multi-angle
    multi_angle_composite: float
    multi_angle_block: str
    signals: tuple[str, ...]
    angle_demand: float
    angle_supply: float
    angle_price: float
    angle_behavioral: float
    angle_emerging: float
    angle_profitability: float
    angle_anomaly: float
    # Cycle / profondeur / confiance / international (12–18)
    lifecycle: str
    lifecycle_label: str
    lifecycle_avoid: bool
    depth_summary: str
    weak_signal: bool
    weak_signal_summary: str
    confidence_label: str
    international: bool
    international_summary: str
    explain_why: str
    explain_signals: str
    explain_strategy: str
    # Texte
    why_short: str
    ai_analysis: str
    strategy_where: str
    strategy_buy: str
    strategy_sell: str
    action: str
    action_detail: str
    photo_url: str | None
    brand_slug: str | None
    model_slug: str | None
    category_slug: str | None
    keyword_flags: str
    search_terms: tuple[str, ...]
    sample_size: int
    listing_count: int
    disappeared_count: int


def _flag_tokens(flags: str) -> set[str]:
    if not flags:
        return set()
    return {p.strip().lower() for p in flags.replace(",", "+").split("+") if p.strip()}


def _norm_brand_key(brand: str | None) -> str:
    return (brand or "").strip().lower().replace(" ", "_")


def _is_obscure_brand(brand: str | None) -> bool:
    b = _norm_brand_key(brand)
    if not b or b in {"inconnu", "unknown"}:
        return True
    return b not in _KNOWN_BRANDS and b not in {
        _norm_brand_key(x) for x in _FAMOUS_NEED_MODEL
    } and b not in {_norm_brand_key(x) for x in _BROAD_BRANDS}


def has_sufficient_market_sample(snap: NicheSnapshot | Any) -> bool:
    """True si l'échantillon est un ensemble (jamais 1 annonce / 1 vendeur)."""
    n = int(getattr(snap, "listing_count", 0) or 0)
    if n < MIN_NICHE_LISTINGS:
        return False
    sellers = getattr(snap, "unique_sellers", None)
    if sellers is not None and int(sellers or 0) < MIN_NICHE_SELLERS:
        return False
    return True


def is_granular_niche(snap: NicheSnapshot | Any) -> bool:
    """Niche actionnable : modèle, licence, objet, ou marque×catégorie (pas marque seule).

    Toujours sur un ensemble d'annonces similaires (≥ MIN_NICHE_LISTINGS).
    """
    brand = (getattr(snap, "brand_slug", None) or "").strip().lower()
    model = (getattr(snap, "model_slug", None) or "").strip()
    flags = (getattr(snap, "keyword_flags", None) or "").strip()
    category = (getattr(snap, "category_slug", None) or "").strip()
    n = int(getattr(snap, "listing_count", 0) or 0)
    tokens = _flag_tokens(flags)

    # Règle dure : pas d'analyse niche sur un article isolé
    if n < MIN_NICHE_LISTINGS:
        return False
    if not has_sufficient_market_sample(snap):
        return False

    if brand in _VAGUE_LABELS and not model and not flags and category not in _OBJECT_CATEGORIES:
        return False
    # Marque absente : OK seulement avec modèle / licence / objet
    if brand in {"", "inconnu", "unknown"} and not model and not flags:
        if category not in _OBJECT_CATEGORIES or n < MIN_PRODUCT_OBJECT_N:
            return False
    # Ultra-marques fashion : modèle obligatoire
    if brand in _BROAD_BRANDS and not model:
        return False
    if brand in _FAMOUS_NEED_MODEL and not model:
        return False
    # Niveau produit / modèle (prioritaire) — volume déjà validé
    if model:
        return True
    # Licence / collection / produit détecté dans les flags
    if tokens & _LICENSE_FLAG_TOKENS:
        return True
    # Flags riches même sans marque connue (inconnu + style/licence)
    if flags and len(flags) >= 4 and brand not in _BROAD_BRANDS:
        return True
    # Objet / jouet / déco / électronique : catégorie × (marque obscure|inconnu) avec volume
    if category in _OBJECT_CATEGORIES and n >= MIN_PRODUCT_OBJECT_N:
        if brand in {"", "inconnu"} or _is_obscure_brand(brand):
            return True
        if brand and brand not in _BROAD_BRANDS:
            return True
    # Marque × catégorie (hors ultra-marques) avec volume minimal
    if (
        brand
        and brand not in {"inconnu", ""}
        and category
        and brand not in _BROAD_BRANDS
        and brand not in _FAMOUS_NEED_MODEL
        and n >= MIN_BRAND_CATEGORY_N
    ):
        return True
    if category and not model and not flags:
        return False
    return False


def opportunity_priority(score: float) -> tuple[str, str]:
    if score >= 85:
        return "exceptional", "🥇 Opportunité exceptionnelle"
    if score >= 70:
        return "strong", "🥈 Forte opportunité"
    if score >= MIN_OPPORTUNITY_SCORE:
        return "interesting", "🥉 Opportunité intéressante"
    return "weak", "👀 Signal faible"


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _compute_gauges(
    snap: NicheSnapshot,
    *,
    w7: Any | None,
    w30: Any | None,
) -> dict[str, float]:
    listing_count = float(snap.listing_count or 0)
    disappeared = float(snap.disappeared_count or 0)
    sellers = float(snap.unique_sellers or 0)
    vol7 = float((w7.new_listings if w7 else snap.new_listings) or 0)
    vol30 = float((w30.new_listings if w30 else snap.new_listings) or 0)
    if vol30 <= 0:
        vol30 = float(snap.listing_count or 0)
    expected = max(1.0, vol30 / 4.0)
    demand_ratio = vol7 / expected if expected else 1.0
    dis_ratio = disappeared / max(1.0, listing_count)
    has_liquidity = disappeared >= 2 or snap.median_ttl_days is not None

    # Demande : flux relatif + disparitions (sans inventer si liquidité absente)
    demand = _clamp(
        min(45.0, demand_ratio * 22.0)
        + (min(40.0, dis_ratio * 80.0) if has_liquidity else min(12.0, vol7 * 1.5))
        + min(15.0, vol7 * 1.2)
    )

    # Rareté : stock bas + peu de vendeurs
    if listing_count <= 8:
        rarity = 88.0
    elif listing_count <= 15:
        rarity = 72.0
    elif listing_count <= 30:
        rarity = 55.0
    elif listing_count <= 60:
        rarity = 38.0
    else:
        rarity = 18.0
    if 0 < sellers <= 5:
        rarity = _clamp(rarity + 12)
    elif sellers == 0:
        rarity = _clamp(rarity - 8)  # vendeurs inconnus → moins de confiance rareté

    # Concurrence (haut = mauvais)
    if sellers > 0:
        competition = _clamp(sellers * 6.0 + listing_count * 0.45)
    else:
        competition = _clamp(listing_count * 0.8 + 25.0)

    # Rotation : bas / inconnu si pas de TTL ni disparitions (plus de faux 35)
    ttl = snap.median_ttl_days
    if ttl is not None and disappeared >= 2:
        rotation = _clamp(
            100.0 - min(90.0, float(ttl) * 8.0) + min(20.0, disappeared * 3)
        )
    elif disappeared >= 3:
        rotation = _clamp(45.0 + disappeared * 5.0)
    elif disappeared >= 1:
        rotation = 28.0
    else:
        rotation = 12.0

    # Facilité d'approvisionnement
    if 8 <= listing_count <= 40:
        supply = 72.0
    elif listing_count < 5:
        supply = 35.0
    elif listing_count < 8:
        supply = 48.0
    else:
        supply = _clamp(90.0 - listing_count * 0.5)

    # Stabilité prix
    pmin = snap.price_min_cents or 0
    pmax = snap.price_max_cents or 0
    pmed = snap.price_median_cents or 0
    if pmed > 0 and pmax > 0:
        spread = (pmax - pmin) / pmed
        stability = _clamp(100.0 - spread * 40.0)
    else:
        stability = 50.0

    # Confiance : volume + vendeurs connus + liquidité observée
    confidence = 25.0
    confidence += min(35.0, listing_count * 2.5)
    if sellers > 0:
        confidence += min(20.0, sellers * 3.0)
    if has_liquidity:
        confidence += 20.0
    if snap.median_ttl_days is not None:
        confidence += 10.0
    if listing_count < MIN_PUBLISH_LISTINGS:
        confidence = min(confidence, 50.0)
    if sellers < MIN_NICHE_SELLERS:
        confidence = min(confidence, 40.0)

    return {
        "demand": round(demand, 1),
        "rarity": round(rarity, 1),
        "competition": round(competition, 1),
        "rotation": round(rotation, 1),
        "supply_ease": round(supply, 1),
        "price_stability": round(stability, 1),
        "confidence": round(_clamp(confidence), 1),
    }


def _composite_score(
    *,
    margin_pct: float | None,
    gauges: dict[str, float],
    niche_type: str,
    listing_count: int,
    brand: str | None = None,
    median_eur: float | None = None,
    has_model: bool = False,
) -> float:
    # Marge moins crédible sur petits échantillons
    margin_raw = _clamp((margin_pct or 0) * 0.7)
    if listing_count < 8:
        margin_raw *= 0.55
    elif listing_count < 12:
        margin_raw *= 0.75

    anti_comp = 100.0 - gauges["competition"]
    score = (
        margin_raw * 0.26
        + gauges["demand"] * 0.18
        + gauges["rarity"] * 0.14
        + gauges["rotation"] * 0.12
        + gauges["supply_ease"] * 0.10
        + gauges["price_stability"] * 0.08
        + anti_comp * 0.12
    )
    # Pénalité / boost confiance
    conf = gauges.get("confidence", 50.0)
    score *= 0.70 + 0.30 * (conf / 100.0)

    if niche_type == "hidden":
        score *= 1.10
    elif niche_type == "emerging":
        score *= 1.07
    elif niche_type == "undervalued":
        score *= 1.04
    elif niche_type == "high_rotation":
        score *= 1.03

    # Valeur cachée : marque obscure + demande + ticket / marge élevés
    if _is_obscure_brand(brand) and gauges["demand"] >= 45:
        score *= 1.08
        if (median_eur or 0) >= 80 or (margin_pct or 0) >= 50:
            score *= 1.05
    # Analyse produit (modèle précis) > marque générique
    if has_model:
        score *= 1.03
    # Pénalité légère marques saturées déjà connues
    if (brand or "") in _FAMOUS_NEED_MODEL:
        score *= 0.96

    return round(_clamp(score), 1)


def _classify_niche(
    *,
    margin_pct: float | None,
    gauges: dict[str, float],
    listing_count: int,
    demand_delta: float | None,
    median_eur: float | None,
    brand: str | None,
    disappeared: int,
    has_liquidity: bool,
    has_model: bool = False,
    category: str | None = None,
    flags: str = "",
) -> tuple[str, str]:
    margin = margin_pct or 0
    famous = (brand or "") in _FAMOUS_NEED_MODEL
    obscure = _is_obscure_brand(brand)
    license_hit = bool(_flag_tokens(flags) & _LICENSE_FLAG_TOKENS)
    object_cat = (category or "") in _OBJECT_CATEGORIES

    # Ordre exclusif — un seul type dominant
    # Valeur cachée : notoriété faible mais demande / ticket élevés
    if (
        obscure
        and gauges["demand"] >= 48
        and gauges["competition"] <= 45
        and ((median_eur or 0) >= 70 or margin >= 50)
        and gauges.get("confidence", 0) >= 40
    ):
        return "hidden", "💎 Valeur cachée — demande > notoriété"
    if has_liquidity and gauges["rotation"] >= 65 and gauges["demand"] >= 45:
        return "high_rotation", "⚡ Forte rotation (part vite)"
    if (
        (demand_delta or 0) >= 40
        and gauges["demand"] >= 55
        and (obscure or license_hit or object_cat)
    ):
        return "emerging", "🆕 Produit / licence émergente"
    if (demand_delta or 0) >= 45 and gauges["demand"] >= 55:
        return "emerging", "🆕 Produit / marque émergente"
    if (
        not famous
        and listing_count <= 22
        and gauges["competition"] <= 38
        and gauges["demand"] >= 40
        and gauges.get("confidence", 0) >= 40
    ):
        return "hidden", "💎 Niche peu connue / sous-exploitée"
    if margin >= 55 and (median_eur or 0) < 200 and listing_count >= 6:
        return "undervalued", "💰 Fort écart achat / revente"
    if (median_eur or 0) >= 120 and margin >= 35:
        return "high_value", "👑 Forte valeur de revente"
    if margin >= 45 and listing_count >= 8:
        return "undervalued", "💰 Fort écart achat / revente"
    if not famous and gauges["competition"] <= 32 and listing_count <= 25:
        return "hidden", "💎 Niche peu connue / sous-exploitée"
    if disappeared == 0 and gauges["demand"] >= 60 and (has_model or obscure):
        return "emerging", "🆕 Signal de volume récent"
    return "high_value", "📊 Marché rentable"


def _badges_for(
    *,
    niche_type: str,
    gauges: dict[str, float],
    margin_pct: float | None,
    brand: str | None,
    disappeared: int,
    demand_delta: float | None,
) -> tuple[str, ...]:
    badges: list[str] = []
    # Signaux les plus discriminants d'abord (cap 4)
    if gauges["rotation"] >= 65 and disappeared >= 2:
        badges.append("⚡ Rotation rapide")
    if gauges["demand"] >= 70 and disappeared >= 2:
        badges.append("🔥 Forte demande")
    elif gauges["demand"] >= 70:
        badges.append("📡 Volume actif")
    if (margin_pct or 0) >= 55:
        badges.append("💰 Forte marge")
    if niche_type == "hidden" and _is_obscure_brand(brand):
        badges.append("💎 Valeur cachée")
    elif niche_type == "hidden" or (
        _is_obscure_brand(brand)
        and gauges["competition"] <= 35
        and gauges.get("confidence", 0) >= 45
    ):
        badges.append("💎 Peu connue")
    if niche_type == "emerging" or (demand_delta or 0) >= 40:
        badges.append("📈 En croissance")
    if gauges["rarity"] >= 75 and (gauges.get("confidence") or 0) >= 50:
        badges.append("🧊 Stock limité")
    if (gauges.get("confidence") or 0) < 45:
        badges.append("⚠️ Confiance limitée")
    return tuple(dict.fromkeys(badges))[:4] or ("📊 Analyse marché",)


def _facts_line(
    *,
    listing_count: int,
    sellers: int,
    buy: float | None,
    median: float | None,
    p75: float | None,
    disappeared: int,
    disappeared_pct: float | None,
    ttl: float | None,
    confidence: float,
) -> str:
    parts: list[str] = [f"n={listing_count}"]
    if sellers > 0:
        parts.append(f"{sellers} vendeur{'s' if sellers > 1 else ''}")
    else:
        parts.append("vendeurs n/c")
    if buy is not None:
        parts.append(f"P25 {buy:.0f}€")
    if median is not None:
        parts.append(f"médiane {median:.0f}€")
    if p75 is not None:
        parts.append(f"P75 {p75:.0f}€")
    if disappeared > 0 and disappeared_pct is not None:
        parts.append(f"disparues {disappeared_pct:.0f}%")
    if ttl is not None:
        parts.append(f"TTL ~{ttl:.1f}j")
    parts.append(f"confiance {confidence:.0f}%")
    return " · ".join(parts)


def _why_one_liner(
    niche_type: str,
    *,
    gauges: dict[str, float],
    margin_pct: float | None,
    disappeared: int,
    demand_delta: float | None,
    listing_count: int,
    sellers: int,
) -> str:
    parts: list[str] = []
    if gauges["demand"] >= 65 and disappeared >= 2:
        parts.append("demande confirmée (liquidité)")
    elif gauges["demand"] >= 60:
        parts.append(f"flux récent ({listing_count} annonces)")
    if gauges["rarity"] >= 70 and sellers > 0 and sellers <= 6:
        parts.append(f"stock concentré ({sellers} vendeurs)")
    elif gauges["rarity"] >= 70:
        parts.append("offre limitée")
    if (margin_pct or 0) >= 50:
        parts.append(f"écart prix ~{margin_pct:.0f}%")
    if gauges["rotation"] >= 65 and disappeared >= 2:
        parts.append("rotation rapide")
    if niche_type == "hidden":
        parts.append("faible concurrence")
    if niche_type == "emerging" or (demand_delta or 0) >= 40:
        parts.append("signal émergent")
    if gauges.get("confidence", 100) < 45:
        parts.append("échantillon encore mince")
    if not parts:
        parts.append("indicateurs marché favorables")
    return " + ".join(parts[:3])


def _ai_analysis(
    name: str,
    *,
    niche_type: str,
    gauges: dict[str, float],
    margin_eur: float | None,
    buy: float | None,
    resell: float | None,
    category: str | None,
    facts_line: str,
    disappeared: int,
    demand_delta: float | None,
    listing_count: int,
    sellers: int,
    ttl: float | None,
) -> str:
    buyer = {
        "high_value": "acheteurs premium / collectionneurs",
        "undervalued": "revendeurs et acheteurs mal informés du prix marché",
        "hidden": "communautés spécialisées encore peu visibles",
        "emerging": "early adopters et chasseurs de tendances",
        "high_rotation": "acheteurs impulsifs / besoin immédiat",
    }.get(niche_type, "acheteurs actifs Vinted")

    sentences: list[str] = [
        f"« {name} »"
        + (f" ({category})" if category else "")
        + f" attire surtout {buyer}."
    ]
    sentences.append(f"Faits : {facts_line}.")

    if disappeared >= 2:
        sentences.append(
            f"Liquidité observée : {disappeared} disparition(s) sur la fenêtre — "
            "proxy de rotation (vente ou retrait)."
        )
    elif gauges["demand"] >= 55:
        sentences.append(
            f"Volume suivi : {listing_count} annonces en fenêtre, "
            "sans disparitions encore mesurées — la demande reste à confirmer."
        )
    else:
        sentences.append(
            "Demande encore modérée sur le corpus actuel ; surveiller le flux."
        )

    if sellers > 0:
        if sellers <= 5:
            sentences.append(
                f"Offre concentrée ({sellers} vendeur(s) connus) : "
                "moins de concurrence directe."
            )
        else:
            sentences.append(
                f"{sellers} vendeurs distincts : être sélectif sur le prix d'entrée."
            )

    if ttl is not None:
        sentences.append(f"TTL médian observé ~{ttl:.1f} jour(s).")

    if (demand_delta or 0) >= 40:
        sentences.append(
            f"Accélération du flux 7j vs tendance 30j (~+{demand_delta:.0f} %)."
        )

    if buy and resell and margin_eur:
        sentences.append(
            f"Cible : acheter ≤ {buy:.0f} € pour viser ~{resell:.0f} € "
            f"(+{margin_eur:.0f} € brut avant frais)."
        )

    if gauges.get("confidence", 100) < 45:
        sentences.append(
            "Confiance limitée : échantillon ou historique liquidité encore faible."
        )

    return " ".join(sentences)


def _search_terms(snap: NicheSnapshot, name: str) -> tuple[str, ...]:
    terms: list[str] = []
    if snap.brand_slug:
        terms.append(snap.brand_slug.replace("_", " ").title())
    if snap.model_slug:
        terms.append(snap.model_slug.replace("_", " ").title())
    elif snap.category_slug:
        terms.append(snap.category_slug.replace("_", " ").title())
    if snap.keyword_flags:
        terms.extend(
            p.replace("_", " ") for p in snap.keyword_flags.split("+") if p
        )
    for tok in name.replace("·", " ").split():
        if len(tok) >= 4 and tok.lower() not in {t.lower() for t in terms}:
            terms.append(tok)
    return tuple(dict.fromkeys(terms))[:8]


_COLOR_TOKENS = (
    ("marron", "marron"),
    ("brown", "marron"),
    ("noir", "noir"),
    ("black", "noir"),
    ("beige", "beige"),
    ("vert", "vert"),
    ("olive", "olive"),
    ("argent", "argentée"),
    ("silver", "argentée"),
    ("bordeaux", "bordeaux"),
    ("bleu", "bleu"),
    ("navy", "navy"),
)
_ERA_TOKENS = (
    ("années 2000", "années 2000"),
    ("annees 2000", "années 2000"),
    ("2000s", "années 2000"),
    ("y2k", "Y2K"),
    ("années 90", "années 90"),
    ("annees 90", "années 90"),
    ("90s", "années 90"),
    ("années 80", "années 80"),
    ("annees 80", "années 80"),
)


def _enrich_name_from_titles(base: str, titles: list[str]) -> str:
    """Ajoute couleur / époque fréquentes pour un nom de niche actionnable."""
    if not titles:
        return base
    blob = " ".join(titles).lower()
    extras: list[str] = []
    for needle, label in _ERA_TOKENS:
        if needle in blob and label.lower() not in base.lower():
            extras.append(label)
            break
    for needle, label in _COLOR_TOKENS:
        if needle in blob and label.lower() not in base.lower():
            extras.append(label)
            break
    if not extras:
        return base
    return f"{base} {' '.join(extras[:2])}".strip()


def _sample_titles_for_snap(snap: NicheSnapshot, *, limit: int = 12) -> list[str]:
    brand = (snap.brand_slug or "").strip().lower()
    loose_brand = brand in {"", "inconnu", "unknown"}
    with session_scope() as session:
        stmt = (
            select(Listing)
            .where(Listing.is_active.is_(True))
            .order_by(Listing.last_seen_at.desc().nullslast())
            .limit(200)
        )
        if snap.model_slug:
            stmt = stmt.where(Listing.model_slug == snap.model_slug)
        elif snap.category_slug:
            stmt = stmt.where(Listing.category_slug == snap.category_slug)
        titles: list[str] = []
        for listing in session.scalars(stmt).all():
            listing_brand = normalize_brand(listing.brand) or "inconnu"
            if not loose_brand and listing_brand != brand:
                continue
            if listing.title:
                titles.append(listing.title)
            if len(titles) >= limit:
                break
        return titles


def _label(snap: NicheSnapshot, *, titles: list[str] | None = None) -> str:
    """Nom actionnable : produit / licence d'abord, marque en second si utile."""
    brand_raw = _norm_brand_key(snap.brand_slug)
    brand = brand_raw.replace("_", " ").title()
    unknown = brand_raw in {"", "inconnu", "unknown", "?"}
    parts: list[str] = []

    if snap.model_slug:
        model = snap.model_slug.replace("_", " ").title()
        if unknown or model.lower().startswith(brand.lower()):
            parts = [model]
        else:
            parts = [brand, model]
    else:
        license_tokens = sorted(
            _flag_tokens(snap.keyword_flags or "") & _LICENSE_FLAG_TOKENS
        )
        if license_tokens:
            lic = license_tokens[0].replace("_", " ").title()
            if snap.category_slug:
                parts = [lic, snap.category_slug.replace("_", " ").title()]
            else:
                parts = [lic]
        elif not unknown:
            # Toujours garder la marque pour les niches marque×catégorie
            parts = [brand]
            if snap.keyword_flags:
                parts.append(
                    snap.keyword_flags.replace("+", " ").replace("_", " ").title()
                )
            elif snap.category_slug:
                parts.append(snap.category_slug.replace("_", " ").title())
        elif snap.keyword_flags:
            parts = [
                snap.keyword_flags.replace("+", " ").replace("_", " ").title()
            ]
            if snap.category_slug:
                parts.append(snap.category_slug.replace("_", " ").title())
        elif snap.category_slug and snap.category_slug not in _VAGUE_LABELS:
            parts = [snap.category_slug.replace("_", " ").title()]

    name = " ".join(parts).strip()
    if not name or name.lower() in _VAGUE_LABELS:
        return ""
    flags = (snap.keyword_flags or "").lower()
    extras: list[str] = []
    for token in (
        "vintage",
        "archive",
        "y2k",
        "gore_tex",
        "deadstock",
        "pokemon",
        "argentique",
    ):
        if token in flags and token.replace("_", " ") not in name.lower():
            extras.append(token.replace("_", " ").title())
    if extras:
        name = f"{name} {' '.join(extras[:2])}"
    return _enrich_name_from_titles(name, titles or [])


def _prefer_large_image_url(url: str) -> str:
    """Pousse une URL Vinted vers une taille plus grande si possible."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return u
    # Remplace les tailles thumb courantes par f800 (grand format Discord)
    for small in ("/f50/", "/f100/", "/f200/", "/f300/", "/f400/", "/thumbs/"):
        if small in u:
            return u.replace(small, "/f800/")
    return u


def _photo_candidates_from_listing(listing: Listing) -> list[str]:
    """Collecte les URLs photo d'une annonce (DB + raw_json), grande taille d'abord."""
    urls: list[str] = []
    raw = listing.raw_json if isinstance(listing.raw_json, dict) else {}
    photo = raw.get("photo") if isinstance(raw, dict) else None
    if isinstance(photo, dict):
        for key in ("full_size_url", "url", "high_resolution_url"):
            val = photo.get(key)
            if val:
                urls.append(str(val))
        hr = photo.get("high_resolution")
        if isinstance(hr, dict) and hr.get("url"):
            urls.append(str(hr["url"]))
        elif isinstance(hr, list):
            for thumb in hr:
                if isinstance(thumb, dict) and thumb.get("url"):
                    urls.append(str(thumb["url"]))
    for p in raw.get("photos") or [] if isinstance(raw, dict) else []:
        if isinstance(p, dict) and p.get("url"):
            urls.append(str(p["url"]))
    for photo_row in listing.photos or []:
        if getattr(photo_row, "url", None):
            urls.append(str(photo_row.url))
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        big = _prefer_large_image_url(u)
        if big.startswith("http") and big not in seen:
            seen.add(big)
            out.append(big)
    return out


def _listing_matches_niche(listing: Listing, snap: NicheSnapshot) -> bool:
    brand = (snap.brand_slug or "").strip().lower()
    loose_brand = brand in {"", "inconnu", "unknown"}
    listing_brand = normalize_brand(listing.brand) or "inconnu"
    if not loose_brand and listing_brand != brand:
        return False
    if snap.model_slug and listing.model_slug == snap.model_slug:
        return True
    if snap.model_slug:
        token = snap.model_slug.replace("_", " ").lower()
        title = (listing.title or "").lower()
        if token and token in title:
            return True
        # tokens significatifs du modèle
        parts = [p for p in snap.model_slug.split("_") if len(p) >= 3]
        if parts and all(p in title for p in parts[:2]):
            return True
        return False
    if snap.category_slug and listing.category_slug == snap.category_slug:
        return True
    flags = (snap.keyword_flags or "").strip()
    if flags:
        title = f" {(listing.title or '').lower()} "
        for tok in flags.replace(",", "+").split("+"):
            t = tok.strip().lower().replace("_", " ")
            if len(t) >= 3 and t in title:
                return True
    return bool(snap.category_slug) and listing.category_slug == snap.category_slug


def _find_photo(snap: NicheSnapshot) -> str | None:
    """Image de référence = une photo réelle parmi les annonces de la niche."""
    brand = (snap.brand_slug or "").strip().lower()
    loose_brand = brand in {"", "inconnu", "unknown"}
    with session_scope() as session:
        stmt = (
            select(Listing)
            .options(selectinload(Listing.photos))
            .where(Listing.is_active.is_(True))
            .order_by(Listing.last_seen_at.desc().nullslast())
            .limit(180)
        )
        if snap.model_slug:
            model_like = f"%{snap.model_slug.replace('_', '%')}%"
            stmt = stmt.where(
                or_(
                    Listing.model_slug == snap.model_slug,
                    Listing.title.ilike(model_like),
                )
            )
        elif snap.category_slug:
            stmt = stmt.where(Listing.category_slug == snap.category_slug)
        rows = list(session.scalars(stmt).unique().all())
        # 1) match strict niche
        for listing in rows:
            if not _listing_matches_niche(listing, snap):
                continue
            cands = _photo_candidates_from_listing(listing)
            if cands:
                return cands[0]
        # 2) fallback marque + photo
        if not loose_brand:
            for listing in rows:
                listing_brand = normalize_brand(listing.brand) or "inconnu"
                if listing_brand != brand:
                    continue
                cands = _photo_candidates_from_listing(listing)
                if cands:
                    return cands[0]
        # 3) dernier recours : première photo du lot filtré
        for listing in rows:
            cands = _photo_candidates_from_listing(listing)
            if cands:
                return cands[0]
    return None


def _p75_from_snap(snap: NicheSnapshot) -> float | None:
    metrics = snap.metrics if isinstance(getattr(snap, "metrics", None), dict) else {}
    raw = metrics.get("price_p75_cents") if metrics else None
    if raw is None:
        return None
    try:
        return float(raw) / 100.0
    except (TypeError, ValueError):
        return None


def snapshot_to_opportunity(
    snap: NicheSnapshot,
    *,
    windows: dict[str, Any] | None = None,
    listings_analyzed: int = 0,
    allow_avoid_lifecycle: bool = False,
    for_fiche: bool = False,
) -> Opportunity | None:
    """Analyse poussée d'une niche = ensemble de produits similaires (jamais 1 annonce)."""
    if for_fiche:
        allow_avoid_lifecycle = True
    if not is_granular_niche(snap):
        return None

    listing_count = int(snap.listing_count or 0)
    sellers = int(snap.unique_sellers or 0)
    # Garde-fou redondant : échantillon marché obligatoire
    if listing_count < MIN_NICHE_LISTINGS or sellers < MIN_NICHE_SELLERS:
        return None

    titles = _sample_titles_for_snap(snap)
    name = _label(snap, titles=titles)
    if not name:
        return None

    w = windows or {}
    w7, w30 = w.get("7d"), w.get("30d")
    gauges = _compute_gauges(snap, w7=w7, w30=w30)

    vol7 = float((w7.new_listings if w7 else snap.new_listings) or 0)
    vol30 = float((w30.new_listings if w30 else snap.new_listings) or 0)
    if vol30 <= 0:
        vol30 = float(snap.listing_count or 0)
    expected = max(1.0, vol30 / 4.0) if vol30 else 1.0
    demand_delta = ((vol7 / expected) - 1.0) * 100.0 if vol30 else None

    disappeared = int(snap.disappeared_count or 0)
    ttl = float(snap.median_ttl_days) if snap.median_ttl_days is not None else None
    has_liquidity = disappeared >= 2 or ttl is not None
    disappeared_pct = (
        (disappeared / listing_count) * 100.0 if listing_count > 0 else None
    )

    median = (snap.price_median_cents or 0) / 100.0
    p25 = (snap.price_p25_cents or 0) / 100.0
    pmax = (snap.price_max_cents or 0) / 100.0
    p75 = _p75_from_snap(snap)
    buy_avg = p25 if p25 > 0 else (median * 0.65 if median else None)
    resell_avg = median if median else None
    buy_max = buy_avg
    if p75 and median:
        resell_target = min(p75, median * 1.15)
    elif median and pmax:
        resell_target = min(pmax * 0.9, median * 1.12)
    elif median:
        resell_target = median * 1.1
    else:
        resell_target = None
    margin_eur = None
    margin_pct = snap.margin_proxy_pct
    if buy_max and resell_avg:
        margin_eur = max(0.0, resell_avg - buy_max)
        if margin_pct is None and buy_max > 0:
            margin_pct = (margin_eur / buy_max) * 100.0

    has_model = bool(snap.model_slug)
    from vinted_bot.services.multi_angle import compute_multi_angle

    multi = compute_multi_angle(
        snap,
        windows=w,
        engagement=(
            snap.metrics if isinstance(getattr(snap, "metrics", None), dict) else None
        ),
        obscure_brand=_is_obscure_brand(snap.brand_slug),
    )
    niche_type, type_label = _classify_niche(
        margin_pct=margin_pct,
        gauges=gauges,
        listing_count=listing_count,
        demand_delta=demand_delta,
        median_eur=median or None,
        brand=snap.brand_slug,
        disappeared=disappeared,
        has_liquidity=has_liquidity,
        has_model=has_model,
        category=snap.category_slug,
        flags=snap.keyword_flags or "",
    )
    # Boost émergent / anomalie si multi-angle le confirme
    if multi.emerging.score >= 65 and niche_type == "high_value":
        niche_type, type_label = "emerging", "🆕 Produit / marque émergente"
    if multi.anomaly.score >= 70 and niche_type not in {"hidden", "undervalued"}:
        niche_type, type_label = "undervalued", "💰 Fort écart achat / revente"

    from vinted_bot.services.niche_insights import (
        build_mandatory_explanation,
        classify_lifecycle,
        compute_confidence_insight,
        detect_international,
        detect_weak_signals,
        extract_depth_profile,
        learning_score_adjustment,
    )

    n7 = float(getattr(w7, "listing_count", 0) or listing_count) if w7 else float(listing_count)
    n30 = float(getattr(w30, "listing_count", 0) or listing_count) if w30 else float(listing_count)
    med7 = float(getattr(w7, "price_median_cents", 0) or 0) if w7 else 0.0
    med30 = float(getattr(w30, "price_median_cents", 0) or 0) if w30 else 0.0
    volume_delta = None
    if n30 > 0:
        volume_delta = ((n7 - (n30 / 4.0)) / max(1.0, n30 / 4.0)) * 100.0
    price_delta = None
    if med7 > 0 and med30 > 0:
        price_delta = ((med7 - med30) / med30) * 100.0

    depth = extract_depth_profile(titles)
    lifecycle = classify_lifecycle(
        listing_count=listing_count,
        sellers=sellers,
        demand_delta=demand_delta,
        demand_score=gauges["demand"],
        competition_score=gauges["competition"],
        price_delta=price_delta,
        volume_delta=volume_delta,
        famous=(snap.brand_slug or "") in _FAMOUS_NEED_MODEL
        or (snap.brand_slug or "") in _BROAD_BRANDS,
        obscure=_is_obscure_brand(snap.brand_slug),
    )
    # Marchés saturés / déclin : ne pas proposer comme opportunité
    # (sauf fiches produit sur niches déjà validées / postées par le détecteur)
    if lifecycle.avoid and not allow_avoid_lifecycle:
        return None

    eng = snap.metrics if isinstance(getattr(snap, "metrics", None), dict) else {}
    conf_insight = compute_confidence_insight(
        listing_count=listing_count,
        sellers=sellers,
        disappeared=disappeared,
        has_ttl=ttl is not None,
        has_engagement=float(eng.get("favourite_avg") or 0) > 0
        or float(eng.get("view_avg") or 0) > 0,
        titles_sampled=len(titles),
        confidence_raw=gauges["confidence"],
    )
    weak = detect_weak_signals(
        demand_delta=demand_delta,
        competition_score=gauges["competition"],
        listing_count=listing_count,
        emerging_score=multi.emerging.score,
        obscure=_is_obscure_brand(snap.brand_slug),
        lifecycle=lifecycle.stage,
    )
    intl = detect_international(
        titles,
        flags=snap.keyword_flags or "",
        brand=snap.brand_slug,
    )

    score = _composite_score(
        margin_pct=margin_pct,
        gauges=gauges,
        niche_type=niche_type,
        listing_count=listing_count,
        brand=snap.brand_slug,
        median_eur=median or None,
        has_model=has_model,
    )
    db_score = float(snap.score or 0)
    # Blend score DB + composite gauges + multi-angle
    score = round(
        _clamp(score * 0.45 + db_score * 0.25 + multi.composite * 0.30),
        1,
    )
    if weak.is_weak_signal:
        score = round(_clamp(score * 1.08), 1)
    if intl.is_international:
        score = round(_clamp(score * 1.04), 1)
    if depth.variant_count >= 3:
        score = round(_clamp(score + 2.0), 1)
    # Apprentissage historique
    try:
        from vinted_bot.db.repositories import get_opportunity_score_history

        with session_scope() as session:
            hist = get_opportunity_score_history(session, snap.niche_key, limit=8)
        score = round(_clamp(score + learning_score_adjustment(hist, current_score=score)), 1)
    except Exception:  # noqa: BLE001
        pass
    # Confiance faible → plafond
    if conf_insight.label == "faible":
        score = min(score, 68.0)

    prio, prio_label = opportunity_priority(score)
    if prio == "weak":
        # Fiches produit : niches déjà validées par le détecteur — on ancre le score
        # sur le meilleur score historique posté si le recalcul live est tombé trop bas
        # (ex. deep-dive qui dilue la marge / concurrence).
        if for_fiche:
            try:
                from vinted_bot.db.models import OpportunityHistory

                with session_scope() as session:
                    hist_score = session.scalar(
                        select(OpportunityHistory.score)
                        .where(OpportunityHistory.niche_key == snap.niche_key)
                        .where(OpportunityHistory.posted.is_(True))
                        .order_by(OpportunityHistory.score.desc())
                        .limit(1)
                    )
                if hist_score is not None and float(hist_score) >= PUBLISH_MIN_SCORE:
                    score = round(float(hist_score), 1)
                    prio, prio_label = opportunity_priority(score)
            except Exception:  # noqa: BLE001
                pass
        if prio == "weak":
            return None

    badges = list(
        _badges_for(
            niche_type=niche_type,
            gauges=gauges,
            margin_pct=margin_pct,
            brand=snap.brand_slug,
            disappeared=disappeared,
            demand_delta=demand_delta,
        )
    )
    badges.insert(0, lifecycle.label.split("—")[0].strip()[:24])
    if weak.is_weak_signal:
        badges.append("📡 Signal faible")
    if intl.is_international:
        badges.append("🌍 Intl")
    badges = list(dict.fromkeys(badges))[:5]

    why = _why_one_liner(
        niche_type,
        gauges=gauges,
        margin_pct=margin_pct,
        disappeared=disappeared,
        demand_delta=demand_delta,
        listing_count=listing_count,
        sellers=sellers,
    )
    if multi.signals:
        why = f"{why} · {multi.signals[0]}"
    facts = _facts_line(
        listing_count=listing_count,
        sellers=sellers,
        buy=buy_max,
        median=median or None,
        p75=p75,
        disappeared=disappeared,
        disappeared_pct=disappeared_pct,
        ttl=ttl,
        confidence=conf_insight.score,
    )
    analysis = _ai_analysis(
        name,
        niche_type=niche_type,
        gauges=gauges,
        margin_eur=margin_eur,
        buy=buy_max,
        resell=resell_target or resell_avg,
        category=snap.category_slug,
        facts_line=facts,
        disappeared=disappeared,
        demand_delta=demand_delta,
        listing_count=listing_count,
        sellers=sellers,
        ttl=ttl,
    )

    terms = _search_terms(snap, name)
    where = " · ".join(f"`{t}`" for t in terms[:5]) or f"`{name}`"
    strategy_buy = (
        f"Prix max achat : **≤ {buy_max:.0f} €** (P25 observé)"
        if buy_max
        else "Attendre une entrée clairement sous médiane."
    )
    sell_ref = resell_target or resell_avg
    if sell_ref and p75:
        strategy_sell = (
            f"Prix revente cible : **{sell_ref:.0f} €** "
            f"(médiane / P75 {p75:.0f} €)"
        )
    elif sell_ref:
        strategy_sell = f"Prix revente cible : **{sell_ref:.0f} €**"
    else:
        strategy_sell = "Caler la revente sur la médiane observée."

    conf = conf_insight.score
    if lifecycle.stage in {"emerging", "growth"} and weak.is_weak_signal:
        action, detail = (
            "buy",
            "Signal précoce : ouvrir alertes maintenant avant saturation.",
        )
    elif (
        score >= 70
        and (margin_pct or 0) >= 40
        and gauges["competition"] <= 55
        and conf >= 50
    ):
        action, detail = (
            "buy",
            "Ouvrir des alertes / recherches actives sur cette niche maintenant.",
        )
    elif score >= 55 and conf >= 40:
        action, detail = (
            "watch",
            "Surveiller 48–72h et n'acheter que sous le prix max conseillé.",
        )
    else:
        action, detail = (
            "wait",
            "Signal encore trop faible (échantillon ou liquidité) pour forcer.",
        )

    all_signals = list(multi.signals)
    if weak.is_weak_signal:
        all_signals.extend(weak.signals)
    explanation = build_mandatory_explanation(
        name=name,
        lifecycle=lifecycle,
        why_core=why,
        signals=all_signals,
        weak=weak,
        depth=depth,
        confidence=conf_insight,
        intl=intl,
        strategy_buy=strategy_buy,
        strategy_sell=strategy_sell,
        action_detail=detail,
        avoid_saturated=lifecycle.avoid,
    )
    # Enrichir l'analyse textuelle avec profondeur / intl
    analysis = (
        f"{analysis} Profondeur : {depth.summary}. "
        f"Confiance {conf_insight.label}. {intl.summary}."
    )

    return Opportunity(
        niche_key=snap.niche_key,
        name=name,
        score=score,
        niche_type=niche_type,
        niche_type_label=type_label,
        priority=prio,
        priority_label=prio_label,
        badges=tuple(badges),
        price_buy_avg_eur=round(buy_avg, 0) if buy_avg else None,
        price_resell_avg_eur=round(resell_avg, 0) if resell_avg else None,
        price_max_eur=round(pmax, 0) if pmax else None,
        price_buy_max_eur=round(buy_max, 0) if buy_max else None,
        price_resell_target_eur=round(resell_target, 0) if resell_target else None,
        margin_eur=round(margin_eur, 0) if margin_eur is not None else None,
        margin_pct=round(margin_pct, 1) if margin_pct is not None else None,
        demand_score=gauges["demand"],
        rarity_score=gauges["rarity"],
        competition_score=gauges["competition"],
        rotation_score=gauges["rotation"],
        supply_ease_score=gauges["supply_ease"],
        price_stability_score=gauges["price_stability"],
        confidence=conf_insight.score,
        unique_sellers=sellers,
        disappeared_pct=round(disappeared_pct, 1) if disappeared_pct is not None else None,
        median_ttl_days=ttl,
        price_p75_eur=round(p75, 0) if p75 else None,
        facts_line=facts,
        multi_angle_composite=multi.composite,
        multi_angle_block=multi.embed_block(),
        signals=tuple(dict.fromkeys(all_signals))[:8],
        angle_demand=multi.demand.score,
        angle_supply=multi.supply.score,
        angle_price=multi.price.score,
        angle_behavioral=multi.behavioral.score,
        angle_emerging=multi.emerging.score,
        angle_profitability=multi.profitability.score,
        angle_anomaly=multi.anomaly.score,
        lifecycle=lifecycle.stage,
        lifecycle_label=lifecycle.label,
        lifecycle_avoid=lifecycle.avoid,
        depth_summary=depth.summary,
        weak_signal=weak.is_weak_signal,
        weak_signal_summary=weak.summary,
        confidence_label=conf_insight.label,
        international=intl.is_international,
        international_summary=intl.summary,
        explain_why=explanation.why,
        explain_signals=explanation.signals,
        explain_strategy=explanation.strategy,
        why_short=why,
        ai_analysis=analysis,
        strategy_where=f"Chercher : {where}",
        strategy_buy=strategy_buy,
        strategy_sell=strategy_sell,
        action=action,
        action_detail=detail,
        photo_url=_find_photo(snap),
        brand_slug=snap.brand_slug,
        model_slug=snap.model_slug,
        category_slug=snap.category_slug,
        keyword_flags=snap.keyword_flags or "",
        search_terms=terms,
        sample_size=listings_analyzed or listing_count,
        listing_count=listing_count,
        disappeared_count=disappeared,
    )


def _load_recently_posted_keys() -> dict[str, str]:
    """niche_key → posted_at iso."""
    with session_scope() as session:
        data = get_checkpoint(session, POSTED_NICHES_CHECKPOINT) or {}
    keys = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(keys, dict):
        return {}
    return {str(k): str(v) for k, v in keys.items()}


def _load_recently_posted_names() -> dict[str, str]:
    """name normalisé → posted_at iso (anti-doublon titres proches)."""
    with session_scope() as session:
        data = get_checkpoint(session, POSTED_NICHES_CHECKPOINT) or {}
    names = data.get("names") if isinstance(data, dict) else None
    if not isinstance(names, dict):
        return {}
    return {str(k): str(v) for k, v in names.items()}


def _norm_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _parse_iso_age_hours(raw: str) -> float | None:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def _is_recently_posted_key(
    niche_key: str,
    posted_map: dict[str, str],
    *,
    hours: float = POSTED_DOWNRANK_HOURS,
) -> bool:
    raw = posted_map.get(niche_key)
    if not raw:
        return False
    age = _parse_iso_age_hours(raw)
    return age is not None and age < hours


def _is_recently_posted_name(
    name: str,
    names_map: dict[str, str],
    *,
    hours: float = POSTED_NAME_COOLDOWN_HOURS,
) -> bool:
    raw = names_map.get(_norm_name(name))
    if not raw:
        return False
    age = _parse_iso_age_hours(raw)
    return age is not None and age < hours


def was_opportunity_recently_shown(op: Opportunity) -> bool:
    """True si cette analyse a déjà été publiée récemment (clé ou nom)."""
    return _is_recently_posted_key(
        op.niche_key, _load_recently_posted_keys()
    ) or _is_recently_posted_name(op.name, _load_recently_posted_names())


def mark_opportunities_posted(ops: Sequence[Opportunity]) -> None:
    """Enregistre les niches postées — exclusion dure des prochaines analyses Discord."""
    if not ops:
        return
    now = datetime.now(timezone.utc).isoformat()
    with session_scope() as session:
        data = get_checkpoint(session, POSTED_NICHES_CHECKPOINT) or {}
        keys = dict(data.get("keys") or {}) if isinstance(data, dict) else {}
        names = dict(data.get("names") or {}) if isinstance(data, dict) else {}
        for op in ops:
            keys[op.niche_key] = now
            names[_norm_name(op.name)] = now
        key_items = sorted(keys.items(), key=lambda kv: kv[1], reverse=True)[
            :MAX_POSTED_KEYS_KEPT
        ]
        name_items = sorted(names.items(), key=lambda kv: kv[1], reverse=True)[
            :MAX_POSTED_KEYS_KEPT
        ]
        set_checkpoint(
            session,
            POSTED_NICHES_CHECKPOINT,
            {"keys": dict(key_items), "names": dict(name_items)},
        )


def board_content_hash(ops: list[Opportunity]) -> str:
    payload = "|".join(f"{o.niche_key}:{o.score:.0f}" for o in ops)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def board_hash_unchanged(ops: list[Opportunity]) -> bool:
    if not ops:
        return False
    digest = board_content_hash(ops)
    with session_scope() as session:
        data = get_checkpoint(session, POSTED_BOARD_HASH_CHECKPOINT) or {}
    return bool(data.get("hash") == digest)


def mark_board_hash(ops: list[Opportunity]) -> None:
    digest = board_content_hash(ops)
    with session_scope() as session:
        set_checkpoint(
            session,
            POSTED_BOARD_HASH_CHECKPOINT,
            {"hash": digest, "posted_at": datetime.now(timezone.utc).isoformat()},
        )


def filter_publishable_opportunities(
    ops: Sequence[Opportunity],
    *,
    min_score: float = PUBLISH_MIN_SCORE,
) -> list[Opportunity]:
    """Garde uniquement les opportunités assez fortes pour Discord.

    Exige un ensemble d'annonces (pas un article isolé) avant publication.
    """
    out = [
        op
        for op in ops
        if op.score >= min_score
        and op.priority != "weak"
        and op.listing_count >= MIN_PUBLISH_LISTINGS
        and op.unique_sellers >= MIN_NICHE_SELLERS
    ]
    return out


def select_publishable_opportunities(
    *,
    limit: int = MAX_OPPORTUNITIES_POSTED,
    min_score: float = PUBLISH_MIN_SCORE,
) -> list[Opportunity]:
    """Top opportunités prêtes à publier (score ≥ seuil publication)."""
    return filter_publishable_opportunities(
        select_opportunities(limit=max(limit * 2, limit), min_score=MIN_OPPORTUNITY_SCORE),
        min_score=min_score,
    )[:limit]


def select_opportunities(
    *,
    limit: int = MAX_OPPORTUNITIES_POSTED,
    min_score: float = MIN_OPPORTUNITY_SCORE,
) -> list[Opportunity]:
    """Sélectionne les meilleures études de niches (granulaire uniquement).

    Autonome : ne suit pas les bestsellers / tendances — priorise les
    niches sous-exploitées détectées dans le pool découverte.
    """
    from vinted_bot.services.market_intel import (
        discovery_candidate_snapshots,
        load_niche_windows,
    )

    snaps = discovery_candidate_snapshots(window="30d", limit=220)
    with session_scope() as session:
        listings_analyzed = int(
            session.scalar(
                select(func.count())
                .select_from(Listing)
                .where(Listing.is_active.is_(True))
            )
            or 0
        )

    posted_keys = _load_recently_posted_keys()
    posted_names = _load_recently_posted_names()
    out: list[Opportunity] = []
    seen: set[str] = set()
    for snap in snaps:
        if not is_granular_niche(snap):
            continue
        # Jamais retraiter une niche déjà publiée récemment
        if _is_recently_posted_key(snap.niche_key, posted_keys):
            continue
        points = load_niche_windows(snap.niche_key)

        class _W:
            def __init__(self, p: Any) -> None:
                self.new_listings = p.new_listings
                self.listing_count = p.listing_count
                self.price_median_cents = p.price_median_cents
                self.median_ttl_days = p.median_ttl_days
                self.disappeared_count = getattr(p, "disappeared_count", 0) or 0

        windows = {p.window: _W(p) for p in points}
        op = snapshot_to_opportunity(
            snap, windows=windows, listings_analyzed=listings_analyzed
        )
        if op is None or op.score < min_score:
            continue
        key = _norm_name(op.name)
        if not key or key in seen:
            continue
        if _is_recently_posted_name(op.name, posted_names):
            continue
        seen.add(key)
        out.append(op)

    def _rank_key(o: Opportunity) -> tuple[float, float]:
        from vinted_bot.services.market_entities import category_domain

        type_boost = {
            "hidden": 16.0,
            "undervalued": 12.0,
            "emerging": 6.0,
            "high_rotation": 3.0,
            "high_value": -6.0,  # souvent déjà saturé / chassé
        }.get(o.niche_type, 0.0)
        famous_pen = (
            -14.0
            if (o.brand_slug or "") in _FAMOUS_NEED_MODEL
            else 0.0
        )
        known_pen = (
            -8.0
            if (o.brand_slug or "") in _KNOWN_BRANDS and not o.model_slug
            else 0.0
        )
        obscure_boost = 10.0 if _is_obscure_brand(o.brand_slug) else 0.0
        product_boost = 4.0 if o.model_slug else 0.0
        object_boost = (
            8.0 if (o.category_slug or "") in _OBJECT_CATEGORIES else 0.0
        )
        license_boost = (
            5.0
            if _flag_tokens(o.keyword_flags) & _LICENSE_FLAG_TOKENS
            else 0.0
        )
        # Volume moyen = affaire inexploitée ; mega-volume = déjà chassé
        n = o.listing_count
        if MIN_NICHE_LISTINGS <= n <= 28:
            volume_adj = 12.0
        elif 29 <= n <= 45:
            volume_adj = 3.0
        elif n > 70:
            volume_adj = -20.0
        else:
            volume_adj = -8.0
        signal_boost = min(10.0, len(o.signals) * 2.5)
        multi_boost = (o.multi_angle_composite - 50.0) * 0.10
        anomaly_boost = 8.0 if o.angle_anomaly >= 60 else 0.0
        weak_boost = 12.0 if o.weak_signal else 0.0
        # Pas un chasseur de tendances : cycle mature OK si marge ; decline out
        lifecycle_boost = {
            "emerging": 4.0,
            "growth": 2.0,
            "maturity": 1.0,
            "decline": -20.0,
            "saturated": -35.0,
        }.get(o.lifecycle, 0.0)
        intl_boost = 4.0 if o.international else 0.0
        conf_boost = (o.confidence - 50.0) * 0.08
        # Découverte large hors fashion bestsellers
        domain = category_domain(o.category_slug)
        domain_boost = 6.0 if domain not in {"fashion", "shoes", "unknown"} else -2.0
        return (
            o.score
            + type_boost
            + famous_pen
            + known_pen
            + obscure_boost
            + product_boost
            + object_boost
            + license_boost
            + volume_adj
            + domain_boost
            + signal_boost
            + multi_boost
            + anomaly_boost
            + weak_boost
            + lifecycle_boost
            + intl_boost
            + conf_boost,
            o.margin_pct or 0.0,
        )

    out.sort(key=_rank_key, reverse=True)
    picked: list[Opportunity] = []
    famous_count = 0
    high_value_count = 0
    type_counts: dict[str, int] = {}
    cat_counts: dict[str, int] = {}
    brand_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    for op in out:
        from vinted_bot.services.market_entities import category_domain

        brand = op.brand_slug or ""
        if brand in _FAMOUS_NEED_MODEL:
            if famous_count >= 1:
                continue
            famous_count += 1
        if op.niche_type == "high_value":
            if high_value_count >= 1:
                continue
            high_value_count += 1
        # Réserve la majorité aux niches inexploitées / sous-évaluées
        if type_counts.get(op.niche_type, 0) >= 3:
            continue
        cat = op.category_slug or "_"
        if cat_counts.get(cat, 0) >= 2:
            continue
        if brand and brand not in {"inconnu", ""} and brand_counts.get(brand, 0) >= 2:
            continue
        domain = category_domain(op.category_slug)
        # Fashion/sneakers : max 2 — le détecteur n'est pas un radar tendances mode
        if domain in {"fashion", "shoes"} and domain_counts.get(domain, 0) >= 2:
            continue
        if domain_counts.get(domain, 0) >= 3:
            continue
        # Mega-volume : skip sauf anomalie / valeur cachée forte
        if op.listing_count > 80 and op.niche_type not in {"hidden", "undervalued"}:
            continue
        type_counts[op.niche_type] = type_counts.get(op.niche_type, 0) + 1
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        if brand:
            brand_counts[brand] = brand_counts.get(brand, 0) + 1
        picked.append(op)
        if len(picked) >= limit:
            break

    # Historique d'apprentissage — enregistre chaque détection
    try:
        from vinted_bot.db.repositories import record_opportunity_history

        with session_scope() as session:
            for op in picked:
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
                    payload={
                        "weak_signal": op.weak_signal,
                        "international": op.international,
                        "depth": op.depth_summary,
                        "confidence_label": op.confidence_label,
                    },
                    posted=False,
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("opportunity_history_record_failed", error=str(exc)[:160])

    log.info(
        "opportunities_selected",
        count=len(picked),
        scanned=len(snaps),
        domains=dict(domain_counts),
        weak_signals=sum(1 for o in picked if o.weak_signal),
    )
    return picked


def _radar_badges(op: Opportunity) -> tuple[str, ...]:
    """Badges membres — lisibles, sans jargon technique."""
    badges: list[str] = []
    if op.demand_score >= 65:
        badges.append("🔥 Forte demande")
    if op.lifecycle in {"emerging", "growth"} or op.angle_emerging >= 55:
        badges.append("📈 En croissance")
    if op.score >= 70:
        badges.append("💎 Opportunité intéressante")
    elif op.priority in {"interesting", "strong", "exceptional"}:
        badges.append("💎 Opportunité intéressante")
    if op.rotation_score >= 60 or (op.disappeared_pct or 0) >= 25:
        badges.append("⚡ Se vend vite")
    if op.international:
        badges.append("🌍 Signal international")
    if op.weak_signal:
        badges.append("📡 Signal précoce")
    # Fallback depuis badges internes (déjà propres)
    for b in op.badges:
        if any(x in b for x in ("🔥", "📈", "💎", "⚡", "🌍", "📡")) and b not in badges:
            badges.append(b)
    return tuple(dict.fromkeys(badges))[:4]


def _radar_demand_pct(op: Opportunity) -> int:
    """Pourcentage lisible demande (proxy score, pas un calcul interne affiché)."""
    return max(5, min(120, int(round(op.demand_score - 25))))


def _radar_sale_pct(op: Opportunity) -> int:
    """Pourcentage lisible vitesse de vente."""
    if op.disappeared_pct is not None and op.disappeared_pct > 0:
        return max(5, min(120, int(round(op.disappeared_pct * 1.4))))
    return max(5, min(120, int(round(op.rotation_score - 20))))


def _radar_why_lines(op: Opportunity) -> str:
    """Résumé IA court — 2 lignes max."""
    line1 = (op.why_short or "Indicateurs marché favorables.").strip()
    # Une seule phrase principale pour le signal
    signal = ""
    if op.signals:
        signal = op.signals[0].replace("📈 ", "").replace("🔥 ", "").replace("💎 ", "")
    elif op.weak_signal:
        signal = op.weak_signal_summary
    elif op.lifecycle_label:
        signal = op.lifecycle_label.split("—")[0].strip()
    line2 = f"Signal principal : {signal}." if signal else "Demande et prix favorables."
    # Couper agressivement
    if len(line1) > 120:
        line1 = line1[:117].rstrip() + "…"
    if len(line2) > 120:
        line2 = line2[:117].rstrip() + "…"
    return f"{line1}\n{line2}"


def _radar_variants(op: Opportunity) -> list[str]:
    raw = (op.depth_summary or "").strip()
    if not raw or raw.startswith("peu de variantes"):
        return []
    parts = [p.strip() for p in raw.split("·") if p.strip()]
    return parts[:4]


def _explore_search_query(op: Opportunity) -> str:
    """Requête catalogue propre (marque + modèle / meilleurs termes)."""
    junk = {
        "inconnu",
        "unknown",
        "niche",
        "opportunité",
        "opportunite",
    }
    terms: list[str] = []
    if op.brand_slug and op.brand_slug.lower() not in junk:
        terms.append(op.brand_slug.replace("_", " ").strip())
    if op.model_slug:
        terms.append(op.model_slug.replace("_", " ").strip())
    for t in op.search_terms:
        tl = (t or "").strip()
        if len(tl) < 2 or tl.lower() in junk:
            continue
        if tl.lower() in {x.lower() for x in terms}:
            continue
        terms.append(tl)
        if len(terms) >= 4:
            break
    if not terms:
        # Nom de niche sans bruit
        for tok in (op.name or "").replace("·", " ").split():
            if len(tok) >= 3 and tok.lower() not in junk:
                terms.append(tok)
            if len(terms) >= 4:
                break
    return " ".join(terms).strip() or (op.name or "vinted").strip()


def _vinted_explore_url(op: Opportunity) -> str:
    """Lien catalogue Vinted valide → annonces du même genre."""
    from urllib.parse import urlencode

    from vinted_bot.config import get_settings

    base = (get_settings().vinted_base_url or "https://www.vinted.fr").rstrip("/")
    query = _explore_search_query(op)
    # Format officiel catalog Vinted (search_text + tri récent)
    params: list[tuple[str, str]] = [
        ("search_text", query),
        ("order", "newest_first"),
    ]
    return f"{base}/catalog?{urlencode(params)}"


def build_opportunity_embed(
    op: Opportunity,
    *,
    listings_analyzed: int | None = None,
) -> dict[str, Any]:
    """Radar Discord premium — essentiel seulement (pas d'analyse technique)."""
    color = {
        "exceptional": COLOR_RED,
        "strong": COLOR_GOLD,
        "interesting": COLOR_GREEN,
        "hidden": COLOR_PURPLE,
    }.get(op.priority, COLOR_ORANGE)
    if op.niche_type == "hidden":
        color = COLOR_PURPLE

    badges = _radar_badges(op)
    badge_line = "\n".join(badges) if badges else "💎 Opportunité intéressante"
    buy = f"{op.price_buy_avg_eur:.0f} €" if op.price_buy_avg_eur else "—"
    resell = f"{op.price_resell_avg_eur:.0f} €" if op.price_resell_avg_eur else "—"
    demand_pct = _radar_demand_pct(op)
    sale_pct = _radar_sale_pct(op)
    why = _radar_why_lines(op)
    keywords = list(op.search_terms[:5]) or [op.name]
    kw_lines = "\n".join(f"• {k}" for k in keywords)
    variants = _radar_variants(op)
    var_block = (
        "\n".join(f"• {v}" for v in variants)
        if variants
        else "• Variantes à explorer via les mots-clés"
    )
    explore = _vinted_explore_url(op)
    search_q = _explore_search_query(op)
    n = listings_analyzed if listings_analyzed is not None else op.sample_size
    photo = _prefer_large_image_url(op.photo_url) if op.photo_url else None

    # Lien dès le haut + titre cliquable (embed.url)
    description = (
        f"**{op.name}**\n\n"
        f"⭐ Opportunité : **{op.score:.0f}/100**\n"
        f"{score_stars(op.score)}\n\n"
        f"{badge_line}\n\n"
        f"🔗 [Voir les annonces similaires sur Vinted]({explore})"
    )
    fields = [
        {
            "name": "💰 Marché",
            "value": (
                f"Achat observé :\n**{buy}**\n\n"
                f"Revente moyenne :\n**{resell}**"
            )[:1024],
            "inline": True,
        },
        {
            "name": "📊 Signaux marché",
            "value": (
                f"🔥 Demande : **+{demand_pct} %**\n"
                f"⚡ Vente : **+{sale_pct} %**"
            )[:1024],
            "inline": True,
        },
        {
            "name": "🧠 Pourquoi ?",
            "value": why[:1024],
            "inline": False,
        },
        {
            "name": "🔎 Rechercher",
            "value": (
                f"**Mots-clés :**\n{kw_lines}\n\n"
                f"**Variantes intéressantes :**\n{var_block}"
            )[:1024],
            "inline": False,
        },
        {
            "name": "🔗 Explorer la niche",
            "value": (
                f"[Ouvrir le catalogue « {search_q} »]({explore})\n"
                f"`{explore}`"
            )[:1024],
            "inline": False,
        },
    ]

    embed: dict[str, Any] = {
        "title": f"🧠 {op.name}"[:256],
        "description": description[:3900],
        "color": color,
        "fields": fields,
        "url": explore,
        "footer": {
            "text": f"📌 Analyse : {n:,} annonces analysées · clic titre = catalogue".replace(
                ",", " "
            )
        },
        "timestamp": _utcnow().isoformat(),
    }
    if photo:
        # Image dominante en grand (pas thumbnail)
        embed["image"] = {"url": photo}
    return embed


def build_opportunities_board_embed(ops: list[Opportunity]) -> dict[str, Any]:
    """Board radar compact — scores + une ligne pourquoi."""
    medals = ("🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣")
    lines: list[str] = []
    for i, op in enumerate(ops):
        medal = medals[i] if i < len(medals) else f"{i+1}."
        buy = f"{op.price_buy_avg_eur:.0f}€" if op.price_buy_avg_eur else "—"
        resell = f"{op.price_resell_avg_eur:.0f}€" if op.price_resell_avg_eur else "—"
        lines.append(
            f"{medal} **{op.name}** — ⭐ `{op.score:.0f}/100`\n"
            f"💰 {buy} → {resell} · {_radar_why_lines(op).splitlines()[0]}"
        )
    body = "\n\n".join(lines) if lines else "_Aucune opportunité intéressante_"
    return {
        "title": "🧠 RADAR — TOP NICHES",
        "description": (
            "Aperçu rapide — détail dans les cartes ci-dessous.\n\n"
            f"{body}"
        )[:3900],
        "color": COLOR_RED,
        "footer": {"text": "Détecteur niches · radar opportunités"},
        "timestamp": _utcnow().isoformat(),
    }


def build_pepite_from_opportunity_embed(
    *,
    title: str,
    url: str | None,
    price_cents: int,
    resell_cents: int,
    margin_pct: float,
    photo_url: str | None,
    niche_name: str,
    niche_score: float,
    size: str | None = None,
) -> dict[str, Any]:
    """Pépite (annonce) liée à une étude de niche."""
    ask = price_cents / 100.0
    resell = resell_cents / 100.0
    margin_eur = resell - ask
    discount = ((resell - ask) / resell * 100.0) if resell > 0 else 0.0
    why = [
        "✓ Appartient à une niche analysée par le détecteur",
        (
            f"✓ Prix inférieur de ~{discount:.0f} % au marché"
            if discount >= 15
            else "✓ Prix sous le marché"
        ),
        "✓ Potentiel de marge exploitable",
    ]
    lines = [f"**{title}**"]
    if size:
        lines.append(f"Taille `{size}`")
    if url:
        lines.append(f"[Voir l'annonce]({url})")
    fields = [
        {
            "name": "💶 Prix",
            "value": (
                f"Prix : **{ask:.0f} €**\n"
                f"Valeur estimée : **{resell:.0f} €**\n"
                f"Marge potentielle : **+{margin_eur:.0f} €** ({margin_pct:.0f} %)"
            ),
            "inline": False,
        },
        {
            "name": "🧠 Pourquoi cette annonce ?",
            "value": "\n".join(why),
            "inline": False,
        },
        {
            "name": "🔥 Niche source",
            "value": (
                f"**{niche_name}**\n"
                f"Score niche : `{niche_score:.0f}/100` {score_stars(niche_score)}"
            ),
            "inline": False,
        },
        {
            "name": "🎯 Action",
            "value": (
                "**🟢 Acheter / vérifier maintenant**\n"
                "Sous le prix marché de la niche analysée."
            ),
            "inline": False,
        },
    ]
    embed: dict[str, Any] = {
        "title": "💎 Pépite trouvée",
        "description": "\n".join(lines)[:3900],
        "color": COLOR_GREEN if margin_pct >= 50 else COLOR_GOLD,
        "fields": fields,
        "footer": {"text": f"Pépites ← étude de niche · {ENGINE_VERSION}"},
        "timestamp": _utcnow().isoformat(),
    }
    if photo_url:
        embed["thumbnail"] = {"url": photo_url}
    if url:
        embed["url"] = url
    return embed
