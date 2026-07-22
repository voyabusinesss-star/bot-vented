"""Configuration chargée depuis les variables d'environnement / .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Clé marque normalisée (espaces) → champ Settings / env DISCORD_CHANNEL_*
# Correspond aux salons Discord (les-classiques + luxe-revente).
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
    request_delay_seconds: float = 3.0
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

    # Salon regroupement (#all-vetement)
    discord_channel_all: str = ""
    discord_channel_logs: str = ""
    discord_post_delay_seconds: float = Field(default=1.5, ge=0.0)

    def brand_channel_map(self) -> dict[str, str]:
        """Marque suivie → channel id (seulement les IDs remplis)."""
        mapping: dict[str, str] = {}
        for brand_key, field_name in BRAND_CHANNEL_FIELDS.items():
            value = getattr(self, field_name, "") or ""
            value = value.strip()
            if value:
                mapping[brand_key] = value
        return mapping

    def discord_ready(self) -> bool:
        return bool(
            self.discord_enabled
            and self.discord_bot_token.strip()
            and self.discord_channel_all.strip()
            and self.brand_channel_map()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
