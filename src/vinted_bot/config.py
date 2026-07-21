"""Configuration chargée depuis les variables d'environnement / .env."""

from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
