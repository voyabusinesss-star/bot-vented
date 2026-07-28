"""Filtrage intelligent revente : marque × catégorie × prix + score opportunité."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from vinted_bot.notify.discord import normalize_brand
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

Rarity = Literal["low", "medium", "high", "ultra"]
DealLevel = Literal["pepite", "bon_deal", "surveillance", "skip"]

DEAL_LEVEL_LABELS: dict[DealLevel, str] = {
    "pepite": "🔥 PÉPITE",
    "bon_deal": "💎 BON DEAL",
    "surveillance": "👀 SURVEILLANCE",
    "skip": "⏭️ SKIP",
}

DEAL_LEVEL_COLORS: dict[DealLevel, int] = {
    "pepite": 0xFF4500,  # orange feu
    "bon_deal": 0x57F287,  # vert
    "surveillance": 0xFEE75C,  # jaune
    "skip": 0x2B2D31,
}

CATEGORY_LABELS: dict[str, str] = {
    "dunk": "Dunk",
    "air_force_1": "Air Force 1",
    "chaussure": "Chaussure",
    "polo": "Polo",
    "hoodie": "Hoodie",
    "sweat": "Sweat",
    "pull": "Pull",
    "chemise": "Chemise",
    "veste": "Veste",
    "tshirt": "T-shirt",
    "pantalon": "Pantalon",
    "short": "Short",
    "default": "Article",
}


@dataclass(slots=True, frozen=True)
class CategoryRule:
    category: str
    max_buy_price: float
    average_resell: float
    minimum_profit: float
    rarity: Rarity = "medium"


@dataclass(slots=True, frozen=True)
class BrandDealConfig:
    key: str
    display_name: str
    brand_score: int
    items: dict[str, CategoryRule]
    allow_shoes: bool = False
    # True = Pépites Sneakers : uniquement chaussures / baskets
    shoes_only: bool = False


@dataclass(slots=True, frozen=True)
class DealFilterSettings:
    enabled: bool = True
    min_score_to_post: int = 60
    require_brand_config: bool = True
    require_category_match: bool = True
    # Si pas de match catégorie → fallback prix médian marque (évite de rater des deals)
    use_category_fallback: bool = True
    # Ignore les annonces plus vieilles que X minutes (None = pas de limite)
    max_listing_age_minutes: int | None = 30
    reject_kids: bool = True
    # Chaussures interdites sauf marques allow_shoes: true (luxe)
    reject_shoes_unless_allowed: bool = True
    # Rejette les annonces type replica / fake
    reject_replicas: bool = True


@dataclass(slots=True, frozen=True)
class KidsFilterConfig:
    title_keywords: tuple[str, ...] = ()
    age_patterns: tuple[str, ...] = ()
    size_keywords: tuple[str, ...] = ()
    size_patterns: tuple[str, ...] = ()
    adult_size_allowlist: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ShoesFilterConfig:
    title_keywords: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    # Sur marques shoes_only : titres clairement vêtement → rejet
    clothing_exclude_keywords: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ReplicaFilterConfig:
    title_keywords: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ScoringConfig:
    weights: dict[str, int]
    rarity_points: dict[str, int]
    freshness_minutes: dict[int, int]
    pepite: int = 90
    bon_deal: int = 75
    surveillance: int = 60


@dataclass(slots=True, frozen=True)
class DealFiltersConfig:
    settings: DealFilterSettings
    scoring: ScoringConfig
    category_keywords: list[tuple[str, tuple[str, ...]]]
    brands: dict[str, BrandDealConfig]
    kids: KidsFilterConfig = KidsFilterConfig()
    shoes: ShoesFilterConfig = ShoesFilterConfig()
    replicas: ReplicaFilterConfig = ReplicaFilterConfig()


@dataclass(slots=True, frozen=True)
class DealEvaluation:
    """Résultat d'évaluation d'une annonce pour le filtrage Discord."""

    should_post: bool
    score: int
    level: DealLevel
    level_label: str
    brand_key: str
    brand_display: str
    category: str | None
    category_label: str | None
    buy_price: float
    average_resell: float
    estimated_profit: float
    max_buy_price: float
    minimum_profit: float
    rarity: Rarity | None
    reason: str
    age_minutes: int | None = None

    @property
    def color(self) -> int:
        return DEAL_LEVEL_COLORS.get(self.level, DEAL_LEVEL_COLORS["skip"])


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = text.replace("-", " ").replace("_", " ")
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return " ".join(text.split())


def _brand_config_key(brand: str | None) -> str:
    return normalize_brand(brand).replace(" ", "_")


def _as_rarity(value: Any) -> Rarity:
    raw = str(value or "medium").lower().strip()
    if raw in ("low", "medium", "high", "ultra"):
        return raw  # type: ignore[return-value]
    return "medium"


def _parse_category_rule(category: str, raw: dict[str, Any]) -> CategoryRule | None:
    try:
        max_buy = float(raw["max_buy_price"])
        average = float(raw["average_resell"])
        min_profit = float(raw.get("minimum_profit", 0))
    except (KeyError, TypeError, ValueError):
        return None
    return CategoryRule(
        category=category,
        max_buy_price=max_buy,
        average_resell=average,
        minimum_profit=min_profit,
        rarity=_as_rarity(raw.get("rarity")),
    )


def _default_scoring() -> ScoringConfig:
    return ScoringConfig(
        weights={
            "margin": 30,
            "brand": 15,
            "rarity": 15,
            "category": 10,
            "below_market": 20,
            "freshness": 10,
        },
        rarity_points={"low": 40, "medium": 60, "high": 80, "ultra": 100},
        freshness_minutes={5: 10, 15: 8, 60: 5, 360: 2},
        pepite=90,
        bon_deal=75,
        surveillance=60,
    )


@lru_cache(maxsize=4)
def load_deal_filters(path: str | None = None) -> DealFiltersConfig:
    config_path = Path(path) if path else (_project_root() / "config" / "deal_filters.yaml")
    if not config_path.exists():
        log.warning("deal_filters_missing", path=str(config_path))
        return DealFiltersConfig(
            settings=DealFilterSettings(enabled=False),
            scoring=_default_scoring(),
            category_keywords=[],
            brands={},
            kids=KidsFilterConfig(),
            shoes=ShoesFilterConfig(),
            replicas=ReplicaFilterConfig(),
        )

    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    settings_raw = raw.get("settings") or {}
    max_age_raw = settings_raw.get("max_listing_age_minutes", 30)
    max_age: int | None
    if max_age_raw is None or max_age_raw == "" or max_age_raw is False:
        max_age = None
    else:
        max_age = max(1, int(max_age_raw))

    settings = DealFilterSettings(
        enabled=bool(settings_raw.get("enabled", True)),
        min_score_to_post=int(settings_raw.get("min_score_to_post", 60)),
        require_brand_config=bool(settings_raw.get("require_brand_config", True)),
        require_category_match=bool(settings_raw.get("require_category_match", True)),
        use_category_fallback=bool(settings_raw.get("use_category_fallback", True)),
        max_listing_age_minutes=max_age,
        reject_kids=bool(settings_raw.get("reject_kids", True)),
        reject_shoes_unless_allowed=bool(
            settings_raw.get("reject_shoes_unless_allowed", True)
        ),
        reject_replicas=bool(settings_raw.get("reject_replicas", True)),
    )

    scoring_raw = raw.get("scoring") or {}
    weights_raw = scoring_raw.get("weights") or {}
    default_scoring = _default_scoring()
    levels = scoring_raw.get("levels") or {}
    freshness_raw = scoring_raw.get("freshness_minutes") or default_scoring.freshness_minutes
    freshness: dict[int, int] = {}
    for key, value in dict(freshness_raw).items():
        try:
            freshness[int(key)] = int(value)
        except (TypeError, ValueError):
            continue

    scoring = ScoringConfig(
        weights={
            "margin": int(weights_raw.get("margin", default_scoring.weights["margin"])),
            "brand": int(weights_raw.get("brand", default_scoring.weights["brand"])),
            "rarity": int(weights_raw.get("rarity", default_scoring.weights["rarity"])),
            "category": int(weights_raw.get("category", default_scoring.weights["category"])),
            "below_market": int(
                weights_raw.get("below_market", default_scoring.weights["below_market"])
            ),
            "freshness": int(
                weights_raw.get("freshness", default_scoring.weights["freshness"])
            ),
        },
        rarity_points={
            str(k): int(v)
            for k, v in (
                scoring_raw.get("rarity_points") or default_scoring.rarity_points
            ).items()
        },
        freshness_minutes=freshness or dict(default_scoring.freshness_minutes),
        pepite=int(levels.get("pepite", default_scoring.pepite)),
        bon_deal=int(levels.get("bon_deal", default_scoring.bon_deal)),
        surveillance=int(levels.get("surveillance", default_scoring.surveillance)),
    )

    # Mots-clés : ordre YAML préservé (spécifique → générique)
    keywords_raw = raw.get("category_keywords") or {}
    category_keywords: list[tuple[str, tuple[str, ...]]] = []
    for category, words in keywords_raw.items():
        if not isinstance(words, list):
            continue
        normalized = tuple(
            _normalize_text(str(w)) for w in words if str(w).strip()
        )
        if normalized:
            category_keywords.append((str(category), normalized))

    brand_scores_raw = raw.get("brand_scores") or {}
    brands: dict[str, BrandDealConfig] = {}
    for brand_key, brand_raw in (raw.get("brands") or {}).items():
        if not isinstance(brand_raw, dict):
            continue
        key = _brand_config_key(str(brand_key))
        display = str(brand_raw.get("display_name") or brand_key).strip()
        normalized_name = normalize_brand(display) or normalize_brand(key.replace("_", " "))
        score_lookup = (
            brand_scores_raw.get(normalized_name)
            or brand_scores_raw.get(key.replace("_", " "))
            or brand_scores_raw.get(key)
            or 50
        )
        items: dict[str, CategoryRule] = {}
        for cat_key, cat_raw in (brand_raw.get("items") or {}).items():
            if not isinstance(cat_raw, dict):
                continue
            rule = _parse_category_rule(str(cat_key), cat_raw)
            if rule is not None:
                items[str(cat_key)] = rule
        brands[key] = BrandDealConfig(
            key=key,
            display_name=display,
            brand_score=max(0, min(100, int(score_lookup))),
            items=items,
            allow_shoes=bool(brand_raw.get("allow_shoes", False)),
            shoes_only=bool(brand_raw.get("shoes_only", False)),
        )
        # alias sans underscore
        brands[key.replace("_", " ")] = brands[key]

    kids_raw = raw.get("kids_filter") or {}
    kids = KidsFilterConfig(
        title_keywords=tuple(
            _normalize_text(str(w))
            for w in (kids_raw.get("title_keywords") or [])
            if str(w).strip()
        ),
        age_patterns=tuple(
            str(p) for p in (kids_raw.get("age_patterns") or []) if str(p).strip()
        ),
        size_keywords=tuple(
            _normalize_text(str(w))
            for w in (kids_raw.get("size_keywords") or [])
            if str(w).strip()
        ),
        size_patterns=tuple(
            str(p) for p in (kids_raw.get("size_patterns") or []) if str(p).strip()
        ),
        adult_size_allowlist=tuple(
            _normalize_text(str(w))
            for w in (kids_raw.get("adult_size_allowlist") or [])
            if str(w).strip()
        ),
    )

    shoes_raw = raw.get("shoes_filter") or {}
    shoes = ShoesFilterConfig(
        title_keywords=tuple(
            _normalize_text(str(w))
            for w in (shoes_raw.get("title_keywords") or [])
            if str(w).strip()
        ),
        categories=tuple(
            str(c).strip()
            for c in (shoes_raw.get("categories") or [])
            if str(c).strip()
        ),
        clothing_exclude_keywords=tuple(
            _normalize_text(str(w))
            for w in (shoes_raw.get("clothing_exclude_keywords") or [])
            if str(w).strip()
        ),
    )

    replicas_raw = raw.get("replica_filter") or {}
    replicas = ReplicaFilterConfig(
        title_keywords=tuple(
            _normalize_text(str(w))
            for w in (replicas_raw.get("title_keywords") or [])
            if str(w).strip()
        ),
    )

    return DealFiltersConfig(
        settings=settings,
        scoring=scoring,
        category_keywords=category_keywords,
        brands=brands,
        kids=kids,
        shoes=shoes,
        replicas=replicas,
    )


def clear_deal_filters_cache() -> None:
    load_deal_filters.cache_clear()


def _contains_keyword(haystack: str, keyword: str) -> bool:
    if not haystack or not keyword:
        return False
    return bool(re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", haystack))


def _matches_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    if not text:
        return False
    for pattern in patterns:
        try:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def is_adult_size_only(size: str | None, *, config: DealFiltersConfig | None = None) -> bool:
    """True si la taille est clairement adulte (XL, XXL, M…) sans signal enfant."""
    cfg = config or load_deal_filters()
    normalized = _normalize_text(size)
    if not normalized:
        return False
    # "xl", "xxl", "2xl", "taille xl", etc.
    compact = normalized.replace("taille", "").strip()
    if compact in cfg.kids.adult_size_allowlist:
        return True
    # numéros EU adulte courants (veste/pantalon) 34–60
    if re.fullmatch(r"(3[4-9]|[4-5]\d|60)", compact):
        return True
    return False


def is_kids_listing(
    title: str | None,
    size: str | None = None,
    *,
    config: DealFiltersConfig | None = None,
) -> bool:
    """
    Détecte un article enfant via titre + taille uniquement.
    XL / XXL adultes ne sont jamais exclus pour la taille seule.
    Pas de mots ambigus type fille / garçon.
    """
    cfg = config or load_deal_filters()
    if not cfg.settings.reject_kids:
        return False

    title_n = _normalize_text(title)
    size_n = _normalize_text(size)
    blob = f"{title_n} {size_n}".strip()

    for keyword in cfg.kids.title_keywords:
        if _contains_keyword(title_n, keyword):
            return True

    if _matches_any_pattern(blob, cfg.kids.age_patterns):
        return True

    # Taille seule adulte (XL/XXL…) → jamais kids
    if size_n and is_adult_size_only(size_n, config=cfg):
        # sauf si le titre a déjà un signal (géré plus haut) ;
        # ici on ne regarde que des signaux taille kids supplémentaires
        for keyword in cfg.kids.size_keywords:
            if _contains_keyword(size_n, keyword):
                return True
        if _matches_any_pattern(size_n, cfg.kids.size_patterns):
            return True
        return False

    for keyword in cfg.kids.size_keywords:
        if _contains_keyword(size_n, keyword):
            return True
    if _matches_any_pattern(size_n, cfg.kids.size_patterns):
        return True
    # Âge parfois uniquement dans le champ taille ("12 ans")
    if _matches_any_pattern(size_n, cfg.kids.age_patterns):
        return True

    return False


def is_shoe_listing(
    title: str | None,
    *,
    config: DealFiltersConfig | None = None,
) -> bool:
    """Détecte une chaussure via titre / catégories sneaker."""
    cfg = config or load_deal_filters()
    title_n = _normalize_text(title)
    for keyword in cfg.shoes.title_keywords:
        if _contains_keyword(title_n, keyword):
            return True
    category = detect_category(title, config=cfg)
    if category and category in cfg.shoes.categories:
        return True
    return False


def is_clothing_not_shoe(
    title: str | None,
    *,
    config: DealFiltersConfig | None = None,
) -> bool:
    """Vêtement clair (hoodie, casquette…) — prioritaire sur les mots marque type jordan."""
    cfg = config or load_deal_filters()
    title_n = _normalize_text(title)
    for keyword in cfg.shoes.clothing_exclude_keywords:
        if _contains_keyword(title_n, keyword):
            return True
    # Catégorie vêtement détectée sans signal chaussure fort
    if is_shoe_listing(title, config=cfg):
        return False
    category = detect_category(title, config=cfg)
    if category and category not in cfg.shoes.categories and category != "default":
        return True
    return False


def brand_allows_shoes(
    brand: str | None,
    *,
    config: DealFiltersConfig | None = None,
) -> bool:
    cfg = config or load_deal_filters()
    brand_key = _brand_config_key(brand)
    brand_cfg = cfg.brands.get(brand_key) or cfg.brands.get(normalize_brand(brand))
    return bool(brand_cfg and brand_cfg.allow_shoes)


def is_replica_listing(
    title: str | None,
    *,
    config: DealFiltersConfig | None = None,
) -> bool:
    cfg = config or load_deal_filters()
    if not cfg.settings.reject_replicas:
        return False
    title_n = _normalize_text(title)
    for keyword in cfg.replicas.title_keywords:
        if _contains_keyword(title_n, keyword):
            return True
    return False


def _resolve_category(
    title: str | None,
    brand_cfg: BrandDealConfig,
    *,
    config: DealFiltersConfig,
) -> tuple[str | None, CategoryRule | None, bool]:
    """
    Résout catégorie + règle prix.
    Retourne (category, rule, used_fallback).
    Évite de rater des annonces : mapping chaussure + fallback médian.
    """
    allowed = set(brand_cfg.items.keys())
    category = detect_category(
        title,
        allowed=None,  # détecte d'abord globalement
        config=config,
    )

    # Dunk / AF1 → chaussure si la marque n'a que "chaussure"
    if (
        category in config.shoes.categories
        and category not in brand_cfg.items
        and "chaussure" in brand_cfg.items
    ):
        category = "chaussure"

    if category and category in brand_cfg.items:
        return category, brand_cfg.items[category], False

    # Retry limité aux cats de la marque
    category = detect_category(title, allowed=allowed, config=config)
    if category and category in brand_cfg.items:
        return category, brand_cfg.items[category], False

    # Marques sneakers : forcer chaussure si pas de match clair
    if brand_cfg.shoes_only and "chaussure" in brand_cfg.items:
        return "chaussure", brand_cfg.items["chaussure"], True

    if not config.settings.use_category_fallback:
        return None, None, False

    if "default" in brand_cfg.items:
        return "default", brand_cfg.items["default"], True

    # Médiane des règles vêtements (hors chaussures) pour rester conservateur
    clothing = [r for r in brand_cfg.items.values() if r.category not in config.shoes.categories]
    pool = clothing or list(brand_cfg.items.values())
    if not pool:
        return None, None, False

    max_buys = sorted(r.max_buy_price for r in pool)
    resells = sorted(r.average_resell for r in pool)
    profits = sorted(r.minimum_profit for r in pool)
    mid = len(pool) // 2
    rule = CategoryRule(
        category="default",
        max_buy_price=max_buys[mid],
        average_resell=resells[mid],
        minimum_profit=profits[mid],
        rarity="medium",
    )
    return "default", rule, True


def detect_category(
    title: str | None,
    *,
    allowed: set[str] | None = None,
    config: DealFiltersConfig | None = None,
) -> str | None:
    """Détecte la catégorie depuis le titre. Première règle qui matche."""
    cfg = config or load_deal_filters()
    haystack = f" {_normalize_text(title)} "
    for category, keywords in cfg.category_keywords:
        if allowed is not None and category not in allowed:
            continue
        for keyword in keywords:
            if f" {keyword} " in haystack:
                return category
    return None


def category_label(category: str | None) -> str | None:
    if not category:
        return None
    return CATEGORY_LABELS.get(category, category.replace("_", " ").title())


def _published_age_minutes(
    *,
    published_at: datetime | None,
    raw_json: dict[str, Any] | None,
) -> int | None:
    dt: datetime | None = None
    if published_at is not None:
        dt = published_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        raw = raw_json or {}
        created: Any = raw.get("created_at_ts") or raw.get("created_at")
        photo = raw.get("photo")
        if created is None and isinstance(photo, dict):
            hr = photo.get("high_resolution")
            if isinstance(hr, dict):
                created = hr.get("timestamp")
            elif isinstance(hr, list) and hr:
                first = hr[0]
                if isinstance(first, dict):
                    created = first.get("timestamp")
        if isinstance(created, (int, float)):
            dt = datetime.fromtimestamp(float(created), tz=timezone.utc)
        elif isinstance(created, str):
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                dt = None
    if dt is None:
        return None
    seconds = int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    return max(0, seconds // 60)


def _freshness_points(age_minutes: int | None, scoring: ScoringConfig) -> float:
    """Points fraîcheur sur une échelle 0–100."""
    if age_minutes is None:
        return 40.0
    for threshold in sorted(scoring.freshness_minutes.keys()):
        if age_minutes <= threshold:
            # freshness_minutes stocke encore 0–10 → scale ×10
            return float(scoring.freshness_minutes[threshold]) * 10.0
    return 0.0


def _score_deal(
    *,
    buy_price: float,
    rule: CategoryRule,
    brand_score: int,
    age_minutes: int | None,
    scoring: ScoringConfig,
    category_matched: bool = True,
) -> int:
    """Score /100 calibré pour qu'un gros deal (ex. SI sweat 45→150) ≈ PÉPITE."""
    profit = rule.average_resell - buy_price
    weights = scoring.weights
    total_weight = sum(weights.values()) or 1

    # Marge (0–100) : ratio profit / prix de revente, courbe un peu agressive
    margin_ratio = max(0.0, profit / max(rule.average_resell, 1.0))
    margin_pts = min(100.0, margin_ratio * 140.0)

    brand_pts = float(max(0, min(100, brand_score)))

    rarity_pts = float(scoring.rarity_points.get(rule.rarity, 60))
    rarity_pts = max(0.0, min(100.0, rarity_pts))

    # Fallback catégorie → moins de points (mais on ne drop pas l'annonce)
    category_pts = 100.0 if category_matched else 55.0

    discount = (
        max(0.0, 1.0 - (buy_price / rule.average_resell))
        if rule.average_resell > 0
        else 0.0
    )
    below_market_pts = min(100.0, discount * 130.0)

    # Bonus si clairement sous le max_buy
    if rule.max_buy_price > 0 and buy_price <= rule.max_buy_price:
        steal = 1.0 - (buy_price / rule.max_buy_price)
        below_market_pts = min(100.0, below_market_pts + steal * 15.0)

    freshness_pts = _freshness_points(age_minutes, scoring)

    weighted = (
        margin_pts * weights["margin"]
        + brand_pts * weights["brand"]
        + rarity_pts * weights["rarity"]
        + category_pts * weights["category"]
        + below_market_pts * weights["below_market"]
        + freshness_pts * weights["freshness"]
    )
    score = int(round(weighted / total_weight))
    return max(0, min(100, score))


