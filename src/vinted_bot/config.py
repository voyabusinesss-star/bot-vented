"""Configuration chargée depuis les variables d'environnement / .env."""

from __future__ import annotations

import base64
import re
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Clé marque normalisée (espaces) → champ Settings / env DISCORD_CHANNEL_*
# Correspond aux salons Discord (les-classiques / indémodables + luxe-revente).
BRAND_CHANNEL_FIELDS: dict[str, str] = {
    "ralph lauren": "discord_channel_ralph_lauren",
    "nike": "discord_channel_nike",
    "adidas": "discord_channel_adidas",
    "carhartt": "discord_channel_carhartt",
    "stone island": "discord_channel_stone_island",
    "lacoste": "discord_channel_lacoste",
    "tommy hilfiger": "discord_channel_tommy_hilfiger",
    "the north face": "discord_channel_the_north_face",
    "stussy": "discord_channel_stussy",
    "dickies": "discord_channel_dickies",
    "under armour": "discord_channel_under_armour",
    "supreme": "discord_channel_supreme",
    "levis": "discord_channel_levis",
    "ami paris": "discord_channel_ami_paris",
    "the kooples": "discord_channel_the_kooples",
    "columbia": "discord_channel_columbia",
    # Luxe revente
    "moncler": "discord_channel_moncler",
    "louis vuitton": "discord_channel_louis_vuitton",
    "prada": "discord_channel_prada",
    "celine": "discord_channel_celine",
    "fendi": "discord_channel_fendi",
    "burberry": "discord_channel_burberry",
    "saint laurent": "discord_channel_saint_laurent",
    "hermes": "discord_channel_hermes",
    "givenchy": "discord_channel_givenchy",
    "loewe": "discord_channel_loewe",
    "jacquemus": "discord_channel_jacquemus",
    "acne studios": "discord_channel_acne_studios",
    "toteme": "discord_channel_toteme",
    "ganni": "discord_channel_ganni",
    "comme des garcons": "discord_channel_cdg",
}

# Marques catégorie Discord « indémodables / les-classiques » (#all-vetement)
CLASSIQUE_BRANDS: frozenset[str] = frozenset(
    {
        "ralph lauren",
        "nike",
        "adidas",
        "carhartt",
        "stone island",
        "lacoste",
        "tommy hilfiger",
        "the north face",
        "stussy",
        "dickies",
        "under armour",
        "supreme",
        "levis",
        "ami paris",
        "the kooples",
        "columbia",
    }
)

# Marques catégorie Discord « Luxe revente » (pas dans #all-vetement)
LUXE_BRANDS: frozenset[str] = frozenset(
    {
        "moncler",
        "louis vuitton",
        "prada",
        "celine",
        "fendi",
        "burberry",
        "saint laurent",
        "hermes",
        "givenchy",
        "loewe",
        "jacquemus",
        "acne studios",
        "toteme",
        "ganni",
        "comme des garcons",
    }
)

# Pépites Sneakers — salons dédiés chaussures
# Nike/Adidas : override sneakers (sinon fallback salon vêtements)
BRAND_SNEAKER_CHANNEL_FIELDS: dict[str, str] = {
    "nike": "discord_channel_nike_sneakers",
    "adidas": "discord_channel_adidas_sneakers",
    "salomon": "discord_channel_salomon",
    "new balance": "discord_channel_new_balance",
    "asics": "discord_channel_asics",
    "hoka": "discord_channel_hoka",
    "dr martens": "discord_channel_dr_martens",
    "on cloud": "discord_channel_on_cloud",
    "ugg": "discord_channel_ugg",
    "birkenstock": "discord_channel_birkenstock",
    "saucony": "discord_channel_saucony",
    "timberland": "discord_channel_timberland",
    "jordan": "discord_channel_jordan",
    "yeezy": "discord_channel_yeezy",
}


