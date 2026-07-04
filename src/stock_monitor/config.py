"""Type-safe configuration loaded from the environment / gitignored `.env`.

Secrets never live in code. See `.env.example` for the template.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings sourced from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # SEC EDGAR requires a descriptive User-Agent with contact info (fair-access policy).
    sec_user_agent: str = "Stock-Monitor/0.1 (you@example.com)"

    # Forward-return label window in months. Phase 0 default = 12 (cleaner long-term signal).
    label_window_months: int = 12

    # HTTP cache TTL in seconds (politeness + free-tier rate-limit safety).
    http_cache_ttl: int = 86_400


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
