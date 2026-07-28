"""Parser résultats de recherche Vinted (JSON catalog)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

BASE_URL = "https://www.vinted.fr"


@dataclass(slots=True)
class SearchItem:
    vinted_id: int
    title: str
    url: str
    price_cents: int | None = None
    currency: str = "EUR"
    brand: str | None = None
    size: str | None = None
    published_at: datetime | None = None
    photo_urls: list[str] = field(default_factory=list)
    raw_json: dict[str, Any] = field(default_factory=dict)


def extract_published_at(item: dict[str, Any]) -> datetime | None:
    """Extrait la date de publication depuis le JSON catalog Vinted."""
    created: Any = item.get("created_at_ts") or item.get("created_at")
    photo = item.get("photo")
    if created is None and isinstance(photo, dict):
        hr = photo.get("high_resolution")
        if isinstance(hr, dict):
            created = hr.get("timestamp")
        elif isinstance(hr, list) and hr:
            first = hr[0]
            if isinstance(first, dict):
                created = first.get("timestamp")
    if isinstance(created, (int, float)):
        return datetime.fromtimestamp(float(created), tz=timezone.utc)
    if isinstance(created, str):
        try:
            return datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _to_cents(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, dict):
        # formats fréquents: {"amount": "12.50", ...} ou {"numeric": 12.5}
        if "amount" in value:
            return _to_cents(value["amount"])
        if "numeric" in value:
            return _to_cents(value["numeric"])
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value) * 100))
    if isinstance(value, str):
        cleaned = value.replace(",", ".").strip()
        cleaned = re.sub(r"[^\d.]", "", cleaned)
        if not cleaned:
            return None
        return int(round(float(cleaned) * 100))
    return None


def _photo_urls(item: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    photo = item.get("photo") or {}
    if isinstance(photo, dict):
        for key in ("url", "full_size_url", "high_resolution_url"):
            if photo.get(key):
                urls.append(str(photo[key]))
                break
        for thumb in photo.get("high_resolution") or []:
            if isinstance(thumb, dict) and thumb.get("url"):
                urls.append(str(thumb["url"]))
    for photo in item.get("photos") or []:
        if isinstance(photo, dict) and photo.get("url"):
            urls.append(str(photo["url"]))
    # dedup en gardant l'ordre
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def parse_catalog_item(item: dict[str, Any], *, base_url: str = BASE_URL) -> SearchItem | None:
    raw_id = item.get("id")
    if raw_id is None:
        return None
    try:
        vinted_id = int(raw_id)
    except (TypeError, ValueError):
        return None

    title = str(item.get("title") or item.get("name") or "").strip()
    if not title:
        title = f"Item {vinted_id}"

    path = item.get("url") or item.get("path") or f"/items/{vinted_id}"
    url = str(path)
    if url.startswith("/"):
        url = urljoin(base_url, url)
    elif not url.startswith("http"):
        url = urljoin(base_url, f"/items/{vinted_id}")

    brand = None
    brand_obj = item.get("brand_title") or item.get("brand")
    if isinstance(brand_obj, dict):
        brand = brand_obj.get("title") or brand_obj.get("name")
    elif isinstance(brand_obj, str):
        brand = brand_obj

    size = None
    size_obj = item.get("size_title") or item.get("size")
    if isinstance(size_obj, dict):
        size = size_obj.get("title") or size_obj.get("name")
    elif isinstance(size_obj, str):
        size = size_obj

    price_cents = _to_cents(
        item.get("price")
        or item.get("total_item_price")
        or item.get("price_numeric")
    )
    currency = "EUR"
    price_obj = item.get("price")
    if isinstance(price_obj, dict) and price_obj.get("currency_code"):
        currency = str(price_obj["currency_code"])

    return SearchItem(
        vinted_id=vinted_id,
        title=title,
        url=url,
        price_cents=price_cents,
        currency=currency,
        brand=brand,
        size=size,
        published_at=extract_published_at(item),
        photo_urls=_photo_urls(item),
        raw_json=item,
    )


def parse_catalog_payload(payload: dict[str, Any], *, base_url: str = BASE_URL) -> list[SearchItem]:
    items_raw = payload.get("items") or payload.get("catalog_items") or []
    results: list[SearchItem] = []
    for item in items_raw:
        if not isinstance(item, dict):
            continue
        parsed = parse_catalog_item(item, base_url=base_url)
        if parsed is not None:
            results.append(parsed)
    return results
