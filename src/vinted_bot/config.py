"""Configuration chargée depuis les variables d'environnement / .env."""

from __future__ import annotations

import base64
import re
from functools import lru_cache
from typing import Self

from pydantic import Field, field_validator, model_validator
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

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, value: object) -> object:
        """Railway injecte postgresql:// — SQLAlchemy a besoin de +psycopg."""
        if not isinstance(value, str):
            return value
        url = value.strip()
        if url.startswith("postgresql+psycopg://"):
            return url
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        return url

    vinted_base_url: str = "https://www.vinted.fr"
    request_delay_seconds: float = 0.5
    max_retries: int = 3
    scrape_headless: bool = True
    # Navigateurs sticky (3 ≈ bon rapport vitesse/RAM Railway ; staggered start)
    scrape_parallel_workers: int = Field(default=3, ge=1, le=20)
    # Pause minimale entre deux recherches du même worker (quasi temps réel)
    scrape_poll_seconds_min: float = Field(default=0.3, ge=0.1)
    scrape_poll_seconds_max: float = Field(default=0.8, ge=0.1)
    # Proxies HTTP/SOCKS (CSV ou lignes) — 1 sticky par worker, rotation au recycle
    scrape_proxy_urls: list[str] = Field(default_factory=list)

    @field_validator("scrape_proxy_urls", mode="before")
    @classmethod
    def _parse_scrape_proxy_urls(cls, value: object) -> list[str]:
        from vinted_bot.utils.proxy import parse_proxy_url_list

        return parse_proxy_url_list(value)

    @model_validator(mode="after")
    def _ensure_poll_range(self) -> Self:
        if self.scrape_poll_seconds_max < self.scrape_poll_seconds_min:
            self.scrape_poll_seconds_max = self.scrape_poll_seconds_min
        return self

    discord_enabled: bool = True
    discord_bot_token: str = ""

    @field_validator("discord_bot_token", mode="before")
    @classmethod
    def _strip_discord_bot_token(cls, value: object) -> object:
        # Railway / collage .env laisse souvent un \\n → Illegal header value
        if isinstance(value, str):
            return value.strip()
        return value

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
    # Hébergement technique du xlsx catalogue (salon admin, jamais le détecteur public)
    discord_channel_catalog_host: str = ""
    # Annonces liaison compte Vinted (visible serveur). Fallback : logs.
    discord_channel_vinted_links: str = ""
    # Salon #mes-alertes — panneau filtres privés
    discord_channel_mes_alertes: str = ""
    # Salon règlement — validation par bouton (post-reglement)
    discord_channel_reglement: str = ""
    # Salon bienvenue — visible avant validation du règlement
    discord_channel_bienvenue: str = ""
    discord_channel_presentation: str = ""
    discord_channel_annonces: str = ""
    discord_channel_concours: str = ""
    # Rôle attribué après acceptation du règlement (ID Discord)
    discord_role_reglement_verified: str = ""
    # Rôle Resello VIP (legacy / fallback si les rôles par tier ne sont pas définis)
    discord_role_resello_vip: str = ""
    # Rôles Discord par offre Whop (accès salons) — empilés : Pro = starter+pro, etc.
    discord_role_sub_starter: str = ""
    discord_role_sub_pro: str = ""
    discord_role_sub_proplus: str = ""
    # Catégorie réservée Pro / Pro+ (Starter : pas d'accès)
    discord_category_private_tools: str = ""
    # Message règlement existant (post-reglement --attach)
    discord_reglement_message_id: str = ""
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
    # Salon Vintify (vintify.me) — intro marketing IA photos
    discord_channel_vintify: str = ""
    discord_webhook_vintify: str = ""
    vintify_site_url: str = "https://vintify.me/"
    vintify_preview_image_path: str = "config/vintify-preview.png"
    # Salon abonnements (Starter / Pro / Pro+)
    discord_channel_subscriptions: str = ""
    discord_webhook_subscriptions: str = ""
    subscriptions_images_path: str = "config/subscriptions"
    subscriptions_checkout_url: str = ""
    subscriptions_checkout_starter: str = ""
    subscriptions_checkout_pro: str = ""
    subscriptions_checkout_proplus: str = ""
    # Salon guide fiscalité (post-fiscalite)
    discord_channel_fiscalite: str = ""
    discord_webhook_fiscalite: str = ""
    fiscalite_guides_path: str = "config/guides"
    # Salon panneau tickets recrutement
    discord_channel_recruitment: str = ""
    discord_webhook_recruitment: str = ""
    # Catégorie où créer les salons privés recrutement-*
    discord_category_recruitment_tickets: str = ""
    # Rôle staff qui voit / gère les tickets (ex. Sous Responsable)
    discord_role_recruitment_staff: str = ""
    # Salon panneau tickets aide / support
    discord_channel_support: str = ""
    discord_webhook_support: str = ""
    # Catégorie tickets aide (vide = même que recrutement)
    discord_category_support_tickets: str = ""
    # Rôle staff tickets aide (vide = même que recrutement)
    discord_role_support_staff: str = ""
    # Salon fournisseurs (Fleek)
    discord_channel_fournisseurs: str = ""
    discord_webhook_fournisseurs: str = ""
    fleek_banner_path: str = "config/fleek-banner.png"
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
    # Alertes DM filtres privés : délai court entre envois (worker async, anti 429)
    private_filter_dm_delay_seconds: float = Field(default=0.4, ge=0.0)
    # Ne DM que les annonces publiées récemment (défaut 15 min)
    private_filter_max_age_seconds: float = Field(default=900.0, ge=30.0)
    # Max matches mis en file par passage scrape (le worker envoie ensuite)
    private_filter_max_dm_per_scrape: int = Field(default=50, ge=1, le=200)
    # Intervalle dédié scrape filtres privés (secondes) — boucle continue
    private_filter_scrape_interval_seconds: float = Field(default=8.0, ge=3.0)
    # Portail liaison Vinted (URL publique HTTPS, ex. ngrok)
    vinted_link_public_url: str = ""
    vinted_link_server_host: str = "0.0.0.0"
    vinted_link_server_port: int = Field(default=8787, ge=1, le=65535)
    # Whop — abonnements Nos offres (webhook + mapping produits)
    whop_webhook_secret: str = ""
    whop_api_key: str = ""
    whop_product_starter: str = ""
    whop_product_pro: str = ""
    whop_product_proplus: str = ""
    whop_webhook_host: str = "0.0.0.0"
    whop_webhook_port: int = Field(default=8788, ge=1, le=65535)
    # Railway injecte PORT — prioritaire pour exposer le webhook en HTTPS
    port: int | None = Field(default=None, ge=1, le=65535)
    # Délai minimal entre posts Discord (évite 429 sans ralentir le scrape)
    discord_post_delay_seconds: float = Field(default=0.0, ge=0.0)
    # Salon aperçu bot — flux public ralenti (sans boutons achat/négociation)
    discord_channel_bot_preview: str = ""
    # Intervalle mini entre 2 pings aperçu (secondes) — défaut ~2,5 min
    bot_preview_interval_seconds: float = Field(default=150.0, ge=60.0)

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

    def effective_whop_webhook_port(self) -> int:
        """PORT (Railway) prioritaire, sinon WHOP_WEBHOOK_PORT, sinon 8080."""
        import os

        raw = (os.environ.get("PORT") or "").strip()
        if raw.isdigit():
            return int(raw)
        if self.port is not None:
            return int(self.port)
        return int(self.whop_webhook_port or 8080)


@lru_cache
def get_settings() -> Settings:
    return Settings()