def _level_for_score(score: int, scoring: ScoringConfig) -> DealLevel:
    if score > scoring.pepite:
        return "pepite"
    if score >= scoring.bon_deal:
        return "bon_deal"
    if score >= scoring.surveillance:
        return "surveillance"
    return "skip"


def _reject(
    *,
    reason: str,
    brand_key: str = "",
    brand_display: str = "",
    buy_price: float = 0.0,
    category: str | None = None,
    age_minutes: int | None = None,
) -> DealEvaluation:
    return DealEvaluation(
        should_post=False,
        score=0,
        level="skip",
        level_label=DEAL_LEVEL_LABELS["skip"],
        brand_key=brand_key,
        brand_display=brand_display,
        category=category,
        category_label=category_label(category),
        buy_price=buy_price,
        average_resell=0.0,
        estimated_profit=0.0,
        max_buy_price=0.0,
        minimum_profit=0.0,
        rarity=None,
        reason=reason,
        age_minutes=age_minutes,
    )


def evaluate_deal(
    *,
    brand: str | None,
    title: str | None,
    price_cents: int | None,
    size: str | None = None,
    published_at: datetime | None = None,
    raw_json: dict[str, Any] | None = None,
    config: DealFiltersConfig | None = None,
) -> DealEvaluation:
    """Évalue une annonce. Point d'entrée unique (scrape + Discord + tests)."""
    cfg = config or load_deal_filters()
    age_minutes = _published_age_minutes(
        published_at=published_at,
        raw_json=raw_json,
    )

    # Fraîcheur : ignore les annonces trop vieilles (indépendant du score deal)
    max_age = cfg.settings.max_listing_age_minutes
    if max_age is not None and age_minutes is not None and age_minutes > max_age:
        buy = (price_cents or 0) / 100.0
        return _reject(
            reason="too_old",
            brand_key=_brand_config_key(brand),
            brand_display=brand or "",
            buy_price=buy,
            age_minutes=age_minutes,
        )

    # Replicas / fakes — pas de deal
    if is_replica_listing(title, config=cfg):
        buy = (price_cents or 0) / 100.0
        return _reject(
            reason="replica_item",
            brand_key=_brand_config_key(brand),
            brand_display=brand or "",
            buy_price=buy,
            age_minutes=age_minutes,
        )

    # Enfant : titre + taille uniquement (XL/XXL adultes OK)
    if is_kids_listing(title, size, config=cfg):
        buy = (price_cents or 0) / 100.0
        return _reject(
            reason="kids_item",
            brand_key=_brand_config_key(brand),
            brand_display=brand or "",
            buy_price=buy,
            age_minutes=age_minutes,
        )

    # Chaussures : rejetées sauf marques luxe (allow_shoes: true)
    if (
        cfg.settings.reject_shoes_unless_allowed
        and is_shoe_listing(title, config=cfg)
        and not brand_allows_shoes(brand, config=cfg)
    ):
        buy = (price_cents or 0) / 100.0
        return _reject(
            reason="shoes_not_allowed",
            brand_key=_brand_config_key(brand),
            brand_display=brand or "",
            buy_price=buy,
            age_minutes=age_minutes,
        )

    if not cfg.settings.enabled:
        # Filtre prix désactivé : tout passe (sauf trop vieux ci-dessus)
        buy = (price_cents or 0) / 100.0
        return DealEvaluation(
            should_post=True,
            score=100,
            level="bon_deal",
            level_label=DEAL_LEVEL_LABELS["bon_deal"],
            brand_key=_brand_config_key(brand),
            brand_display=brand or "—",
            category=None,
            category_label=None,
            buy_price=buy,
            average_resell=buy,
            estimated_profit=0.0,
            max_buy_price=buy,
            minimum_profit=0.0,
            rarity=None,
            reason="filter_disabled",
            age_minutes=age_minutes,
        )

    if price_cents is None:
        return _reject(
            reason="missing_price",
            brand_display=brand or "",
            age_minutes=age_minutes,
        )

    buy_price = price_cents / 100.0
    brand_key = _brand_config_key(brand)
    brand_cfg = cfg.brands.get(brand_key) or cfg.brands.get(normalize_brand(brand))

    if brand_cfg is None:
        return _reject(
            reason="brand_not_configured",
            brand_key=brand_key,
            brand_display=brand or brand_key,
            buy_price=buy_price,
            age_minutes=age_minutes,
        )

    # Marques sneakers : uniquement chaussures / baskets (pas de hoodie, casquette…)
    if brand_cfg.shoes_only and is_clothing_not_shoe(title, config=cfg):
        return _reject(
            reason="not_a_shoe",
            brand_key=brand_cfg.key,
            brand_display=brand_cfg.display_name,
            buy_price=buy_price,
            age_minutes=age_minutes,
        )

    category, rule, used_fallback = _resolve_category(title, brand_cfg, config=cfg)
    if category is None or rule is None:
        return _reject(
            reason="category_not_matched",
            brand_key=brand_cfg.key,
            brand_display=brand_cfg.display_name,
            buy_price=buy_price,
            age_minutes=age_minutes,
        )

    estimated_profit = rule.average_resell - buy_price

    # Règle métier : (prix <= max) OU (marge >= min)
    under_max = buy_price <= rule.max_buy_price
    good_margin = estimated_profit >= rule.minimum_profit
    if not under_max and not good_margin:
        return DealEvaluation(
            should_post=False,
            score=0,
            level="skip",
            level_label=DEAL_LEVEL_LABELS["skip"],
            brand_key=brand_cfg.key,
            brand_display=brand_cfg.display_name,
            category=category,
            category_label=category_label(category),
            buy_price=buy_price,
            average_resell=rule.average_resell,
            estimated_profit=estimated_profit,
            max_buy_price=rule.max_buy_price,
            minimum_profit=rule.minimum_profit,
            rarity=rule.rarity,
            reason="price_and_margin_too_weak",
            age_minutes=age_minutes,
        )

    score = _score_deal(
        buy_price=buy_price,
        rule=rule,
        brand_score=brand_cfg.brand_score,
        age_minutes=age_minutes,
        scoring=cfg.scoring,
        category_matched=not used_fallback,
    )
    level = _level_for_score(score, cfg.scoring)
    should_post = score >= cfg.settings.min_score_to_post and level != "skip"

    return DealEvaluation(
        should_post=should_post,
        score=score,
        level=level if should_post else "skip",
        level_label=DEAL_LEVEL_LABELS[level if should_post else "skip"],
        brand_key=brand_cfg.key,
        brand_display=brand_cfg.display_name,
        category=category,
        category_label=category_label(category),
        buy_price=buy_price,
        average_resell=rule.average_resell,
        estimated_profit=estimated_profit,
        max_buy_price=rule.max_buy_price,
        minimum_profit=rule.minimum_profit,
        rarity=rule.rarity,
        reason=("ok_fallback" if used_fallback else "ok") if should_post else "score_too_low",
        age_minutes=age_minutes,
    )


def evaluate_listing(listing: Any, *, config: DealFiltersConfig | None = None) -> DealEvaluation:
    """Adapter pour un objet Listing SQLAlchemy."""
    return evaluate_deal(
        brand=getattr(listing, "brand", None),
        title=getattr(listing, "title", None),
        price_cents=getattr(listing, "price_cents", None),
        size=getattr(listing, "size", None),
        published_at=getattr(listing, "published_at", None),
        raw_json=getattr(listing, "raw_json", None) or {},
        config=config,
    )


def format_age_label(age_minutes: int | None) -> str:
    if age_minutes is None:
        return "—"
    if age_minutes < 1:
        return "à l'instant"
    if age_minutes < 60:
        return f"{age_minutes} minute{'s' if age_minutes > 1 else ''}"
    hours = age_minutes // 60
    if hours < 24:
        return f"{hours} heure{'s' if hours > 1 else ''}"
    days = hours // 24
    return f"{days} jour{'s' if days > 1 else ''}"


def format_price_eur(amount: float) -> str:
    if amount == int(amount):
        return f"{int(amount)}€"
    return f"{amount:.2f}€".replace(".", ",")
