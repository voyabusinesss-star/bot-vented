"""Configuration chargée depuis les variables d'environnement / .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    discord_channel_nike: str = ""
    discord_channel_adidas: str = ""
    # Salon qui regroupe toutes les annonces des marques suivies
    discord_channel_all: str = ""
    discord_channel_logs: str = ""
    discord_post_delay_seconds: float = Field(default=1.5, ge=0.0)

    def brand_channel_map(self) -> dict[str, str]:
        """Marque suivie → channel id (pas de fallback)."""
        mapping: dict[str, str] = {}
        if self.discord_channel_nike.strip():
            mapping["nike"] = self.discord_channel_nike.strip()
        if self.discord_channel_adidas.strip():
            mapping["adidas"] = self.discord_channel_adidas.strip()
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
