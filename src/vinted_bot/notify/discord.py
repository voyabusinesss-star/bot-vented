"""Publication Discord via API REST (bot token)."""

from __future__ import annotations

import time
import unicodedata
from datetime import datetime, timezone
from typing import Any

import httpx

from vinted_bot.config import (
    CLASSIQUE_BRANDS,
    LUXE_BRANDS,
    Settings,
    get_settings,
    sanitize_discord_channel_id,
)
from vinted_bot.db.models import Listing
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

DISCORD_API = "https://discord.com/api/v10"
EMBED_COLOR = 0x57F287  # barre verte (le texte d’embed ne peut pas être coloré)
DEAL_ATTR = "_deal_evaluation"

# Approximation frais protection acheteur (affichage TTC indicatif)
_TTC_FIXED_CENTS = 70
_TTC_RATE = 0.05


def attach_deal_evaluation(listing: Listing, deal: Any) -> Listing:
    """Attache une DealEvaluation au listing (attribut transient, non persisté)."""
    setattr(listing, DEAL_ATTR, deal)
    return listing


def get_deal_evaluation(listing: Listing) -> Any | None:
    return getattr(listing, DEAL_ATTR, None)


def normalize_brand(brand: str | None) -> str:
    if not brand:
        return ""
    text = unicodedata.normalize("NFKD", brand)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    # tirets / underscores → espaces (ex. stone-island, ralph_lauren)
    text = text.replace("-", " ").replace("_", " ")
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    text = " ".join(text.split())
    # alias fréquents
    aliases = {
        "levi s": "levis",
        "levis": "levis",
        "ami": "ami paris",
        "tnf": "the north face",
        "north face": "the north face",
        "lv": "louis vuitton",
        "ysl": "saint laurent",
        "st laurent": "saint laurent",
        "yves saint laurent": "saint laurent",
        "polo ralph lauren": "ralph lauren",
        "ralph lauren polo": "ralph lauren",
        "carhartt wip": "carhartt",
        "under armor": "under armour",
        "acne studio": "acne studios",
        "cdg": "comme des garcons",
        "comme des garcons play": "comme des garcons",
        "stussy": "stussy",
        "celine": "celine",
        "toteme": "toteme",
        "dr. martens": "dr martens",
        "doc martens": "dr martens",
        "docs": "dr martens",
        "on running": "on cloud",
        "on-running": "on cloud",
        "hoka one one": "hoka",
        "newbalance": "new balance",
        "air jordan": "jordan",
        "jordan brand": "jordan",
    }
    return aliases.get(text, text)


# Catégories vêtement acceptées dans les salons indémodables / classiques.
VETEMENT_CATEGORIES: frozenset[str] = frozenset(
    {
        "polo",
        "hoodie",
        "sweat",
        "pull",
        "tshirt",
        "chemise",
        "veste",
        "pantalon",
        "short",
    }
)


def is_classique_brand(brand: str | None) -> bool:
    """Marque des salons indémodables / les-classiques (hors luxe, hors sneakers pures)."""
    key = normalize_brand(brand)
    if not key:
        return False
    if key in CLASSIQUE_BRANDS:
        return True
    return any(key.startswith(f"{classic} ") for classic in CLASSIQUE_BRANDS)


def route_channel(
    brand: str | None,
    channel_map: dict[str, str],
    *,
    sneaker_map: dict[str, str] | None = None,
    is_shoe: bool = False,
) -> str | None:
    """Retourne le channel marque.

    Chaussures → **uniquement** salon sneakers (jamais fallback vêtements).
    """
    normalized = normalize_brand(brand)
    if not normalized:
        return None

    def _match(mapping: dict[str, str]) -> str | None:
        if normalized in mapping:
            return mapping[normalized]
        for key, channel_id in mapping.items():
            if normalized == key or normalized.startswith(f"{key} "):
                return channel_id
        return None

    if is_shoe:
        if sneaker_map:
            return _match(sneaker_map)
        return None

    return _match(channel_map)


