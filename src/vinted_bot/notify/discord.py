"""Publication Discord via API REST (bot token)."""

from __future__ import annotations

import time
import unicodedata
from datetime import datetime, timezone
from typing import Any

import httpx

from vinted_bot.config import Settings, get_settings
from vinted_bot.db.models import Listing
from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

DISCORD_API = "https://discord.com/api/v10"
EMBED_COLOR = 0x2B2D31  # proche du style sombre type "Vinted Plug"

# Approximation frais protection acheteur (affichage TTC indicatif)
_TTC_FIXED_CENTS = 70
_TTC_RATE = 0.05


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
        "acne studio": "acne studios",
        "cdg": "comme des garcons",
        "comme des garcons play": "comme des garcons",
    }
    return aliases.get(text, text)


def route_channel(brand: str | None, channel_map: dict[str, str]) -> str | None:
    """Retourne le channel marque si suivi, sinon None (ignoré)."""
    normalized = normalize_brand(brand)
    if not normalized:
        return None
    if normalized in channel_map:
        return channel_map[normalized]
    for key, channel_id in channel_map.items():
        if normalized == key or normalized.startswith(f"{key} "):
            return channel_id
    return None


def is_allowed_brand(brand: str | None, channel_map: dict[str, str]) -> bool:
    return route_channel(brand, channel_map) is not None


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
    seconds = int((now - dt.astimezone(timezone.utc)).total_seconds())
    if seconds < 60:
        return "à l'instant"
    if seconds < 3600:
        return f"il y a {seconds // 60} min"
    if seconds < 86400:
        return f"il y a {seconds // 3600} h"
    return f"il y a {seconds // 86400} j"


def _rating_label(listing: Listing) -> str:
    raw = _raw(listing)
    user = raw.get("user") or {}
    if not isinstance(user, dict):
        return "⭐⭐⭐⭐⭐ (0)"
    feedback = (
        user.get("feedback_reputation")
        or user.get("feedback_rating")
        or user.get("rating")
    )
    count = (
        user.get("feedback_count")
        or user.get("item_count")
        or 0
    )
    try:
        score = float(feedback) if feedback is not None else 0.0
    except (TypeError, ValueError):
        score = 0.0
    # feedback_reputation Vinted est souvent 0..1
    stars = 5 if score >= 0.9 else max(0, min(5, round(score * 5)))
    if feedback is None:
        stars = 5
    try:
        count_i = int(count)
    except (TypeError, ValueError):
        count_i = 0
    return f"{'⭐' * stars}{'☆' * (5 - stars)} ({count_i})"


def _price_label(listing: Listing) -> str:
    raw = _raw(listing)
    currency = listing.currency or "EUR"
    price_cents = listing.price_cents

    total = raw.get("total_item_price")
    if isinstance(total, dict) and total.get("amount") is not None:
        amount = str(total["amount"])
        cur = total.get("currency_code") or currency
        base = (
            f"{price_cents / 100:.2f} {currency}"
            if price_cents is not None
            else f"{amount} {cur}"
        )
        return f"{base} | ≈ {amount} {cur} (TTC)"

    if price_cents is None:
        return "N/A"
    base = f"{price_cents / 100:.2f} {currency}"
    ttc_cents = int(round(price_cents * (1 + _TTC_RATE) + _TTC_FIXED_CENTS))
    return f"{base} | ≈ {ttc_cents / 100:.2f} {currency} (TTC)"


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


def build_listing_payload(listing: Listing) -> dict[str, Any]:
    """Construit embeds + boutons style aperçu riche."""
    seller_name, seller_avatar = _seller_info(listing)
    photos = _photo_urls(listing)

    fields = [
        {"name": "⌛ Publié", "value": _published_label(listing), "inline": True},
        {"name": "📕 Marque", "value": listing.brand or "—", "inline": True},
        {"name": "📏 Taille", "value": listing.size or "—", "inline": True},
        {"name": "🌟 Avis", "value": _rating_label(listing), "inline": True},
        {"name": "💎 État", "value": _condition_label(listing), "inline": True},
        {"name": "💰 Prix", "value": _price_label(listing), "inline": True},
    ]

    main: dict[str, Any] = {
        "title": listing.title[:256],
        "url": listing.url,
        "color": EMBED_COLOR,
        "fields": fields,
        "footer": {"text": "Vinted Bot"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if seller_name:
        author: dict[str, Any] = {"name": seller_name}
        if seller_avatar:
            author["icon_url"] = seller_avatar
        main["author"] = author
    if photos:
        # grande image (pas une mini thumbnail)
        main["image"] = {"url": photos[0]}

    embeds: list[dict[str, Any]] = [main]
    # images supplémentaires empilées (effet multi-photos)
    for photo_url in photos[1:4]:
        embeds.append(
            {
                "url": listing.url,
                "color": EMBED_COLOR,
                "image": {"url": photo_url},
            }
        )

    components = [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 5,
                    "label": "📄 Détails",
                    "url": listing.url,
                },
                {
                    "type": 2,
                    "style": 5,
                    "label": "💳 Acheter",
                    "url": listing.url,
                },
                {
                    "type": 2,
                    "style": 5,
                    "label": "🤝 Négocier",
                    "url": listing.url,
                },
            ],
        }
    ]
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

    def post_message(self, channel_id: str, payload: dict[str, Any]) -> None:
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

    def post_embed(self, channel_id: str, embed: dict[str, Any]) -> None:
        self.post_message(channel_id, {"embeds": [embed]})

    def post_listing(self, listing: Listing) -> str | None:
        """Poste canal marque (obligatoire) + #all (best-effort).

        Si le post marque réussit, on considère l'annonce postée même si #all échoue
        (évite les doublons marque au prochain run).
        """
        brand_channel_id = route_channel(listing.brand, self.channel_map)
        if not brand_channel_id:
            log.info(
                "discord_skipped_brand",
                brand=listing.brand,
                vinted_id=listing.vinted_id,
            )
            return None

        payload = build_listing_payload(listing)
        self.post_message(brand_channel_id, payload)

        all_channel = self.settings.discord_channel_all.strip()
        if all_channel and all_channel != brand_channel_id:
            try:
                time.sleep(self.settings.discord_post_delay_seconds)
                self.post_message(all_channel, payload)
            except Exception as exc:
                log.warning(
                    "discord_all_channel_failed",
                    vinted_id=listing.vinted_id,
                    error=str(exc),
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
) -> list[int]:
    """Poste les annonces. Retourne les listing.id postés avec succès."""
    cfg = settings or get_settings()
    if not cfg.discord_ready():
        log.info("discord_skipped", reason="not_configured")
        return []

    posted_ids: list[int] = []
    with DiscordNotifier(cfg) as notifier:
        for listing in listings:
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
            time.sleep(cfg.discord_post_delay_seconds)

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
