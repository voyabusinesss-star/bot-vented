"""Récupération profil Vinted (adresse, livraison, paiement) via session membre."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, sync_playwright

from vinted_bot.clients.playwright_browser import apply_vinted_stealth, launch_vinted_browser
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_BASE_URL = "https://www.vinted.fr"

CAPTCHA_URL_MARKERS: tuple[str, ...] = (
    "captcha-delivery.com",
    "geo.captcha-delivery",
    "datadome",
    "/captcha/",
    "cf-browser-verification",
    "challenge-platform",
)

CAPTCHA_TEXT_MARKERS: tuple[str, ...] = (
    "anti-robot",
    "captcha",
    "je ne suis pas un robot",
    "press & hold",
    "vérification de sécurité",
    "unusual traffic",
    "accès refusé",
)

CAPTCHA_HELP_MESSAGE = (
    "Vinted a demandé une vérification anti-robot. "
    "Valide le captcha dans la fenêtre Chrome, puis réessaie."
)

BUY_SELECTORS: tuple[str, ...] = (
    "[data-testid='item-buy-button']",
    "[data-testid='buy-button']",
    "button:has-text('Acheter')",
    "button:has-text('Buy now')",
)

TARGET_CARRIER_ORDER: tuple[str, ...] = ("Mondial Relay", "Chronopost", "Vinted Go")

PURCHASE_ID_PATTERNS: tuple[str, ...] = (
    r"purchase_id=([^&]+)",
    r"/api/v2/purchases/([^/?#]+)/checkout",
)

INVALID_PURCHASE_ID_TOKENS: frozenset[str] = frozenset(
    {"checkout", "create", "new", "build", "status", "summary"}
)

CATALOG_SEARCH_QUERIES: tuple[str, ...] = ("accessoire", "tee", "pull", "chaussure")
MAX_CHECKOUT_ITEM_PRICE_EUR = 15.0
MAX_CHECKOUT_ITEM_ATTEMPTS = 4
PROFILE_CHECKOUT_BUDGET_S = 35
CAPTCHA_WAIT_S = 90

PICKUP_POINT_API_SUFFIXES: tuple[str, ...] = (
    "/pickup_points/shipping_point?shipping_rate_uuid={rate_uuid}",
    "/pickup_point?shipping_rate_uuid={rate_uuid}",
    "/pickup_points?shipping_rate_uuid={rate_uuid}",
)


SESSION_EXPIRED_MARKERS: tuple[str, ...] = (
    "/users/login",
    "/member/signup",
    "signup/select",
)
SESSION_REFRESH_MARKER = "session-refresh"


@dataclass(slots=True)
class VintedProfileInfo:
    username: str = "Compte Vinted"
    avatar_url: str | None = None
    address: str | None = None
    delivery_points: list[str] = field(default_factory=list)
    payment_label: str | None = None
    error: str | None = None


def _fetch_json(page: Page, url: str) -> object | None:
    try:
        return page.evaluate(
            """async (url) => {
                const res = await fetch(url, {
                    headers: { Accept: 'application/json' },
                    credentials: 'include',
                });
                if (!res.ok) return null;
                return await res.json();
            }""",
            url,
        )
    except Exception:
        return None


def _session_expired_url(url: str) -> bool:
    lower = url.lower()
    return any(marker in lower for marker in SESSION_EXPIRED_MARKERS)


def _current_user_payload(page: Page, base_url: str) -> dict[str, Any] | None:
    user = _fetch_json(page, f"{base_url}/api/v2/users/current")
    if not isinstance(user, dict):
        return None
    block = user.get("user") if isinstance(user.get("user"), dict) else user
    return block if isinstance(block, dict) else None


def _ensure_vinted_session(page: Page, base_url: str, *, attempts: int = 10) -> bool:
    """Charge la session Vinted (gère session-refresh) ou détecte une expiration."""
    # Homepage d'abord : /member/general renvoie souvent vers signup si anti-bot.
    for start_url in (f"{base_url}/", f"{base_url}/member/general"):
        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=15_000)
        except Exception:
            continue
        for _ in range(attempts):
            url = page.url
            if _session_expired_url(url):
                break
            if SESSION_REFRESH_MARKER in url.lower():
                page.wait_for_timeout(400)
                continue
            user_block = _current_user_payload(page, base_url)
            if user_block is not None and (user_block.get("login") or user_block.get("username")):
                return True
            page.wait_for_timeout(400)
        if _current_user_payload(page, base_url) is not None:
            return True
    log.warning("vinted_session_ensure_failed", url=page.url)
    return False


def _first_dict_list(payload: object, *keys: str) -> list[object]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _format_address(entry: dict[str, Any]) -> str:
    line1 = str(entry.get("line1") or entry.get("street") or entry.get("address_line1") or "").strip()
    postal = str(entry.get("postal_code") or entry.get("zip_code") or "").strip()
    city = str(entry.get("city") or "").strip()
    name = str(entry.get("name") or "").strip()

    parts = [p for p in (line1, " ".join(p for p in (postal, city) if p)) if p]
    formatted = ", ".join(parts)
    if name and formatted:
        return f"{name} — {formatted}"
    return formatted or name or ""


def _pick_address_entry(items: list[object], username: str) -> dict[str, Any] | None:
    dict_items = [item for item in items if isinstance(item, dict)]
    if not dict_items:
        return None

    login = username.lower().strip()
    login_compact = login.replace(" ", "")
    name_tokens = [t for t in re.split(r"[\s._-]+", login) if len(t) >= 4]

    def score(item: dict[str, Any]) -> tuple[int, int]:
        name = str(item.get("name") or "").lower()
        name_compact = name.replace(" ", "")
        points = 0
        if item.get("entry_type") == 1:
            points += 8
        if login_compact and login_compact in name_compact:
            points += 10
        if any(token in name for token in name_tokens):
            points += 5
        return (points, -int(item.get("id") or 0))

    return max(dict_items, key=score)


def _pick_card_entry(cards: list[object]) -> dict[str, Any] | None:
    dict_cards = [card for card in cards if isinstance(card, dict)]
    if not dict_cards:
        return None
    for card in dict_cards:
        if card.get("default") and not card.get("expired"):
            return card
    for card in dict_cards:
        if not card.get("expired"):
            return card
    return dict_cards[0]


def _format_payment(card: dict[str, Any]) -> str:
    owner = str(card.get("owner_name") or "Carte").strip()
    brand = str(card.get("brand") or "").strip()
    last4 = str(card.get("last4") or "????").strip()
    brand_part = f"{brand} " if brand else ""
    return f"{owner} | {brand_part}**** {last4}"


def _carrier_label(option: dict[str, Any]) -> str:
    title = str(option.get("title") or "").strip()
    if title:
        return title
    first_mile = option.get("first_mile_carrier")
    if isinstance(first_mile, dict):
        return str(first_mile.get("name") or "").strip()
    return ""


def _carrier_display_name(*, code: str = "", title: str = "") -> str | None:
    blob = f"{code} {title}".upper()
    if "MONDIAL" in blob:
        return "Mondial Relay"
    if "CHRONO" in blob:
        return "Chronopost"
    if "VINTED" in blob:
        return "Vinted Go"
    return None


def _shipping_order_id(components: dict[str, Any]) -> int | None:
    for key in ("shipping_pickup_options", "shipping_pickup_details", "shipping_address"):
        block = components.get(key)
        if isinstance(block, dict):
            raw = block.get("shipping_order_id")
            if isinstance(raw, int):
                return raw
            if isinstance(raw, str) and raw.isdigit():
                return int(raw)
    return None


def _rate_uuid(option: dict[str, Any]) -> str | None:
    for key in ("rate_uuid", "selected_rate_uuid"):
        value = option.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _point_from_payload(payload: object) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        for key in ("shipping_point", "pickup_point", "point"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        if payload.get("name") and (
            payload.get("address_line1") or payload.get("line1") or payload.get("postal_code")
        ):
            return payload
    return None


def _fetch_pickup_point_for_rate(
    page: Page,
    base_url: str,
    *,
    shipping_order_id: int,
    rate_uuid: str,
) -> dict[str, Any] | None:
    for suffix in PICKUP_POINT_API_SUFFIXES:
        url = f"{base_url}/api/v2/shipping_orders/{shipping_order_id}{suffix.format(rate_uuid=rate_uuid)}"
        payload = _fetch_json(page, url)
        point = _point_from_payload(payload)
        if point is not None:
            return point
    return None


def _format_shipping_point(point: dict[str, Any], *, carrier_title: str = "") -> str:
    name = str(point.get("name") or "").strip()
    line1 = str(point.get("address_line1") or point.get("line1") or "").strip()
    postal = str(point.get("postal_code") or "").strip()
    city = str(point.get("city") or "").strip()
    location = ", ".join(p for p in (line1, " ".join(p for p in (postal, city) if p)) if p)

    label = name
    if carrier_title:
        label = f"{name} ({carrier_title})" if name else carrier_title
    if location and label:
        return f"{label} · {location}"
    return label or location or carrier_title


def _format_home_delivery(
    address: dict[str, Any] | None,
    shipping_option: dict[str, Any] | None,
) -> str:
    carrier = _carrier_label(shipping_option) if isinstance(shipping_option, dict) else ""
    if isinstance(address, dict):
        formatted = _format_address(address)
        if carrier:
            return f"Domicile — {formatted} ({carrier})"
        return f"Domicile — {formatted}"
    if carrier:
        return f"Domicile — {carrier}"
    return "Domicile — Envoi à domicile"


def _pick_shipping_option(option_block: dict[str, Any]) -> dict[str, Any] | None:
    direct = option_block.get("shipping_option")
    if isinstance(direct, dict):
        return direct
    options = option_block.get("shipping_options")
    if isinstance(options, list):
        for entry in options:
            if isinstance(entry, dict):
                return entry
    return None


def _is_home_delivery_label(label: str) -> bool:
    lower = label.strip().lower()
    return lower.startswith("domicile") or "envoi à domicile" in lower or "envoi a domicile" in lower


def _append_unique(points: list[str], label: str) -> None:
    cleaned = label.strip()
    if not cleaned or _is_home_delivery_label(cleaned) or _is_junk_pickup_label(cleaned):
        return
    if cleaned not in points:
        points.append(cleaned)


def _is_junk_pickup_label(label: str) -> bool:
    """Filtre les faux positifs (footer / nav) type « Vinted Go — À propos de Vinted »."""
    lower = label.strip().lower()
    if not lower or _is_home_delivery_label(lower):
        return True
    junk_markers = (
        "à propos",
        "a propos",
        "vinted pro",
        "conditions",
        "cookie",
        "centre d'aide",
        "à configurer",
        "a configurer",
        "non configuré",
        "non configure",
        "depuis un relais",
        "envoi au point relais",
    )
    if any(marker in lower for marker in junk_markers):
        return True
    # Label = uniquement un transporteur, sans nom de locker.
    carriers = {c.lower() for c in TARGET_CARRIER_ORDER} | {"point relais", "mondial relay", "vinted go"}
    stripped = lower
    for prefix in ("mondial relay —", "chronopost —", "vinted go —", "point relais —"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :].strip()
    if stripped in carriers or stripped in {"vinted", "vinted go"}:
        return True
    return False


def _filter_pickup_only(points: list[str]) -> list[str]:
    return [
        point
        for point in points
        if not _is_home_delivery_label(point) and not _is_junk_pickup_label(point)
    ]


def _has_carrier_pickup_points(points: list[str]) -> bool:
    """Vrai s'il y a au moins un vrai nom de point relais enregistré."""
    return bool(_filter_pickup_only(points))