def is_allowed_brand(
    brand: str | None,
    channel_map: dict[str, str],
    *,
    sneaker_map: dict[str, str] | None = None,
) -> bool:
    if route_channel(brand, channel_map) is not None:
        return True
    if sneaker_map and route_channel(brand, sneaker_map) is not None:
        return True
    return False


def belongs_in_all_vetement(
    brand: str | None,
    *,
    is_shoe: bool,
    brand_channel_id: str | None = None,
    sneaker_channel_ids: set[str] | None = None,
    is_vetement: bool = True,
) -> bool:
    """#all-vetement = indémodables vêtements seulement.

    Exclus : chaussures, objets non-vêtements, salons Pépites Sneakers, luxe.
    """
    if is_shoe or not is_vetement:
        return False
    if brand_channel_id and sneaker_channel_ids and brand_channel_id in sneaker_channel_ids:
        return False
    return is_classique_brand(brand) and normalize_brand(brand) not in LUXE_BRANDS


def _raw(listing: Listing) -> dict[str, Any]:
    return listing.raw_json if isinstance(listing.raw_json, dict) else {}


def _seller_info(listing: Listing) -> tuple[str | None, str | None]:
    raw = _raw(listing)
    user = raw.get("user") or raw.get("seller") or {}
    if not isinstance(user, dict):
        return None, None
    login = user.get("login") or user.get("username")
    avatar = None
    photo = user.get("photo") or {}
    if isinstance(photo, dict):
        avatar = photo.get("url") or photo.get("full_size_url")
    return (str(login) if login else None), (str(avatar) if avatar else None)


