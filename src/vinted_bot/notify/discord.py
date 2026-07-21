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


def normalize_brand(brand: str | None) -> str:
    if not brand:
        return ""
    text = unicodedata.normalize("NFKD", brand)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    text = " ".join(text.split())
    return text


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


def build_listing_embed(listing: Listing) -> dict[str, Any]:
    price = (
        f"{listing.price_cents / 100:.2f} {listing.currency}"
        if listing.price_cents is not None
        else "N/A"
    )
    fields = [
        {"name": "Prix", "value": price, "inline": True},
        {"name": "Marque", "value": listing.brand or "—", "inline": True},
        {"name": "Taille", "value": listing.size or "—", "inline": True},
    ]
    if listing.condition:
        fields.append(
            {"name": "État", "value": listing.condition, "inline": True}
        )

    embed: dict[str, Any] = {
        "title": listing.title[:256],
        "url": listing.url,
        "color": 0x09B1BA,
        "fields": fields,
        "footer": {"text": f"Vinted · id {listing.vinted_id}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if listing.photos:
        embed["thumbnail"] = {"url": listing.photos[0].url}
    return embed


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

    def post_embed(self, channel_id: str, embed: dict[str, Any]) -> None:
        response = self.client.post(
            f"/channels/{channel_id}/messages",
            json={"embeds": [embed]},
        )
        if response.status_code == 429:
            retry_after = float(response.json().get("retry_after", 2))
            log.warning("discord_rate_limited", retry_after=retry_after)
            time.sleep(retry_after)
            response = self.client.post(
                f"/channels/{channel_id}/messages",
                json={"embeds": [embed]},
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Discord API {response.status_code}: {response.text[:400]}"
            )

    def post_listing(self, listing: Listing) -> str | None:
        """Poste dans le canal marque + le salon regroupement. Retourne l'id canal marque."""
        brand_channel_id = route_channel(listing.brand, self.channel_map)
        if not brand_channel_id:
            log.info(
                "discord_skipped_brand",
                brand=listing.brand,
                vinted_id=listing.vinted_id,
            )
            return None

        embed = build_listing_embed(listing)
        self.post_embed(brand_channel_id, embed)

        all_channel = self.settings.discord_channel_all.strip()
        if all_channel and all_channel != brand_channel_id:
            time.sleep(self.settings.discord_post_delay_seconds)
            self.post_embed(all_channel, embed)

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
                "Connexion Discord OK — les annonces iront dans "
                "chaque canal marque + ce salon regroupement."
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
