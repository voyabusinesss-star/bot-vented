"""Insights avancés : cycle, profondeur, signaux faibles, confiance, international."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

_COLOR_RE = (
    ("noir", "noir"),
    ("black", "noir"),
    ("blanc", "blanc"),
    ("white", "blanc"),
    ("beige", "beige"),
    ("marron", "marron"),
    ("brown", "marron"),
    ("vert", "vert"),
    ("olive", "olive"),
    ("bleu", "bleu"),
    ("navy", "navy"),
    ("rouge", "rouge"),
    ("red", "rouge"),
    ("gris", "gris"),
    ("grey", "gris"),
    ("gray", "gris"),
    ("rose", "rose"),
    ("pink", "rose"),
    ("orange", "orange"),
    ("jaune", "jaune"),
    ("violet", "violet"),
    ("bordeaux", "bordeaux"),
)
_SIZE_RE = re.compile(
    r"(?<!\w)(xxs|xs|s|m|l|xl|xxl|xxxl|2xl|3xl|\d{2})\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(19[89]\d|20[0-2]\d)\b")
_COLLAB_RE = re.compile(
    r"\b(collab|collaboration| x |×|travis|off[- ]white|sacai|fragment)\b",
    re.IGNORECASE,
)
_EDITION_RE = re.compile(
    r"\b(og|limited|edition|exclusive|special|archive|deadstock|sample)\b",
    re.IGNORECASE,
)
_INTL_PATTERNS = (
    ("usa", "USA"),
    ("us exclusive", "USA"),
    ("made in usa", "USA"),
    ("japan", "Japon"),
    ("japon", "Japon"),
    ("japanese", "Japon"),
    ("made in japan", "Japon"),
    ("uk exclusive", "UK"),
    ("london", "UK"),
    ("import", "Import"),
    ("ship from", "Import"),
    ("deadstock", "Deadstock intl"),
    ("jp exclusive", "Japon"),
    ("korea", "Corée"),
    ("seoul", "Corée"),
)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _pct_delta(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return ((curr - prev) / abs(prev)) * 100.0


@dataclass(slots=True, frozen=True)
class DepthProfile:
    colors: tuple[str, ...]
    sizes: tuple[str, ...]
    years: tuple[str, ...]
    has_collab: bool
    has_edition: bool
    variant_count: int
    summary: str


@dataclass(slots=True, frozen=True)
class LifecycleInsight:
    stage: str  # emerging | growth | maturity | decline | saturated
    label: str
    avoid: bool
    reason: str


@dataclass(slots=True, frozen=True)
class ConfidenceInsight:
    score: float
    label: str  # faible | moyenne | bonne | élevée
    reasons: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class WeakSignalInsight:
    score: float
    is_weak_signal: bool
    summary: str
    signals: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class InternationalInsight:
    score: float
    is_international: bool
    markets: tuple[str, ...]
    summary: str


@dataclass(slots=True, frozen=True)
class NicheExplanation:
    why: str
    signals: str
    strategy: str
    full_block: str


def extract_depth_profile(titles: Sequence[str]) -> DepthProfile:
    """Profondeur : couleurs, tailles, années, collabs, éditions."""
    blob = " ".join(titles).lower()
    colors: list[str] = []
    for needle, label in _COLOR_RE:
        if needle in blob and label not in colors:
            colors.append(label)
    sizes = tuple(
        sorted({m.group(1).upper() for m in _SIZE_RE.finditer(blob)})
    )[:8]
    years = tuple(sorted(set(_YEAR_RE.findall(blob))))[:6]
    has_collab = bool(_COLLAB_RE.search(blob))
    has_edition = bool(_EDITION_RE.search(blob))
    variant_count = len(colors) + len(years) + (1 if has_collab else 0) + (
        1 if has_edition else 0
    )
    parts: list[str] = []
    if sizes:
        parts.append("tailles " + ",".join(sizes[:5]))
    if years:
        parts.append("années " + ",".join(years[:3]))
    if has_collab:
        parts.append("collab")
    if has_edition:
        parts.append("édition spéciale")
    summary = " · ".join(parts) if parts else "peu de variantes détectées"
    return DepthProfile(
        colors=tuple(colors[:5]),
        sizes=sizes,
        years=years,
        has_collab=has_collab,
        has_edition=has_edition,
        variant_count=variant_count,
        summary=summary,
    )


def classify_lifecycle(
    *,
    listing_count: int,
    sellers: int,
    demand_delta: float | None,
    demand_score: float,
    competition_score: float,
    price_delta: float | None,
    volume_delta: float | None,
    famous: bool,
    obscure: bool,
) -> LifecycleInsight:
    """Cycle : émergence → croissance → maturité → déclin / saturé."""
    # Saturé : volume + concurrence élevés (surtout marques fameuses)
    if (
        listing_count >= 45
        and sellers >= 15
        and competition_score >= 55
        and (famous or demand_score < 55)
    ):
        return LifecycleInsight(
            "saturated",
            "🚫 Saturé — à éviter",
            True,
            "Stock et concurrence élevés : marché déjà exploité.",
        )
    if (
        (volume_delta is not None and volume_delta <= -25)
        and (price_delta is not None and price_delta <= -10)
        and listing_count >= 15
    ):
        return LifecycleInsight(
            "decline",
            "📉 Déclin",
            True,
            "Volume et prix en baisse : opportunité en perte de vitesse.",
        )
    if (
        obscure
        and listing_count <= 18
        and (demand_delta or 0) >= 25
        and competition_score <= 40
    ):
        return LifecycleInsight(
            "emerging",
            "🌱 Émergence",
            False,
            "Petite niche en accélération, encore peu concurrentielle.",
        )
    if (demand_delta or 0) >= 35 or (
        demand_score >= 60 and listing_count <= 35 and competition_score <= 50
    ):
        return LifecycleInsight(
            "growth",
            "📈 Croissance",
            False,
            "Demande en hausse avec concurrence encore gérable.",
        )
    if listing_count >= 25 and competition_score >= 45:
        return LifecycleInsight(
            "maturity",
            "🏛 Maturité",
            False,
            "Marché établi : être sélectif sur le prix d'entrée.",
        )
    if obscure and listing_count <= 12 and demand_score >= 45:
        return LifecycleInsight(
            "emerging",
            "🌱 Émergence",
            False,
            "Signal précoce sur un produit encore peu visible.",
        )
    return LifecycleInsight(
        "maturity",
        "🏛 Maturité",
        False,
        "Indicateurs stables — opportunité possible si la marge tient.",
    )


def compute_confidence_insight(
    *,
    listing_count: int,
    sellers: int,
    disappeared: int,
    has_ttl: bool,
    has_engagement: bool,
    titles_sampled: int,
    confidence_raw: float,
) -> ConfidenceInsight:
    score = confidence_raw
    reasons: list[str] = []
    if listing_count >= 15:
        reasons.append(f"échantillon n={listing_count}")
    elif listing_count >= 8:
        reasons.append(f"échantillon correct n={listing_count}")
    else:
        reasons.append(f"échantillon mince n={listing_count}")
        score = min(score, 50.0)
    if sellers >= 3:
        reasons.append(f"{sellers} vendeurs distincts")
    else:
        reasons.append("vendeurs peu identifiés")
        score -= 5
    if disappeared >= 2 or has_ttl:
        reasons.append("liquidité observée")
    else:
        reasons.append("liquidité encore faible")
        score -= 8
    if has_engagement:
        reasons.append("favoris/vues disponibles")
        score += 5
    if titles_sampled >= 5:
        reasons.append("variantes lues dans les titres")
    score = _clamp(score)
    if score >= 75:
        label = "élevée"
    elif score >= 55:
        label = "bonne"
    elif score >= 40:
        label = "moyenne"
    else:
        label = "faible"
    return ConfidenceInsight(round(score, 1), label, tuple(reasons[:5]))


def detect_weak_signals(
    *,
    demand_delta: float | None,
    competition_score: float,
    listing_count: int,
    emerging_score: float,
    obscure: bool,
    lifecycle: str,
) -> WeakSignalInsight:
    """Signaux faibles : progression avant popularité, faible concurrence."""
    signals: list[str] = []
    score = 0.0
    if (
        obscure
        and listing_count <= 20
        and competition_score <= 35
        and (demand_delta or 0) >= 20
    ):
        score += 40
        signals.append("progression + faible concurrence")
    if emerging_score >= 50 and listing_count <= 25:
        score += 25
        signals.append("émergence encore discrète")
    if lifecycle == "emerging":
        score += 20
        signals.append("cycle émergence")
    if (demand_delta or 0) >= 40 and competition_score <= 40:
        score += 15
        signals.append("accélération précoce")
    score = _clamp(score)
    is_weak = score >= 45 and lifecycle not in {"saturated", "decline"}
    summary = (
        " · ".join(signals)
        if signals
        else "pas de signal faible prioritaire"
    )
    return WeakSignalInsight(score, is_weak, summary, tuple(signals))


def detect_international(
    titles: Sequence[str],
    *,
    flags: str = "",
    brand: str | None = None,
) -> InternationalInsight:
    """Heuristique : signaux de demande internationale sous-exploités en FR."""
    blob = (" ".join(titles) + " " + flags).lower()
    markets: list[str] = []
    for needle, market in _INTL_PATTERNS:
        if needle in blob and market not in markets:
            markets.append(market)
    # Marques souvent tirées par hype hors FR
    intl_brands = {
        "jellycat",
        "porter",
        "visvim",
        "kapital",
        "needles",
        "human_made",
        "bape",
        "wtaps",
    }
    b = (brand or "").replace(" ", "_").lower()
    if b in intl_brands and "Japon" not in markets:
        markets.append("Hype intl")
    score = min(100.0, len(markets) * 28.0 + (15.0 if b in intl_brands else 0.0))
    is_intl = score >= 28
    summary = (
        f"Signaux {', '.join(markets)} — potentiellement sous-exploité sur Vinted FR"
        if is_intl
        else "pas de signal international clair"
    )
    return InternationalInsight(
        round(score, 1), is_intl, tuple(markets[:4]), summary
    )


def build_mandatory_explanation(
    *,
    name: str,
    lifecycle: LifecycleInsight,
    why_core: str,
    signals: Sequence[str],
    weak: WeakSignalInsight,
    depth: DepthProfile,
    confidence: ConfidenceInsight,
    intl: InternationalInsight,
    strategy_buy: str,
    strategy_sell: str,
    action_detail: str,
    avoid_saturated: bool,
) -> NicheExplanation:
    why = (
        f"**Pourquoi** : {why_core} "
        f"Cycle : {lifecycle.label} — {lifecycle.reason}"
    )
    sig_parts = list(signals[:4])
    if weak.is_weak_signal:
        sig_parts.append(f"signal faible ({weak.summary})")
    if depth.variant_count:
        sig_parts.append(f"profondeur : {depth.summary}")
    if intl.is_international:
        sig_parts.append(intl.summary)
    sig_parts.append(f"confiance {confidence.label} ({confidence.score:.0f}%)")
    signals_text = (
        "**Signaux** : " + " · ".join(sig_parts)
        if sig_parts
        else "**Signaux** : indicateurs marché favorables"
    )
    if avoid_saturated or lifecycle.avoid:
        strategy = (
            f"**Stratégie** : ⚠️ Éviter ou attendre — {lifecycle.reason} "
            f"Sinon seulement sous prix max si une pépite apparaît."
        )
    else:
        strategy = (
            f"**Stratégie** : {strategy_buy} {strategy_sell} "
            f"{action_detail}"
        )
    full = f"{why}\n\n{signals_text}\n\n{strategy}"
    return NicheExplanation(why, signals_text, strategy, full[:1024])


def learning_score_adjustment(
    history_scores: Sequence[float],
    *,
    current_score: float,
) -> float:
    """Ajuste le score selon l'historique de la même niche."""
    if not history_scores:
        return 0.0
    recent = list(history_scores)[-5:]
    avg = sum(recent) / len(recent)
    # Progression historique → boost ; chute → pénalité
    delta = current_score - avg
    if delta >= 8:
        return min(6.0, delta * 0.25)
    if delta <= -10:
        return max(-8.0, delta * 0.2)
    # Stabilité haute → léger boost de confiance
    if avg >= 70 and abs(delta) < 5:
        return 2.0
    return 0.0
