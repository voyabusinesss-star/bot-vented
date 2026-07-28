"""Extraction marque / modèle / mots-clés pour le moteur market-intel."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_bot.db.models import Listing, MarketBrand, MarketKeyword, MarketModel
from vinted_bot.db.repositories import replace_listing_entities
from vinted_bot.notify.discord import (
    VETEMENT_CATEGORIES,
    normalize_brand,
)
from vinted_bot.services.deal_filter import (
    detect_category,
    is_clothing_not_shoe,
    is_shoe_listing,
    load_deal_filters,
)
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
SHOE_CATEGORIES = frozenset({"chaussure", "dunk", "air_force_1"})
SATURATED_BRANDS = frozenset({"nike", "adidas", "jordan"})
# Domaines hors mode — découverte large (objets, jouets, déco, etc.)
EXTENDED_MARKET_CATEGORIES = frozenset(
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
FASHION_MARKET_CATEGORIES = VETEMENT_CATEGORIES | SHOE_CATEGORIES
ALL_MARKET_CATEGORIES = FASHION_MARKET_CATEGORIES | EXTENDED_MARKET_CATEGORIES


@dataclass(slots=True, frozen=True)
class ModelDef:
    slug: str
    display_name: str
    brand: str | None
    aliases: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class KeywordDef:
    slug: str
    display_name: str
    kind: str
    aliases: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class CategoryDef:
    slug: str
    domain: str
    aliases: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class ExtractedEntities:
    brand_slug: str
    category_slug: str | None
    model_slug: str | None
    keyword_slugs: tuple[str, ...]
    in_domain: bool

    @property
    def niche_key(self) -> str:
        flags = "+".join(self.keyword_slugs[:3]) if self.keyword_slugs else ""
        return "|".join(
            [
                self.brand_slug or "",
                self.model_slug or "",
                self.category_slug or "",
                flags,
            ]
        )


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("-", " ").replace("_", " ")
    text = re.sub(r"[^\w\s+x]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _alias_pattern(alias: str) -> re.Pattern[str]:
    cleaned = _normalize_text(alias).strip()
    if not cleaned:
        return re.compile(r"(?!)")
    # Permet alias numériques courts (501, 550) et tokens
    escaped = re.escape(cleaned).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


@lru_cache(maxsize=1)
def load_model_defs() -> tuple[ModelDef, ...]:
    path = CONFIG_DIR / "market_models.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    models: list[ModelDef] = []
    for row in raw.get("models") or []:
        if not isinstance(row, dict) or not row.get("slug"):
            continue
        aliases = [str(a) for a in (row.get("aliases") or []) if str(a).strip()]
        brand = normalize_brand(row.get("brand")) or None
        models.append(
            ModelDef(
                slug=str(row["slug"]).strip().lower(),
                display_name=str(row.get("display_name") or row["slug"]),
                brand=brand,
                aliases=tuple(aliases),
            )
        )
    # Plus d'aliases longs d'abord pour éviter dunk avant dunk_sb
    models.sort(key=lambda m: max((len(a) for a in m.aliases), default=0), reverse=True)
    return tuple(models)


@lru_cache(maxsize=1)
def load_keyword_defs() -> tuple[KeywordDef, ...]:
    path = CONFIG_DIR / "market_keywords.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    keywords: list[KeywordDef] = []
    for row in raw.get("keywords") or []:
        if not isinstance(row, dict) or not row.get("slug"):
            continue
        aliases = [str(a) for a in (row.get("aliases") or []) if str(a).strip()]
        keywords.append(
            KeywordDef(
                slug=str(row["slug"]).strip().lower(),
                display_name=str(row.get("display_name") or row["slug"]),
                kind=str(row.get("kind") or "style"),
                aliases=tuple(aliases),
            )
        )
    keywords.sort(key=lambda k: max((len(a) for a in k.aliases), default=0), reverse=True)
    return tuple(keywords)


@lru_cache(maxsize=1)
def load_category_defs() -> tuple[CategoryDef, ...]:
    path = CONFIG_DIR / "market_categories.yaml"
    if not path.exists():
        return ()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cats: list[CategoryDef] = []
    for row in raw.get("categories") or []:
        if not isinstance(row, dict) or not row.get("slug"):
            continue
        aliases = [str(a) for a in (row.get("aliases") or []) if str(a).strip()]
        cats.append(
            CategoryDef(
                slug=str(row["slug"]).strip().lower(),
                domain=str(row.get("domain") or "objects"),
                aliases=tuple(aliases),
            )
        )
    cats.sort(key=lambda c: max((len(a) for a in c.aliases), default=0), reverse=True)
    return tuple(cats)


def detect_extended_category(title: str | None) -> str | None:
    """Catégories objets / jouets / déco / électronique (market-intel)."""
    haystack = f" {_normalize_text(title)} "
    if not haystack.strip():
        return None
    for cat in load_category_defs():
        for alias in cat.aliases:
            if _alias_pattern(alias).search(haystack):
                return cat.slug
    return None


def detect_market_category(title: str | None) -> str | None:
    """Catégorie mode (deal_filter) puis catégories élargies market-intel."""
    category = detect_category(title)
    if category in SHOE_CATEGORIES:
        return "chaussure"
    if category and category in VETEMENT_CATEGORIES:
        return category
    return detect_extended_category(title)


def category_domain(category_slug: str | None) -> str:
    """Domaine produit pour diversification Discord."""
    if not category_slug:
        return "unknown"
    if category_slug in SHOE_CATEGORIES or category_slug == "chaussure":
        return "shoes"
    if category_slug in VETEMENT_CATEGORIES:
        return "fashion"
    for cat in load_category_defs():
        if cat.slug == category_slug:
            return cat.domain
    return "objects"


def is_analyzable_listing(title: str | None, *, brand: str | None = None) -> bool:
    """Toute annonce Vinted digne d'analyse (multi-catégories, pas mode-only)."""
    if title and len(title.strip()) >= 3:
        return True
    if brand and str(brand).strip() and str(brand).strip().lower() not in {
        "inconnu",
        "unknown",
        "?",
    }:
        return True
    return False