def _dismiss_cookies(page: Page) -> None:
    for selector in (
        "button:has-text('Tout accepter')",
        "button:has-text('Accept all')",
        "[id*='onetrust-accept']",
    ):
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=1200):
                btn.click(timeout=2000)
                return
        except Exception:
            continue


def _is_valid_purchase_id(purchase_id: str) -> bool:
    cleaned = purchase_id.strip()
    if not cleaned or cleaned.lower() in INVALID_PURCHASE_ID_TOKENS:
        return False
    if cleaned.isdigit():
        return len(cleaned) >= 6
    # Vinted utilise des tokens opaques (base64url) ou des UUID.
    return bool(re.fullmatch(r"[0-9A-Za-z_-]{8,}", cleaned))


def _purchase_id_from_payload(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None

    candidates: list[object] = []
    for key in ("purchase_id", "id", "uuid"):
        value = payload.get(key)
        if value is not None:
            candidates.append(value)

    for nested_key in ("purchase", "checkout", "transaction"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            for key in ("purchase_id", "id", "uuid"):
                value = nested.get(key)
                if value is not None:
                    candidates.append(value)

    checkout_block = payload.get("checkout")
    if isinstance(checkout_block, dict):
        purchase = checkout_block.get("purchase")
        if isinstance(purchase, dict):
            for key in ("purchase_id", "id", "uuid"):
                value = purchase.get(key)
                if value is not None:
                    candidates.append(value)

    for candidate in candidates:
        token = str(candidate).strip()
        if _is_valid_purchase_id(token):
            return token
    return None


def _extract_purchase_id_from_url(url: str) -> str | None:
    for pattern in PURCHASE_ID_PATTERNS:
        match = re.search(pattern, url)
        if not match:
            continue
        purchase_id = match.group(1).strip()
        if _is_valid_purchase_id(purchase_id):
            return purchase_id
    return None


def _extract_order_id_from_url(url: str) -> str | None:
    query = parse_qs(urlparse(url).query)
    values = query.get("order_id") or []
    if values and values[0].strip().isdigit():
        return values[0].strip()
    match = re.search(r"order_id=(\d+)", url)
    return match.group(1) if match else None


def _register_carrier_pickup(
    registry: dict[str, tuple[dict[str, Any] | None, dict[str, Any] | None]],
    *,
    option: dict[str, Any] | None,
    point: dict[str, Any] | None = None,
) -> None:
    if not isinstance(option, dict):
        return
    carrier = _carrier_display_name(
        code=str(option.get("carrier_code") or option.get("primary_carrier_code") or ""),
        title=_carrier_label(option),
    )
    if carrier is None:
        return
    current_point, current_option = registry.get(carrier, (None, None))
    merged_point = point or current_point
    if isinstance(option, dict) and not isinstance(merged_point, dict):
        if isinstance(option.get("shipping_point"), dict):
            merged_point = option["shipping_point"]
    if merged_point and not current_point:
        registry[carrier] = (merged_point, option)
    elif carrier not in registry:
        registry[carrier] = (merged_point, option)


def _collect_carrier_pickups(components: dict[str, Any]) -> dict[str, tuple[dict[str, Any] | None, dict[str, Any] | None]]:
    registry: dict[str, tuple[dict[str, Any] | None, dict[str, Any] | None]] = {}

    def scan_pickup_block(block: dict[str, Any]) -> None:
        point = block.get("shipping_point") if isinstance(block.get("shipping_point"), dict) else None
        option = _pick_shipping_option(block)
        if option is not None:
            _register_carrier_pickup(registry, option=option, point=point)
        for entry in block.get("shipping_options") or []:
            if isinstance(entry, dict):
                entry_point = entry.get("shipping_point")
                _register_carrier_pickup(
                    registry,
                    option=entry,
                    point=entry_point if isinstance(entry_point, dict) else None,
                )

    for component_key in ("shipping_pickup_options", "shipping_pickup_details"):
        component = components.get(component_key)
        if not isinstance(component, dict):
            continue
        for parent_key in ("pickup_options", "pickup_types"):
            parent = component.get(parent_key)
            if isinstance(parent, dict) and isinstance(parent.get("pickup"), dict):
                scan_pickup_block(parent["pickup"])
        if isinstance(component.get("pickup_details"), dict):
            scan_pickup_block(component["pickup_details"])

    return registry


def _format_carrier_pickup_line(
    carrier: str,
    *,
    point: dict[str, Any] | None,
    option: dict[str, Any] | None,
) -> str:
    """Nom du point relais (style Vinted Plug) — pas le label transporteur générique."""
    if isinstance(point, dict):
        name = str(point.get("name") or "").strip()
        if name and not _is_junk_pickup_label(name):
            return name
        formatted = _format_shipping_point(point)
        if formatted and not _is_junk_pickup_label(formatted):
            return formatted
    _ = carrier, option
    return ""


def parse_checkout_profile_data(
    checkout_payload: dict[str, Any],
    *,
    page: Page | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> tuple[str | None, list[str]]:
    """Extrait adresse + points relais (Mondial Relay, Chronopost, Vinted Go) depuis checkout."""
    components = checkout_payload.get("checkout", {}).get("components", {})
    if not isinstance(components, dict):
        return None, []

    address: str | None = None
    shipping_address = components.get("shipping_address")
    if isinstance(shipping_address, dict):
        addr_block = shipping_address.get("address")
        if isinstance(addr_block, dict):
            address = _format_address(addr_block)

    receiver_address: dict[str, Any] | None = None
    pickup_details = components.get("shipping_pickup_details")
    if isinstance(pickup_details, dict):
        receiver = pickup_details.get("receiver_address")
        if isinstance(receiver, dict):
            receiver_address = receiver

    registry = _collect_carrier_pickups(components)
    shipping_order_id = _shipping_order_id(components)

    if page is not None and shipping_order_id is not None:
        for carrier in TARGET_CARRIER_ORDER:
            point, option = registry.get(carrier, (None, None))
            if point is not None or option is None:
                continue
            rate = _rate_uuid(option)
            if not rate:
                continue
            fetched = _fetch_pickup_point_for_rate(
                page,
                base_url.rstrip("/"),
                shipping_order_id=shipping_order_id,
                rate_uuid=rate,
            )
            if fetched is not None:
                registry[carrier] = (fetched, option)

    points: list[str] = []
    has_shipping_data = bool(registry) or isinstance(pickup_details, dict)
    if has_shipping_data:
        for carrier in TARGET_CARRIER_ORDER:
            point, option = registry.get(carrier, (None, None))
            _append_unique(
                points,
                _format_carrier_pickup_line(carrier, point=point, option=option),
            )

    if points:
        return address, _filter_pickup_only(points)

    # Fallback : 1 point relais uniquement (jamais domicile)
    if isinstance(pickup_details, dict):
        details = pickup_details.get("pickup_details")
        if isinstance(details, dict):
            shipping_point = details.get("shipping_point")
            pickup_option = _pick_shipping_option(details)
            carrier = _carrier_display_name(
                code=str(
                    (pickup_option or {}).get("carrier_code")
                    or (pickup_option or {}).get("primary_carrier_code")
                    or ""
                ),
                title=_carrier_label(pickup_option) if isinstance(pickup_option, dict) else "",
            ) or "Point relais"
            if isinstance(shipping_point, dict):
                _append_unique(
                    points,
                    _format_carrier_pickup_line(
                        carrier, point=shipping_point, option=pickup_option
                    ),
                )

    _ = receiver_address  # adresse domicile volontairement ignorée pour l'affichage
    return address, _filter_pickup_only(points)


def profile_is_complete(profile: VintedProfileInfo) -> bool:
    """Profil utilisable : adresse + paiement renseignés."""
    return bool(profile.address and profile.payment_label)


def profile_missing_fields(profile: VintedProfileInfo) -> list[str]:
    missing: list[str] = []
    if not profile.address:
        missing.append("adresse")
    if not profile.payment_label:
        missing.append("moyen de paiement")
    return missing


def build_profile_incomplete_message(profile: VintedProfileInfo) -> str:
    reconnect = (
        "Reconnecte ta session Vinted avant de réessayer."
    )
    if profile.error:
        return f"❌ {profile.error}\n\n{reconnect}"

    missing = profile_missing_fields(profile)
    if not missing:
        return ""

    if set(missing) == {"adresse", "moyen de paiement"}:
        return f"❌ **Session Vinted expirée, invalide ou incomplète.**\n\n{reconnect}"

    joined = ", ".join(missing)
    return (
        f"❌ Infos Vinted incomplètes : **{joined}**.\n"
        "Mets à jour sur Vinted (adresse / paiement), puis :\n"
        f"{reconnect}"
    )


def _extract_user(page: Page, base_url: str, profile: VintedProfileInfo) -> None:
    user_block = _current_user_payload(page, base_url)
    if user_block is None:
        return
    login = user_block.get("login") or user_block.get("username")
    if isinstance(login, str) and login.strip():
        profile.username = login.strip()
    photo = user_block.get("photo") or {}
    if isinstance(photo, dict):
        avatar = photo.get("url") or photo.get("full_size_url")
        if isinstance(avatar, str):
            profile.avatar_url = avatar


def _extract_address(page: Page, base_url: str, profile: VintedProfileInfo) -> None:
    addresses = _fetch_json(page, f"{base_url}/api/v2/user_addresses")
    items = _first_dict_list(addresses, "user_addresses", "shipping_addresses", "addresses")
    entry = _pick_address_entry(items, profile.username)
    if entry is not None:
        profile.address = _format_address(entry)


def _extract_payment(page: Page, base_url: str, profile: VintedProfileInfo) -> None:
    page.goto(f"{base_url}/settings/payments", wait_until="domcontentloaded")
    page.wait_for_timeout(800)

    cards_payload = _fetch_json(page, f"{base_url}/api/v2/payments/credit_cards")
    cards = _first_dict_list(cards_payload, "cards", "credit_cards")
    card = _pick_card_entry(cards)
    if card is not None:
        profile.payment_label = _format_payment(card)
        return

    try:
        body = page.locator("body").inner_text(timeout=5000)
        match = re.search(
            r"(?i)([a-zà-ÿ\s.'-]{2,40})\s*(?:termin[eé]\s*par|fin.*?)\s*(\d{4})",
            body,
        )
        if match:
            profile.payment_label = f"{match.group(1).strip()} | **** {match.group(2)}"
    except Exception:
        pass


def _pick_cheap_catalog_item(
    catalog_payload: object,
    *,
    max_price: float = MAX_CHECKOUT_ITEM_PRICE_EUR,
) -> int | None:
    if not isinstance(catalog_payload, dict):
        return None
    items = catalog_payload.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("is_reserved") or item.get("is_closed"):
            continue
        price = item.get("price")
        amount = float(price.get("amount", 99)) if isinstance(price, dict) else 99.0
        if amount <= max_price:
            item_id = item.get("id")
            if isinstance(item_id, int):
                return item_id
            if isinstance(item_id, str) and item_id.isdigit():
                return int(item_id)
    return None


def _find_checkout_item_ids(page: Page, base_url: str) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for query in CATALOG_SEARCH_QUERIES:
        catalog = _fetch_json(
            page,
            f"{base_url}/api/v2/catalog/items?search_text={query}"
            f"&price_to={int(MAX_CHECKOUT_ITEM_PRICE_EUR)}&order=newest_first&per_page=20",
        )
        if not isinstance(catalog, dict):
            continue
        for item in catalog.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_id = _pick_cheap_catalog_item({"items": [item]})
            if item_id is not None and item_id not in seen:
                seen.add(item_id)
                ids.append(item_id)
    return ids


def _click_buy_button(page: Page) -> bool:
    for selector in BUY_SELECTORS:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=2000):
                btn.click(timeout=8000)
                return True
        except Exception:
            continue
    return False


def _page_is_closed(page: Page) -> bool:
    try:
        return bool(page.is_closed())
    except Exception:
        return True


def _is_captcha_url(url: str) -> bool:
    lower = url.lower()
    return any(marker in lower for marker in CAPTCHA_URL_MARKERS)


def _page_has_captcha(page: Page) -> bool:
    if _page_is_closed(page):
        return False
    try:
        if _is_captcha_url(page.url):
            return True
        for frame in page.frames:
            if _is_captcha_url(frame.url):
                return True
        text = page.locator("body").inner_text(timeout=2000).lower()
    except Exception:
        return False
    return any(marker in text for marker in CAPTCHA_TEXT_MARKERS)


def _wait_out_captcha(
    page: Page,
    *,
    deadline: float,
    wait: bool = True,
) -> bool:
    """Attend le captcha si `wait` (navigateur visible). En headless : échec immédiat."""
    if not _page_has_captcha(page):
        return True

    log.warning("vinted_captcha_detected", url=page.url, wait=wait)
    if not wait:
        return False

    while time.monotonic() < deadline:
        if _page_is_closed(page):
            return False
        if not _page_has_captcha(page):
            page.wait_for_timeout(800)
            if not _page_has_captcha(page):
                log.info("vinted_captcha_cleared")
                return True
        page.wait_for_timeout(1000)
    log.warning("vinted_captcha_timeout", url=getattr(page, "url", ""))
    return False


def _open_checkout_purchase_id(
    page: Page,
    base_url: str,
    item_id: int,
    *,
    captcha_deadline: float | None = None,
    wait_for_captcha: bool = True,
) -> tuple[str | None, dict[str, Any] | None]:
    captured: list[str] = []
    checkout_payloads: list[dict[str, Any]] = []
    deadline = captcha_deadline if captcha_deadline is not None else time.monotonic() + CAPTCHA_WAIT_S

    def on_response(response: Any) -> None:
        try:
            url = response.url
            if _is_captcha_url(url):
                return
            purchase_id = _extract_purchase_id_from_url(url)
            if purchase_id and purchase_id not in captured:
                captured.append(purchase_id)
            if not response.ok or "/checkout" not in url:
                return
            payload = response.json()
        except Exception:
            return
        if isinstance(payload, dict) and isinstance(payload.get("checkout"), dict):
            checkout_payloads.append(payload)
        from_payload = _purchase_id_from_payload(payload)
        if from_payload and from_payload not in captured:
            captured.append(from_payload)

    page.on("response", on_response)
    try:
        page.goto(f"{base_url}/items/{item_id}", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        _dismiss_cookies(page)

        if not _wait_out_captcha(page, deadline=deadline, wait=wait_for_captcha):
            return None, None

        if "login" in page.url.lower() or "signup" in page.url.lower():
            log.warning("vinted_checkout_login_redirect", item_id=item_id, url=page.url)
            return None, None
        if not _click_buy_button(page):
            log.warning("vinted_checkout_buy_button_missing", item_id=item_id, url=page.url)
            return None, None

        for _ in range(40):
            if _page_is_closed(page):
                log.warning("vinted_checkout_page_closed", item_id=item_id)
                break
            if _page_has_captcha(page):
                if not _wait_out_captcha(page, deadline=deadline, wait=wait_for_captcha):
                    return None, None
                continue
            if captured:
                payload = checkout_payloads[0] if checkout_payloads else None
                return captured[0], payload
            purchase_id = _extract_purchase_id_from_url(page.url)
            if purchase_id:
                payload = checkout_payloads[0] if checkout_payloads else None
                return purchase_id, payload
            if checkout_payloads:
                purchase_from_payload = _purchase_id_from_payload(checkout_payloads[0])
                if purchase_from_payload:
                    return purchase_from_payload, checkout_payloads[0]
            try:
                page.wait_for_timeout(500)
            except Exception as exc:
                log.warning("vinted_checkout_wait_failed", item_id=item_id, error=str(exc))
                break

        if checkout_payloads:
            purchase_from_payload = _purchase_id_from_payload(checkout_payloads[0])
            return purchase_from_payload, checkout_payloads[0]

        log.warning("vinted_checkout_no_purchase_id", item_id=item_id, url=getattr(page, "url", ""))
        return None, None
    except Exception as exc:
        log.warning("vinted_checkout_open_failed", item_id=item_id, error=str(exc))
        if checkout_payloads:
            return _purchase_id_from_payload(checkout_payloads[0]), checkout_payloads[0]
        return None, None
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass


def _fetch_checkout_payload(
    page: Page,
    base_url: str,
    *,
    purchase_id: str | None,
    page_url: str,
) -> dict[str, Any] | None:
    if purchase_id:
        checkout = _fetch_json(page, f"{base_url}/api/v2/purchases/{purchase_id}/checkout")
        if isinstance(checkout, dict):
            return checkout

    order_id = _extract_order_id_from_url(page_url)
    if order_id:
        checkout = _fetch_json(page, f"{base_url}/api/v2/transactions/{order_id}/checkout")
        if isinstance(checkout, dict):
            return checkout

    return None


def _parse_purchase_id(url: str) -> str | None:
    query = parse_qs(urlparse(url).query)
    values = query.get("purchase_id") or []
    if values and values[0].strip():
        return values[0].strip()
    return _extract_purchase_id_from_url(url)


def _extract_delivery_from_checkout(
    page: Page,
    base_url: str,
    profile: VintedProfileInfo,
    *,
    deadline: float | None = None,
    wait_for_captcha: bool = True,
) -> bool:
    """Lit adresse + points relais via checkout brouillon.

    Retourne False si captcha / timeout — n'empêche pas d'afficher adresse + carte.
    """
    if deadline is not None and time.monotonic() >= deadline:
        log.warning("vinted_checkout_skipped_timeout")
        return False

    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_timeout(500)
    captcha_deadline = time.monotonic() + (CAPTCHA_WAIT_S if wait_for_captcha else 0)
    if deadline is not None:
        captcha_deadline = min(captcha_deadline, deadline)

    if not _wait_out_captcha(page, deadline=captcha_deadline, wait=wait_for_captcha):
        return False

    item_ids = _find_checkout_item_ids(page, base_url)
    if not item_ids:
        log.warning("vinted_checkout_no_catalog_items")
        return not _page_has_captcha(page)

    purchase_id: str | None = None
    checkout: dict[str, Any] | None = None
    checkout_page_url = page.url
    saw_captcha_block = False
    for item_id in item_ids[:MAX_CHECKOUT_ITEM_ATTEMPTS]:
        if deadline is not None and time.monotonic() >= deadline:
            log.warning("vinted_checkout_skipped_timeout")
            break
        if _page_is_closed(page):
            break
        purchase_id, intercepted = _open_checkout_purchase_id(
            page,
            base_url,
            item_id,
            captcha_deadline=captcha_deadline,
            wait_for_captcha=wait_for_captcha,
        )
        checkout_page_url = page.url if not _page_is_closed(page) else checkout_page_url
        if intercepted is not None:
            checkout = intercepted
            break
        if purchase_id:
            break
        if _page_has_captcha(page):
            saw_captcha_block = True
            # Headless : inutile de retenter d'autres annonces derrière le même captcha.
            if not wait_for_captcha:
                break

    if checkout is None and not _page_is_closed(page):
        checkout = _fetch_checkout_payload(
            page,
            base_url,
            purchase_id=purchase_id,
            page_url=checkout_page_url,
        )
    if not isinstance(checkout, dict):
        log.warning(
            "vinted_checkout_api_failed",
            purchase_id=purchase_id,
            page_url=checkout_page_url,
            captcha=saw_captcha_block or _page_has_captcha(page),
        )
        return False

    address, points = parse_checkout_profile_data(checkout, page=page, base_url=base_url)
    if address:
        profile.address = address
    pickup_only = _filter_pickup_only(points)
    if pickup_only:
        profile.delivery_points = pickup_only
    else:
        log.warning("vinted_checkout_no_delivery_points", purchase_id=purchase_id)
    return True


def fetch_vinted_profile(
    storage_state: dict[str, Any],
    *,
    base_url: str = DEFAULT_BASE_URL,
    headless: bool = True,
) -> tuple[VintedProfileInfo, dict[str, Any] | None]:
    """Lit adresse + paiement du compte Vinted.

    Pas de checkout / Chrome captcha pour les points relais : Vinted les
    applique automatiquement à l'achat réel.
    """
    base = base_url.rstrip("/")
    profile = VintedProfileInfo()
    refreshed_state: dict[str, Any] | None = None

    with sync_playwright() as playwright:
        browser = launch_vinted_browser(playwright, headless=headless)
        context = browser.new_context(
            storage_state=storage_state,
            locale="fr-FR",
            viewport={"width": 1280, "height": 900},
        )
        apply_vinted_stealth(context)
        page = context.new_page()
        page.set_default_timeout(15_000)
        try:
            if not _ensure_vinted_session(page, base):
                profile.error = (
                    "Session Vinted invalide ou incomplète. "
                    "Refais **Obtenir le token** puis colle le **code** dans Discord."
                )
                return profile, None

            # Pas de bascule Chrome visible : lecture API uniquement.
            if _page_has_captcha(page):
                log.warning("vinted_profile_captcha_skipped", url=page.url)
                profile.error = (
                    "Session Vinted bloquée (anti-robot). "
                    "Refais **Obtenir le token**, puis **Se connecter** avec le code."
                )
                return profile, None

            _extract_user(page, base, profile)
            _extract_address(page, base, profile)
            _extract_payment(page, base, profile)

            if (
                profile.error is None
                and profile.username == "Compte Vinted"
                and not profile.address
                and not profile.payment_label
            ):
                profile.error = (
                    "Session Vinted invalide. "
                    "Refais **Obtenir le token** et colle le **code** (pas un vieux token)."
                )

            try:
                refreshed_state = context.storage_state()
            except Exception:
                refreshed_state = None

            log.info(
                "vinted_profile_fetched",
                username=profile.username,
                has_address=bool(profile.address),
                delivery_count=0,
                has_payment=bool(profile.payment_label),
                error=profile.error,
                cookies_refreshed=bool(refreshed_state),
                checkout_draft=False,
                headless=headless,
            )
        except Exception as exc:
            log.exception("vinted_profile_fetch_failed", error=str(exc))
            if profile.error is None and not (profile.address or profile.payment_label):
                profile.error = f"Impossible de lire ton profil Vinted : {exc}"
        finally:
            browser.close()

    return profile, refreshed_state