def _condition_label(listing: Listing) -> str:
    if listing.condition:
        return listing.condition
    raw = _raw(listing)
    for key in ("status", "status_title", "condition", "status_id"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            title = value.get("title") or value.get("name")
            if title:
                return str(title)
    # status_id fréquents Vinted FR
    status_id = raw.get("status_id")
    mapping = {
        1: "Neuf avec étiquette",
        2: "Neuf sans étiquette",
        3: "Très bon état",
        4: "Bon état",
        5: "Satisfaisant",
        6: "État correct",
    }
    if isinstance(status_id, int) and status_id in mapping:
        return mapping[status_id]
    return "—"


def _fmt_money(amount: float, currency: str = "EUR") -> str:
    cur = (currency or "EUR").upper()
    if cur == "EUR":
        return f"{amount:.2f} €".replace(".", ",")
    return f"{amount:.2f} {cur}"


def _price_parts(listing: Listing) -> tuple[str, str | None]:
    """Retourne (prix article, prix TTC ou None)."""
    raw = _raw(listing)
    currency = listing.currency or "EUR"
    price_cents = listing.price_cents

    total = raw.get("total_item_price")
    ttc_amount: float | None = None
    ttc_currency = currency
    if isinstance(total, dict) and total.get("amount") is not None:
        try:
            ttc_amount = float(total["amount"])
            ttc_currency = str(total.get("currency_code") or currency)
        except (TypeError, ValueError):
            ttc_amount = None

    if price_cents is not None:
        base = _fmt_money(price_cents / 100.0, currency)
    elif ttc_amount is not None:
        base = _fmt_money(ttc_amount, ttc_currency)
    else:
        return "—", None

    if ttc_amount is None and price_cents is not None:
        ttc_amount = (
            int(round(price_cents * (1 + _TTC_RATE) + _TTC_FIXED_CENTS)) / 100.0
        )
        ttc_currency = currency

    ttc = _fmt_money(ttc_amount, ttc_currency) if ttc_amount is not None else None
    return base, ttc


def _price_description(listing: Listing) -> str:
    """Prix en gros (police embed normale) + ligne TTC."""
    base, ttc = _price_parts(listing)
    # Discord ne permet pas de colorer ce texte (le vert ANSI = police code trop petite).
    if ttc and ttc != base:
        return f"# {base}\n≈ {ttc} TTC"
    return f"# {base}"


def _deal_avis_label(deal: Any | None) -> str:
    """Avis deal : étoiles + mot (Pépites, Claqué, …)."""
    if deal is None or not getattr(deal, "should_post", False):
        return "☆☆☆☆☆ · —"
    score = int(getattr(deal, "score", 0) or 0)
    if score >= 92:
        stars, word = 5, "Pépites"
    elif score >= 82:
        stars, word = 5, "Claqué"
    elif score >= 75:
        stars, word = 4, "Très bon"
    elif score >= 68:
        stars, word = 4, "Solide"
    elif score >= 60:
        stars, word = 3, "Correct"
    else:
        stars, word = 2, "Moyen"
    return f"{'⭐' * stars}{'☆' * (5 - stars)} · **{word}**"


def _published_label(listing: Listing) -> str:
    raw = _raw(listing)
    created: Any = raw.get("created_at_ts") or raw.get("created_at")
    photo = raw.get("photo")
    if created is None and isinstance(photo, dict):
        created = photo.get("high_resolution")
        if isinstance(created, dict):
            created = created.get("timestamp")
        elif isinstance(created, list) and created:
            first = created[0]
            if isinstance(first, dict):
                created = first.get("timestamp")

    dt: datetime | None = None
    if listing.published_at is not None:
        dt = listing.published_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    elif isinstance(created, (int, float)):
        dt = datetime.fromtimestamp(float(created), tz=timezone.utc)
    elif isinstance(created, str):
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            dt = None

    if dt is None:
        return "—"
    now = datetime.now(timezone.utc)
    seconds = max(0, int((now - dt.astimezone(timezone.utc)).total_seconds()))
    if seconds < 60:
        return "à l'instant"
    if seconds < 3600:
        mins = seconds // 60
        return f"il y a {mins} minute" if mins == 1 else f"il y a {mins} minutes"
    if seconds < 86400:
        hours = seconds // 3600
        return f"il y a {hours} heure" if hours == 1 else f"il y a {hours} heures"
    days = seconds // 86400
    return f"il y a {days} jour" if days == 1 else f"il y a {days} jours"


def _rating_label(listing: Listing) -> str:
    raw = _raw(listing)
    user = raw.get("user") or {}
    if not isinstance(user, dict):
        return "Aucun avis"
    feedback = (
        user.get("feedback_reputation")
        or user.get("feedback_rating")
        or user.get("rating")
    )
    count = user.get("feedback_count") or 0
    try:
        count_i = int(count)
    except (TypeError, ValueError):
        count_i = 0
    if count_i <= 0 and feedback is None:
        return "Aucun avis"
    try:
        score = float(feedback) if feedback is not None else 0.0
    except (TypeError, ValueError):
        score = 0.0
    # feedback_reputation Vinted est souvent 0..1
    if score > 1.0:
        stars = max(0, min(5, round(score)))
    else:
        stars = max(0, min(5, round(score * 5)))
    return f"{'⭐' * stars}{'☆' * (5 - stars)} ({count_i})"


def _price_label(listing: Listing) -> str:
    raw = _raw(listing)
    currency = listing.currency or "EUR"
    price_cents = listing.price_cents

    total = raw.get("total_item_price")
    if isinstance(total, dict) and total.get("amount") is not None:
        try:
            ttc_amount = float(total["amount"])
        except (TypeError, ValueError):
            ttc_amount = None
        cur = str(total.get("currency_code") or currency)
        if price_cents is not None:
            base = _fmt_money(price_cents / 100.0, currency)
        elif ttc_amount is not None:
            base = _fmt_money(ttc_amount, cur)
        else:
            base = "—"
        if ttc_amount is not None:
            return f"**{base}** · ≈ {_fmt_money(ttc_amount, cur)} TTC"
        return f"**{base}**"

    if price_cents is None:
        return "—"
    base = _fmt_money(price_cents / 100.0, currency)
    ttc_cents = int(round(price_cents * (1 + _TTC_RATE) + _TTC_FIXED_CENTS))
    return f"**{base}** · ≈ {_fmt_money(ttc_cents / 100.0, currency)} TTC"


def _brand_label(listing: Listing) -> str:
    brand = (listing.brand or "").strip()
    if not brand:
        return "—"
    return brand.title() if brand.islower() else brand


def _size_label(listing: Listing) -> str:
    size = (listing.size or "").strip()
    return size.upper() if size else "—"


def _photo_urls(listing: Listing) -> list[str]:
    urls = [p.url for p in (listing.photos or []) if p.url]
    if urls:
        return urls
    raw = _raw(listing)
    collected: list[str] = []
    photo = raw.get("photo") or {}
    if isinstance(photo, dict) and photo.get("url"):
        collected.append(str(photo["url"]))
    for p in raw.get("photos") or []:
        if isinstance(p, dict) and p.get("url"):
            collected.append(str(p["url"]))
    seen: set[str] = set()
    unique: list[str] = []
    for url in collected:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def _build_listing_components(listing: Listing) -> list[dict[str, Any]]:
    """Boutons sous l'embed : liens Vinted (détails / acheter / négocier)."""
    url = listing.url
    return [
        {
            "type": 1,
            "components": [
                {"type": 2, "style": 5, "label": "📄 Détails", "url": url},
                {"type": 2, "style": 5, "label": "💳 Acheter", "url": url},
                {"type": 2, "style": 5, "label": "🤝 Négocier", "url": url},
            ],
        }
    ]


def build_listing_preview_payload(listing: Listing) -> dict[str, Any]:
    """Même annonce riche que les salons publics (détails / acheter / négocier)."""
    return build_listing_payload(listing)


_last_bot_preview_post_at: float = 0.0
_recent_preview_brands: list[str] = []
_recent_preview_vinted_ids: list[int] = []
_RECENT_PREVIEW_BRANDS_MAX = 8
_RECENT_PREVIEW_IDS_MAX = 40


def _preview_brand_key(listing: Listing) -> str:
    return normalize_brand(listing.brand) or "unknown"


def _preview_is_shoe(listing: Listing) -> bool:
    from vinted_bot.services.deal_filter import is_shoe_listing

    deal = get_deal_evaluation(listing)
    category = getattr(deal, "category", None) if deal is not None else None
    if category in ("chaussure", "dunk", "air_force_1"):
        return True
    return is_shoe_listing(listing.title or "")


def pick_diverse_preview_listing(candidates: list[Listing]) -> Listing | None:
    """Évite de spammer la même marque / que des chaussures dans l'aperçu."""
    if not candidates:
        return None

    fresh = [
        c
        for c in candidates
        if int(getattr(c, "vinted_id", 0) or 0) not in set(_recent_preview_vinted_ids)
    ]
    pool = fresh or list(candidates)

    recent_tags = set(_recent_preview_brands)
    recent_brand_names = {
        tag.split(":", 1)[-1] for tag in _recent_preview_brands if ":" in tag
    }
    recent_shoes = sum(1 for b in _recent_preview_brands[-4:] if b.startswith("shoe:"))

    def _score(item: Listing) -> tuple[int, int, int]:
        deal = get_deal_evaluation(item)
        deal_score = int(getattr(deal, "score", 0) or 0)
        brand = _preview_brand_key(item)
        is_shoe = _preview_is_shoe(item)
        brand_tag = f"{'shoe' if is_shoe else 'cloth'}:{brand}"
        # Pénalise marque déjà vue (chaussure ou textile)
        if brand_tag in recent_tags or brand in recent_brand_names:
            diversity = 0
        else:
            diversity = 50
        # Après plusieurs sneakers, pousse le textile
        if is_shoe and recent_shoes >= 2:
            shoe_bias = -40
        elif not is_shoe:
            shoe_bias = 20
        else:
            shoe_bias = 0
        return (
            diversity + shoe_bias,
            deal_score,
            int(getattr(item, "vinted_id", 0) or 0),
        )

    return max(pool, key=_score)


def maybe_post_bot_preview(
    notifier: "DiscordNotifier",
    listing: Listing,
    *,
    settings: Settings | None = None,
) -> bool:
    """Poste au plus 1 aperçu toutes les N secondes dans le salon dédié."""
    return maybe_post_bot_preview_from_candidates(
        notifier, [listing], settings=settings
    )


def maybe_post_bot_preview_from_candidates(
    notifier: "DiscordNotifier",
    candidates: list[Listing],
    *,
    settings: Settings | None = None,
) -> bool:
    """Choisit une annonce diversifiée puis poste (avec boutons)."""
    global _last_bot_preview_post_at, _recent_preview_brands, _recent_preview_vinted_ids

    cfg = settings or notifier.settings
    channel_id = sanitize_discord_channel_id(
        getattr(cfg, "discord_channel_bot_preview", "") or ""
    )
    if not channel_id:
        log.info("discord_bot_preview_skipped", reason="channel_not_configured")
        return False

    interval = float(getattr(cfg, "bot_preview_interval_seconds", 150.0) or 150.0)
    now = time.monotonic()
    if _last_bot_preview_post_at and (now - _last_bot_preview_post_at) < interval:
        log.info(
            "discord_bot_preview_skipped",
            reason="interval",
            wait_seconds=round(interval - (now - _last_bot_preview_post_at), 1),
            candidates=len(candidates),
        )
        return False

    listing = pick_diverse_preview_listing(candidates)
    if listing is None:
        log.info("discord_bot_preview_skipped", reason="no_candidate")
        return False

    try:
        notifier.post_message(channel_id, build_listing_preview_payload(listing))
        _last_bot_preview_post_at = now
        brand = _preview_brand_key(listing)
        tag = f"{'shoe' if _preview_is_shoe(listing) else 'cloth'}:{brand}"
        _recent_preview_brands = (_recent_preview_brands + [tag])[
            -_RECENT_PREVIEW_BRANDS_MAX:
        ]
        vid = int(getattr(listing, "vinted_id", 0) or 0)
        if vid:
            _recent_preview_vinted_ids = (_recent_preview_vinted_ids + [vid])[
                -_RECENT_PREVIEW_IDS_MAX:
            ]
        log.info(
            "discord_bot_preview_posted",
            vinted_id=listing.vinted_id,
            channel_id=channel_id,
            brand=listing.brand,
            kind=tag,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "discord_bot_preview_failed",
            vinted_id=listing.vinted_id,
            error=str(exc)[:200],
        )
        return False


def _listing_fields(listing: Listing, deal: Any | None = None) -> list[dict[str, Any]]:
    """Grille : infos à gauche, avis deal à droite."""
    return [
        {"name": "⌛ Publié", "value": _published_label(listing), "inline": True},
        {"name": "🔖 Marque", "value": _brand_label(listing), "inline": True},
        {"name": "🌟 Avis", "value": _deal_avis_label(deal), "inline": True},
        {"name": "📏 Taille", "value": _size_label(listing), "inline": True},
        {"name": "💎 État", "value": _condition_label(listing), "inline": True},
        {"name": "👤 Vendeur", "value": _rating_label(listing), "inline": True},
    ]


def build_listing_payload(listing: Listing) -> dict[str, Any]:
    """Embed riche : prix gros à gauche, avis étoiles, multi-photos, boutons."""
    seller_name, seller_avatar = _seller_info(listing)
    photos = _photo_urls(listing)
    deal = get_deal_evaluation(listing)
    title = (listing.title or "Annonce Vinted")[:256]
    color = EMBED_COLOR

    main: dict[str, Any] = {
        "title": title,
        "url": listing.url,
        "description": _price_description(listing),
        "color": color,
        "fields": _listing_fields(listing, deal),
        "footer": {"text": "Vinted Bot"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if seller_name:
        author: dict[str, Any] = {"name": seller_name}
        if seller_avatar:
            author["icon_url"] = seller_avatar
        main["author"] = author
    if photos:
        main["image"] = {"url": photos[0]}

    embeds: list[dict[str, Any]] = [main]
    # Même url → collage Discord (grande + petites à droite)
    for photo_url in photos[1:4]:
        embeds.append(
            {
                "url": listing.url,
                "color": color,
                "image": {"url": photo_url},
            }
        )

    components = _build_listing_components(listing)
    return {"embeds": embeds, "components": components}


def build_listing_embed(listing: Listing) -> dict[str, Any]:
    """Rétrocompat : premier embed uniquement."""
    return build_listing_payload(listing)["embeds"][0]


def build_summary_embed(
    *,
    query: str,
    items_found: int,
    items_upserted: int,
    items_posted: int,
    scrape_run_id: int,
) -> dict[str, Any]:
    return {
        "title": "Scrape terminé",
        "color": 0x57F287,
        "fields": [
            {"name": "Query", "value": query or "—", "inline": True},
            {"name": "Trouvées", "value": str(items_found), "inline": True},
            {"name": "Upsertées", "value": str(items_upserted), "inline": True},
            {"name": "Postées Discord", "value": str(items_posted), "inline": True},
            {"name": "Run ID", "value": str(scrape_run_id), "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class DiscordNotifier:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.channel_map = self.settings.brand_channel_map()
        self.sneaker_map = self.settings.sneaker_channel_map()
        self._client: httpx.Client | None = None

    def __enter__(self) -> DiscordNotifier:
        self._client = httpx.Client(
            base_url=DISCORD_API,
            headers={
                "Authorization": f"Bot {self.settings.discord_bot_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        return self

    def __exit__(self, *args: object) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            raise RuntimeError("DiscordNotifier non démarré — utiliser with")
        return self._client

    def post_message(self, channel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(
            f"/channels/{channel_id}/messages",
            json=payload,
        )
        if response.status_code == 429:
            retry_after = float(response.json().get("retry_after", 2))
            log.warning("discord_rate_limited", retry_after=retry_after)
            time.sleep(retry_after)
            response = self.client.post(
                f"/channels/{channel_id}/messages",
                json=payload,
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Discord API {response.status_code}: {response.text[:400]}"
            )
        data = response.json()
        return data if isinstance(data, dict) else {}

    def edit_message(
        self,
        channel_id: str,
        message_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = self.client.patch(
            f"/channels/{channel_id}/messages/{message_id}",
            json=payload,
        )
        if response.status_code == 429:
            retry_after = float(response.json().get("retry_after", 2))
            log.warning("discord_rate_limited", retry_after=retry_after)
            time.sleep(retry_after)
            response = self.client.patch(
                f"/channels/{channel_id}/messages/{message_id}",
                json=payload,
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Discord edit {response.status_code}: {response.text[:400]}"
            )
        data = response.json()
        return data if isinstance(data, dict) else {}

    def delete_message(self, channel_id: str, message_id: str) -> None:
        if not message_id:
            return
        response = self.client.delete(
            f"/channels/{channel_id}/messages/{message_id}",
        )
        if response.status_code == 429:
            retry_after = float(response.json().get("retry_after", 2))
            log.warning("discord_rate_limited", retry_after=retry_after)
            time.sleep(retry_after)
            response = self.client.delete(
                f"/channels/{channel_id}/messages/{message_id}",
            )
        if response.status_code >= 400 and response.status_code != 404:
            raise RuntimeError(
                f"Discord delete {response.status_code}: {response.text[:400]}"
            )

    def post_embed(self, channel_id: str, embed: dict[str, Any]) -> None:
        self.post_message(channel_id, {"embeds": [embed]})

    def open_dm_channel(self, discord_user_id: int) -> str:
        """Crée / récupère le salon DM avec l'utilisateur. Retourne channel_id."""
        response = self.client.post(
            "/users/@me/channels",
            json={"recipient_id": str(int(discord_user_id))},
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Open DM {response.status_code}: {response.text[:400]}"
            )
        data = response.json()
        channel_id = str(data.get("id") or "")
        if not channel_id:
            raise RuntimeError("Open DM: channel id manquant")
        return channel_id

    def send_dm_payload(
        self,
        discord_user_id: int,
        payload: dict[str, Any],
    ) -> tuple[str, str]:
        """Envoie un message DM. Retourne (channel_id, message_id)."""
        channel_id = self.open_dm_channel(discord_user_id)
        data = self.post_message(channel_id, payload)
        message_id = str(data.get("id") or "")
        if not message_id:
            raise RuntimeError("DM: message id manquant")
        return channel_id, message_id

    def send_dm_embed(
        self,
        discord_user_id: int,
        embed: dict[str, Any],
        *,
        content: str | None = None,
    ) -> None:
        """Envoie un embed en message privé (jamais dans un salon public)."""
        payload: dict[str, Any] = {"embeds": [embed]}
        if content:
            payload["content"] = content[:2000]
        self.send_dm_payload(discord_user_id, payload)

    def post_listing(self, listing: Listing) -> str | None:
        """Poste canal marque (obligatoire) + #all (best-effort).

        Si le post marque réussit, on considère l'annonce postée même si #all échoue
        (évite les doublons marque au prochain run).
        """
        from vinted_bot.services.deal_filter import is_clothing_not_shoe, is_shoe_listing

        deal = get_deal_evaluation(listing)
        category = getattr(deal, "category", None) if deal is not None else None
        is_shoe = False
        if category in (
            "chaussure",
            "dunk",
            "air_force_1",
        ):
            is_shoe = True
        elif is_shoe_listing(listing.title):
            is_shoe = True

        brand_channel_id = route_channel(
            listing.brand,
            self.channel_map,
            sneaker_map=self.sneaker_map,
            is_shoe=is_shoe,
        )
        if not brand_channel_id:
            log.info(
                "discord_skipped_brand",
                brand=listing.brand,
                vinted_id=listing.vinted_id,
                is_shoe=is_shoe,
            )
            return None

        sneaker_ids = set(self.sneaker_map.values())
        # Salons indémodables / classiques : vêtements uniquement (pas chaussures / objets).
        if (
            brand_channel_id not in sneaker_ids
            and is_classique_brand(listing.brand)
        ):
            if is_shoe:
                log.info(
                    "discord_skipped_shoe_on_classique",
                    brand=listing.brand,
                    vinted_id=listing.vinted_id,
                    channel_id=brand_channel_id,
                )
                return None
            is_vetement = (
                category in VETEMENT_CATEGORIES
                if category
                else is_clothing_not_shoe(listing.title)
            )
            if category == "default":
                is_vetement = is_clothing_not_shoe(listing.title)
            if not is_vetement:
                log.info(
                    "discord_skipped_non_clothing_on_classique",
                    brand=listing.brand,
                    vinted_id=listing.vinted_id,
                    category=category,
                    channel_id=brand_channel_id,
                )
                return None

        payload = build_listing_payload(listing)
        self.post_message(brand_channel_id, payload)

        all_channel = sanitize_discord_channel_id(self.settings.discord_channel_all)
        # #all-vetement = indémodables vêtements uniquement.
        # Si on vient de poster sur un salon classique (pas sneakers),
        # on mirror TOUJOURS — pas de 2e jugement catégorie (sinon écarts salon≠ALL).
        mirror_all = (
            bool(all_channel)
            and all_channel != brand_channel_id
            and brand_channel_id not in sneaker_ids
            and not is_shoe
            and is_classique_brand(listing.brand)
        )
        if mirror_all:
            try:
                self.post_message(all_channel, payload)
            except Exception as exc:
                log.warning(
                    "discord_all_channel_failed",
                    vinted_id=listing.vinted_id,
                    error=str(exc),
                )
        elif all_channel and not mirror_all:
            log.info(
                "discord_all_vetement_skipped",
                brand=listing.brand,
                vinted_id=listing.vinted_id,
                is_shoe=is_shoe,
                brand_channel_id=brand_channel_id,
                reason="not_classique_clothing_or_sneaker_or_luxe",
            )

        return brand_channel_id

    def post_summary(
        self,
        *,
        query: str,
        items_found: int,
        items_upserted: int,
        items_posted: int,
        scrape_run_id: int,
    ) -> None:
        logs_id = self.settings.discord_channel_logs.strip()
        if not logs_id:
            return
        self.post_embed(
            logs_id,
            build_summary_embed(
                query=query,
                items_found=items_found,
                items_upserted=items_upserted,
                items_posted=items_posted,
                scrape_run_id=scrape_run_id,
            ),
        )

    def post_test_message(self) -> None:
        channel_id = self.settings.discord_channel_all.strip()
        if not channel_id:
            raise RuntimeError("DISCORD_CHANNEL_ALL manquant dans .env")
        embed = {
            "title": "Test Vinted Bot",
            "description": (
                "Connexion Discord OK — aperçu riche activé "
                "(vendeur, champs, grande image, boutons)."
            ),
            "color": 0x5865F2,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.post_embed(channel_id, embed)


def publish_listings_to_discord(
    listings: list[Listing],
    *,
    query: str,
    items_found: int,
    items_upserted: int,
    scrape_run_id: int,
    settings: Settings | None = None,
    bot_preview: bool = False,
) -> list[int]:
    """Poste les annonces. Retourne les listing.id postés avec succès.

    bot_preview=True : tente aussi 1 ping ralenti dans le salon aperçu
    (scrapes publics uniquement — jamais les filtres privés).
    """
    cfg = settings or get_settings()
    if not cfg.discord_ready() and not (
        bot_preview
        and sanitize_discord_channel_id(
            getattr(cfg, "discord_channel_bot_preview", "") or ""
        )
    ):
        if not cfg.discord_ready():
            log.info("discord_skipped", reason="not_configured")
            return []

    posted_ids: list[int] = []
    with DiscordNotifier(cfg) as notifier:
        total = len(listings)
        for index, listing in enumerate(listings):
            if not cfg.discord_ready():
                break
            try:
                channel_id = notifier.post_listing(listing)
                if channel_id:
                    posted_ids.append(listing.id)
                    log.info(
                        "discord_posted",
                        vinted_id=listing.vinted_id,
                        channel_id=channel_id,
                        brand=listing.brand,
                    )
            except Exception as exc:
                log.exception(
                    "discord_post_failed",
                    vinted_id=listing.vinted_id,
                    error=str(exc),
                )
            # Pause uniquement entre annonces (pas après la dernière)
            if index < total - 1 and cfg.discord_post_delay_seconds > 0:
                time.sleep(cfg.discord_post_delay_seconds)

        # Aperçu : basé sur les annonces du scrape public (pas besoin du post marque)
        if bot_preview and listings:
            maybe_post_bot_preview_from_candidates(
                notifier, list(listings), settings=cfg
            )

        if cfg.discord_ready():
            try:
                notifier.post_summary(
                    query=query,
                    items_found=items_found,
                    items_upserted=items_upserted,
                    items_posted=len(posted_ids),
                    scrape_run_id=scrape_run_id,
                )
            except Exception as exc:
                log.exception("discord_summary_failed", error=str(exc))

    return posted_ids