def is_market_domain(title: str | None, *, brand: str | None = None) -> bool:
    """Domaine marché large : mode, sneakers, objets… et tout listing analysable.

    Le détecteur ne se limite plus aux vêtements/chaussures : une annonce
    hors mode (maison, électronique, jouets, etc.) reste dans le périmètre.
    """
    if is_analyzable_listing(title, brand=brand):
        return True
    if is_shoe_listing(title):
        return True
    if is_clothing_not_shoe(title):
        return True
    category = detect_market_category(title)
    if category and category in ALL_MARKET_CATEGORIES:
        return True
    # Modèle catalogue connu (licence / objet) même sans marque fashion
    if detect_model(title, brand=brand):
        return True
    # Mot-clé licence / collection dans le titre
    for kw in detect_keywords(title):
        if kw.kind in {"license", "collection", "product"}:
            return True
    # Marque connue (deal filters) + titre non vide
    if normalize_brand(brand) and title and len(title.strip()) >= 4:
        cfg = load_deal_filters()
        brand_key = normalize_brand(brand)
        if brand_key in cfg.brands:
            return True
    return False


def detect_model(
    title: str | None,
    *,
    brand: str | None = None,
    description: str | None = None,
) -> ModelDef | None:
    haystack = f" {_normalize_text(title)} {_normalize_text(description)} "
    brand_slug = normalize_brand(brand)
    for model in load_model_defs():
        if model.brand and brand_slug and model.brand != brand_slug:
            # Autoriser match si la marque du modèle apparaît dans le titre
            if model.brand not in haystack and brand_slug != model.brand:
                continue
        for alias in model.aliases:
            if _alias_pattern(alias).search(haystack):
                return model
    return None


def detect_keywords(
    title: str | None,
    *,
    description: str | None = None,
) -> list[KeywordDef]:
    haystack = f" {_normalize_text(title)} {_normalize_text(description)} "
    found: list[KeywordDef] = []
    seen: set[str] = set()
    for keyword in load_keyword_defs():
        for alias in keyword.aliases:
            if _alias_pattern(alias).search(haystack):
                if keyword.slug not in seen:
                    found.append(keyword)
                    seen.add(keyword.slug)
                break
    return found


