"""Konfiguracja pipeline'u (pydantic-settings, czytana z .env).

Wszystkie endpointy/creds są konfigurowalne — podmiana modelu (gemma4 →
gemma4-uncensored) to zmiana GB10_MODEL + GB10_MODEL_KEY, bez zmian w kodzie.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # self-hosted model (gb10, OpenAI-compatible)
    gb10_base_url: str = "http://gb10:8000/v1"
    gb10_api_key: str = "sk-none"
    gb10_model: str = "gemma4"          # nazwa modelu na serwerze (wire `model`)
    gb10_model_key: str = "gb10-gemma4"  # etykieta w SQLite (do porównań)
    gb10_reasoning: bool = False

    # quotes API (read-only GET)
    quotes_base_url: str = "http://127.0.0.1:8090"

    # gielda-agents Postgres — READ ONLY
    agents_db_url: str = (
        "postgresql+asyncpg://agents:agents@127.0.0.1:5434/gielda_agents"
    )

    # prompty (read-only) + storage
    agents_prompts_dir: str = "/Users/alex/Documents/gielda/gielda-agents/prompts"
    sqlite_path: str = "./data/uncensored.db"

    @property
    def agents_dsn(self) -> str:
        """DSN dla surowego asyncpg (bez `+asyncpg`, którego asyncpg.connect nie rozumie)."""
        return self.agents_db_url.replace("postgresql+asyncpg://", "postgresql://")


def load_settings() -> Settings:
    return Settings()
