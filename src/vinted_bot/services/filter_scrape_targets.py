"""Convertit les filtres privés actifs en cibles de scrape Vinted."""

from __future__ import annotations

import threading
import time
import unicodedata
from typing import Any, Sequence

from vinted_bot.config_loader import SearchTarget, brand_ids_lookup, load_searches_config
from vinted_bot.db.session import session_scope
from vinted_bot.db.user_filters import list_all_active_filters
from vinted_bot.notify.discord import normalize_brand
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

# Cap global de cibles Vinted dédupliquées (pas de scrape parallèle).
# Injectées 1–2 à la fois dans le BrandWorker unique (anti-OOM).
DEFAULT_FILTER_CAP = 50
FILTER_INJECT_PER_PULSE = 1
FILTER_TARGETS_REFRESH_SECONDS = 60.0
CLOTHING_CATALOG_IDS = [4, 5]
SHOE_CATALOG_IDS = [1231, 1242]

_SHOE_CATEGORY_HINTS = (
    "chaussure",
    "chaussures",
    "shoe",
    "shoes",
    "sneaker",
    "sneakers",
    "basket",
    "baskets",
    "tn",
    "dunk",
    "jordan",
    "yeezy",
)


def _fold(text: str | None) -> str:
    if not text:
        return ""
    raw = unicodedata.normalize("NFKD", str(text))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return " ".join(raw.lower().replace("-", " ").replace("_", " ").split())


def _catalog_ids_for_category(category: str | None) -> list[int]:
    cat = _fold(category)
    if not cat:
        return list(CLOTHING_CATALOG_IDS)
    if any(h in cat or cat in h for h in _SHOE_CATEGORY_HINTS):
        return list(SHOE_CATALOG_IDS)
    return list(CLOTHING_CATALOG_IDS)


def _query_for_filter(
    *,
    brand: str | None,
    model: str | None,
    keyword: str | None,
    has_brand_ids: bool,
) -> str:
    """Texte de recherche : keyword → model → brand (si pas de brand_ids)."""
    bits: list[str] = []
    if keyword and str(keyword).strip():
        bits.append(str(keyword).strip())
    if model and str(model).strip():
        m = str(model).strip()
        if _fold(m) not in _fold(" ".join(bits)):
            bits.append(m)
    if not has_brand_ids and brand and str(brand).strip():
        b = str(brand).strip()
        if _fold(b) not in _fold(" ".join(bits)):
            bits.insert(0, b)
    if bits:
        return " ".join(bits)
    if brand:
        return str(brand).strip()
    return ""


def filter_row_to_search_target(
    row: Any,
    *,
    brand_ids_map: dict[str, list[int]] | None = None,
) -> SearchTarget | None:
    """Une ligne UserFilter → SearchTarget, ou None si pas de critère scrapeable."""
    brand_raw = (getattr(row, "brand", None) or "").strip() or None
    model = (getattr(row, "model", None) or "").strip() or None
    keyword = (getattr(row, "keyword", None) or "").strip() or None
    category = (getattr(row, "category", None) or "").strip() or None
    max_price = getattr(row, "max_price_eur", None)
    min_price = getattr(row, "min_price_eur", None)

    brand_key = normalize_brand(brand_raw) if brand_raw else ""
    ids_map = brand_ids_map if brand_ids_map is not None else brand_ids_lookup()
    brand_ids = list(ids_map.get(brand_key) or []) if brand_key else []

    query = _query_for_filter(
        brand=brand_raw,
        model=model,
        keyword=keyword,
        has_brand_ids=bool(brand_ids),
    )
    if not query and not brand_ids:
        # Prix seul : pas de scrape ciblé fiable
        return None

    brand_label = brand_key or "filter"
    catalog_ids = _catalog_ids_for_category(category)

    return SearchTarget(
        brand=brand_label,
        query=query or brand_label,
        enabled=True,
        priority="low",  # ne pas affamer les marques YAML si fusionnées
        brand_ids=brand_ids,
        catalog_ids=catalog_ids,
        order="newest_first",
        max_items=8,
        max_discord_posts=0,
        price_from=float(min_price) if min_price is not None else None,
        price_to=float(max_price) if max_price is not None else None,
        source="user_filter",
    )