def sanitize_discord_channel_id(raw: str | None) -> str:
    """Extrait l'ID salon Discord (ignore guild/catégorie collés ou URLs)."""
    if not raw:
        return ""
    text = str(raw).strip().strip('"').strip("'")
    if not text:
        return ""
    match = re.search(r"channels/\d+/(\d{17,20})", text)
    if match:
        return match.group(1)
    if "/" in text:
        parts = [p for p in text.split("/") if p.isdigit() and len(p) >= 17]
        if parts:
            return parts[-1]
    digits = re.findall(r"\d{17,20}", text)
    if digits:
        return digits[-1]
    return text if text.isdigit() else ""


def discord_application_id(bot_token: str) -> str:
    """Extrait l'application id depuis le token bot Discord."""
    if not bot_token:
        return ""
    app_part = bot_token.split(".", 1)[0]
    pad = "=" * (-len(app_part) % 4)
    return base64.b64decode(app_part + pad).decode()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = (
        "postgresql+psycopg://vinted:vinted@localhost:5432/vinted_bot"
    )

    vinted_base_url: str = "https://www.vinted.fr"
    request_delay_seconds: float = 1.0
    max_retries: int = 3
    scrape_headless: bool = True

    discord_enabled: bool = True
    discord_bot_token: str = ""

    # Marques suivies (catégorie les-classiques)
    discord_channel_ralph_lauren: str = ""
    discord_channel_nike: str = ""
    discord_channel_adidas: str = ""
    discord_channel_carhartt: str = ""
    discord_channel_stone_island: str = ""
    discord_channel_lacoste: str = ""
    discord_channel_tommy_hilfiger: str = ""
    discord_channel_the_north_face: str = ""
    discord_channel_stussy: str = ""
    discord_channel_dickies: str = ""
    discord_channel_under_armour: str = ""
    discord_channel_supreme: str = ""
    discord_channel_levis: str = ""
    discord_channel_ami_paris: str = ""
    discord_channel_the_kooples: str = ""
    discord_channel_columbia: str = ""

    # Marques luxe (catégorie LUXE REVENTE)
    discord_channel_moncler: str = ""
    discord_channel_louis_vuitton: str = ""
    discord_channel_prada: str = ""
    discord_channel_celine: str = ""
    discord_channel_fendi: str = ""
    discord_channel_burberry: str = ""
    discord_channel_saint_laurent: str = ""
    discord_channel_hermes: str = ""
    discord_channel_givenchy: str = ""
    discord_channel_loewe: str = ""
    discord_channel_jacquemus: str = ""
    discord_channel_acne_studios: str = ""
    discord_channel_toteme: str = ""
    discord_channel_ganni: str = ""
    discord_channel_cdg: str = ""

    # Pépites Sneakers (coller les IDs Discord des salons)
    discord_channel_nike_sneakers: str = ""
    discord_channel_adidas_sneakers: str = ""
    discord_channel_salomon: str = ""
    discord_channel_new_balance: str = ""
    discord_channel_asics: str = ""
    discord_channel_hoka: str = ""
    discord_channel_dr_martens: str = ""
    discord_channel_on_cloud: str = ""
    discord_channel_ugg: str = ""
    discord_channel_birkenstock: str = ""
    discord_channel_saucony: str = ""
    discord_channel_timberland: str = ""
    discord_channel_jordan: str = ""
    discord_channel_yeezy: str = ""

    # Salon regroupement (#all-vetement)
    discord_channel_all: str = ""
    discord_channel_logs: str = ""
    # Annonces liaison compte Vinted (visible serveur). Fallback : logs.
    discord_channel_vinted_links: str = ""
    # Salon #mes-alertes — panneau filtres privés
    discord_channel_mes_alertes: str = ""
    # Salon alertes niches (fallback / combos)
    discord_channel_niches: str = ""
    # Salon demo / aperçu marketing détecteur (post-detector-apercu)
    discord_channel_niches_demo: str = ""
    # Webhook salon demo (Integrations → Webhooks) — avatar serveur sans permission bot
    discord_webhook_niches_demo: str = ""
    # Salon niches vinted (catalogue 1000 niches + intro)
    discord_channel_niches_vinted: str = ""
    discord_webhook_niches_vinted: str = ""
    niches_vinted_catalog_path: str = "config/Resello_1000_Niches_Vinted.xlsx"
    # Fiches produit live (deep-dive ~1h → analyse niche)
    discord_channel_fiches_produit: str = ""
    # Durée deep-dive d'une niche avant publication fiche (secondes, défaut 1h)
    fiches_develop_seconds: float = Field(default=3600.0, ge=30.0)
    # Market intel — salons dédiés (détecteur de niches)
    discord_channel_marques: str = ""
    discord_channel_modeles: str = ""
    discord_channel_tendances: str = ""
    # Rapport quotidien TOP tendances (pas de spam continu)
    discord_channel_tendances_du_jour: str = ""
    discord_channel_classements: str = ""
    discord_channel_pepites: str = ""
    discord_channel_statistiques: str = ""
    # Heure locale Europe/Paris pour le rapport quotidien (0-23)
    daily_trends_report_hour: int = Field(default=8, ge=0, le=23)
    # Nombre max de tendances dans le rapport du jour
    daily_trends_max: int = Field(default=8, ge=3, le=10)
    # Guild ID (mode dev : slash commands instantanés). Optionnel.
    discord_guild_id: str = ""
    # IDs Discord autorisés à /set-plan (séparés par des virgules). Vide = owner guild seulement via checks basiques.
    discord_filter_admin_ids: str = ""
    # Alertes DM filtres privés : délai entre chaque envoi (ex. 60 = 1 min)
    private_filter_dm_delay_seconds: float = Field(default=60.0, ge=0.0)
    # Ne DM que les annonces publiées récemment (défaut 15 min)
    private_filter_max_age_seconds: float = Field(default=900.0, ge=30.0)
    # Max DM privés par passage scrape
    private_filter_max_dm_per_scrape: int = Field(default=3, ge=1, le=10)
    # Intervalle dédié scrape filtres privés (secondes) — boucle continue
    private_filter_scrape_interval_seconds: float = Field(default=20.0, ge=10.0)
    # Portail liaison Vinted (URL publique HTTPS, ex. ngrok)
    vinted_link_public_url: str = ""
    vinted_link_server_host: str = "0.0.0.0"
    vinted_link_server_port: int = Field(default=8787, ge=1, le=65535)
    # Délai minimal entre posts Discord (évite 429 sans ralentir le scrape)
    discord_post_delay_seconds: float = Field(default=0.25, ge=0.0)

    def brand_channel_map(self) -> dict[str, str]:
        """Marque suivie → channel id (seulement les IDs remplis)."""
        mapping: dict[str, str] = {}
        for brand_key, field_name in BRAND_CHANNEL_FIELDS.items():
            value = sanitize_discord_channel_id(getattr(self, field_name, "") or "")
            if value:
                mapping[brand_key] = value
        return mapping

    def sneaker_channel_map(self) -> dict[str, str]:
        """Marque sneakers → channel id Pépites Sneakers."""
        mapping: dict[str, str] = {}
        for brand_key, field_name in BRAND_SNEAKER_CHANNEL_FIELDS.items():
            value = sanitize_discord_channel_id(getattr(self, field_name, "") or "")
            if value:
                mapping[brand_key] = value
        return mapping

    def all_tracked_brands(self) -> set[str]:
        """Marques actives (vêtements et/ou sneakers)."""
        return set(self.brand_channel_map()) | set(self.sneaker_channel_map())

    def discord_ready(self) -> bool:
        return bool(
            self.discord_enabled
            and self.discord_bot_token.strip()
            and self.discord_channel_all.strip()
            and self.all_tracked_brands()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