def extract_entities_from_text(
    *,
    title: str | None,
    brand: str | None = None,
    description: str | None = None,
) -> ExtractedEntities:
    brand_slug = normalize_brand(brand) or "inconnu"
    category = detect_market_category(title)
    model = detect_model(title, brand=brand, description=description)
    # Si le modèle a une marque et le listing n'en a pas : rattacher la marque produit
    if model and model.brand and brand_slug in {"", "inconnu"}:
        brand_slug = model.brand
    keywords = detect_keywords(title, description=description)
    in_domain = is_market_domain(title, brand=brand)
    return ExtractedEntities(
        brand_slug=brand_slug,
        category_slug=category,
        model_slug=model.slug if model else None,
        keyword_slugs=tuple(k.slug for k in keywords),
        in_domain=in_domain,
    )


def _description_from_raw(raw_json: dict[str, Any] | None) -> str | None:
    if not isinstance(raw_json, dict):
        return None
    for key in ("description", "item_description", "subtitle"):
        value = raw_json.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def ensure_market_catalog(session: Session) -> None:
    """Upsert brands/models/keywords depuis YAML (idempotent)."""
    brand_slugs = {
        m.brand for m in load_model_defs() if m.brand
    }
    for slug in sorted(brand_slugs):
        existing = session.scalar(select(MarketBrand).where(MarketBrand.slug == slug))
        if existing is None:
            session.add(
                MarketBrand(slug=slug, display_name=slug.replace("_", " ").title())
            )
    session.flush()
    brand_ids = {
        b.slug: b.id
        for b in session.scalars(select(MarketBrand)).all()
    }
    for model in load_model_defs():
        existing = session.scalar(
            select(MarketModel).where(MarketModel.slug == model.slug)
        )
        brand_id = brand_ids.get(model.brand) if model.brand else None
        if existing is None:
            session.add(
                MarketModel(
                    slug=model.slug,
                    display_name=model.display_name,
                    brand_id=brand_id,
                    aliases=list(model.aliases),
                )
            )
        else:
            existing.display_name = model.display_name
            existing.brand_id = brand_id
            existing.aliases = list(model.aliases)
    for keyword in load_keyword_defs():
        existing = session.scalar(
            select(MarketKeyword).where(MarketKeyword.slug == keyword.slug)
        )
        if existing is None:
            session.add(
                MarketKeyword(
                    slug=keyword.slug,
                    display_name=keyword.display_name,
                    kind=keyword.kind,
                    aliases=list(keyword.aliases),
                )
            )
        else:
            existing.display_name = keyword.display_name
            existing.kind = keyword.kind
            existing.aliases = list(keyword.aliases)
    session.flush()


def enrich_listing_entities(session: Session, listing: Listing) -> ExtractedEntities:
    """Enrichit listing + listing_entities à partir du titre / raw_json."""
    description = _description_from_raw(
        listing.raw_json if isinstance(listing.raw_json, dict) else None
    )
    extracted = extract_entities_from_text(
        title=listing.title,
        brand=listing.brand,
        description=description,
    )
    listing.category_slug = extracted.category_slug
    listing.model_slug = extracted.model_slug
    listing.keyword_slugs = list(extracted.keyword_slugs)

    entities: list[tuple[str, str, int]] = []
    if extracted.brand_slug and extracted.brand_slug != "inconnu":
        entities.append(("brand", extracted.brand_slug, 100))
    if extracted.category_slug:
        entities.append(("category", extracted.category_slug, 90))
    if extracted.model_slug:
        entities.append(("model", extracted.model_slug, 85))
    for kw in extracted.keyword_slugs:
        entities.append(("keyword", kw, 80))
    replace_listing_entities(session, listing.id, entities)
    session.flush()
    return extracted


def backfill_listing_entities(
    session: Session,
    *,
    limit: int = 2000,
) -> int:
    ensure_market_catalog(session)
    stmt = (
        select(Listing)
        .where(
            Listing.category_slug.is_(None)
            | Listing.model_slug.is_(None)
            | Listing.keyword_slugs.is_(None)
        )
        .order_by(Listing.id.desc())
        .limit(max(1, limit))
    )
    listings = list(session.scalars(stmt).all())
    count = 0
    for listing in listings:
        if not is_market_domain(listing.title, brand=listing.brand):
            continue
        enrich_listing_entities(session, listing)
        count += 1
    return count


def brand_saturation_penalty(brand_slug: str | None, model_slug: str | None) -> float:
    """Pénalise marques saturées sans modèle précis."""
    if not brand_slug:
        return 1.0
    if brand_slug in SATURATED_BRANDS and not model_slug:
        return 2.5
    if brand_slug in SATURATED_BRANDS:
        return 1.2
    return 1.0