def active_filter_search_targets(
    *,
    max_targets: int = DEFAULT_FILTER_CAP,
    filters: Sequence[Any] | None = None,
) -> list[SearchTarget]:
    """Cibles scrape dédupliquées depuis les filtres privés actifs."""
    from types import SimpleNamespace

    if filters is None:
        with session_scope() as session:
            rows = list_all_active_filters(session)
            filters = [
                SimpleNamespace(
                    id=int(r.id),
                    brand=r.brand,
                    model=r.model,
                    category=r.category,
                    keyword=r.keyword,
                    min_price_eur=r.min_price_eur,
                    max_price_eur=r.max_price_eur,
                    is_active=bool(r.is_active),
                )
                for r in rows
            ]

    ids_map = brand_ids_lookup()
    cfg = load_searches_config()
    seen: set[tuple[Any, ...]] = set()
    out: list[SearchTarget] = []

    for row in filters:
        if not getattr(row, "is_active", True):
            continue
        target = filter_row_to_search_target(row, brand_ids_map=ids_map)
        if target is None:
            continue
        key = (
            tuple(target.brand_ids),
            target.query.strip().lower(),
            tuple(target.catalog_ids),
            target.price_from,
            target.price_to,
        )
        if key in seen:
            continue
        seen.add(key)
        if not target.order:
            target.order = cfg.order
        out.append(target)
        if len(out) >= max(1, max_targets):
            break

    log.info(
        "filter_scrape_targets",
        count=len(out),
        active_filters=len(filters),
        queries=[t.query for t in out[:12]],
        brands=[t.brand for t in out[:12]],
    )
    return out


class FilterTargetRotator:
    """Round-robin des cibles filtre pour injection dans le BrandWorker (1 Chromium)."""

    def __init__(
        self,
        *,
        max_targets: int = DEFAULT_FILTER_CAP,
        refresh_seconds: float = FILTER_TARGETS_REFRESH_SECONDS,
    ) -> None:
        self.max_targets = max(1, int(max_targets))
        self.refresh_seconds = max(15.0, float(refresh_seconds))
        self._lock = threading.Lock()
        self._targets: list[SearchTarget] = []
        self._idx = 0
        self._loaded_at = 0.0

    def _refresh_locked(self) -> None:
        now = time.monotonic()
        if self._targets and (now - self._loaded_at) < self.refresh_seconds:
            return
        try:
            self._targets = active_filter_search_targets(max_targets=self.max_targets)
        except Exception as exc:  # noqa: BLE001
            log.warning("filter_targets_refresh_failed", error=str(exc)[:160])
            if not self._targets:
                self._targets = []
            return
        self._loaded_at = now
        if self._idx >= len(self._targets):
            self._idx = 0
        log.info(
            "filter_targets_refreshed",
            count=len(self._targets),
            max_targets=self.max_targets,
        )

    def next_batch(self, n: int = FILTER_INJECT_PER_PULSE) -> list[SearchTarget]:
        """Retourne jusqu'à n cibles (round-robin), liste vide si aucun filtre."""
        take = max(0, int(n))
        if take <= 0:
            return []
        with self._lock:
            self._refresh_locked()
            if not self._targets:
                return []
            out: list[SearchTarget] = []
            for _ in range(min(take, len(self._targets))):
                out.append(self._targets[self._idx % len(self._targets)])
                self._idx = (self._idx + 1) % len(self._targets)
            return out

    def size(self) -> int:
        with self._lock:
            self._refresh_locked()
            return len(self._targets)


_rotator: FilterTargetRotator | None = None
_rotator_lock = threading.Lock()


def get_filter_target_rotator() -> FilterTargetRotator:
    global _rotator
    with _rotator_lock:
        if _rotator is None:
            _rotator = FilterTargetRotator()
        return _rotator
