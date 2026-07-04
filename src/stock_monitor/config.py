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

    # Local analytical store (DuckDB). Gitignored; parent dir is created on demand.
    db_path: str = "data/stock_monitor.duckdb"

    # Where the trained model is persisted for the API to load.
    model_path: str = "models/latest.joblib"

    # Secondary short-horizon model (near-term read alongside the 12-month one).
    model_path_short: str = "models/latest_3m.joblib"
    label_window_months_short: int = 3

    # MLflow tracking (local file store; gitignored).
    mlflow_tracking_uri: str = "file:./mlruns"
    mlflow_experiment: str = "stock-monitor"

    # Alerts (Telegram). Empty = fall back to a logging notifier (no secrets needed).
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    alert_conviction_threshold: int = 70

    # Optional data keys (activated when set).
    finnhub_api_key: str = ""

    # News / sentiment pillar.
    news_lookback_days: int = 7
    sentiment_negative_threshold: float = -0.25  # below this = material negative news
    sentiment_backend: str = "vader"  # "vader" (default) or "finbert" (needs [finbert])

    # Scheduler + heartbeat.
    scan_hour: int = 22  # local hour for the daily universe scan
    heartbeat_max_age_hours: int = 26  # alert if no successful scan within this window
    # Run the scheduler in-process with the API (set True in production/containers).
    run_scheduler: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
