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

    # Email digest (SMTP). Empty smtp_host = email disabled (Telegram/logging still work).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""  # comma-separated recipients

    # Digest cadence (local hours / weekday). Daily digest = Telegram; weekly = email.
    daily_digest_hour: int = 23  # after the daily scan
    weekly_digest_day: str = "mon"  # apscheduler day_of_week
    weekly_digest_hour: int = 8
    digest_top_n: int = 10

    # Paper mode: simulate the daily "buy" call and score it vs the benchmark later.
    paper_min_conviction: int = 70  # only paper-track names in the buy zone
    paper_horizon_months: int = 12  # how long a paper pick is held before it's scored

    # Scheduled retraining (the heavy job). Off by default; enable on the always-on box.
    retrain_weekly: bool = False
    retrain_day_of_week: str = "sun"
    retrain_hour: int = 3

    # LLM "AI analyst" second opinion (optional, has per-call cost). Empty key = disabled.
    llm_analyst_enabled: bool = False
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"

    # Optional data keys (activated when set).
    finnhub_api_key: str = ""
    eodhd_api_key: str = ""  # EODHD: deep history + historical news (one-month backfill)
    tiingo_api_key: str = ""  # Tiingo: reliable EOD prices (free tier is generous)

    # Preferred price source: "yfinance" (default, no key), "tiingo", or "eodhd".
    price_provider: str = "yfinance"

    # Historical-news backfill (learn-from-history). Only used by the backfill job.
    news_backfill_years: int = 5  # how far back to pull + score news for the feature
    news_backfill_max_per_day: int = 50  # cap articles/day to keep scoring bounded

    # Shared secret protecting the API when exposed publicly. Empty = auth disabled
    # (local dev). When set, callers must send it as the `X-API-Key` header.
    api_shared_secret: str = ""

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
