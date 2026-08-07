"""Type-safe configuration loaded from the environment / gitignored `.env`.

Secrets never live in code. See `.env.example` for the template.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings sourced from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def _blank_placeholder_uses_default(cls, data: object) -> object:
        """Treat empty `.env` placeholders as "unset" so the field default applies.

        Lets users pre-stage blank keys (e.g. ``OPEN_ROUTER_API_KEY=`` /
        ``LLM_ANALYST_ENABLED=``) and fill them in later without breaking startup.
        Blank strings are dropped only for non-``str`` fields (bool/int/float);
        string fields keep ``""`` as their intended "disabled" sentinel.
        """
        if not isinstance(data, dict):
            return data
        cleaned: dict[object, object] = {}
        for key, value in data.items():
            field = cls.model_fields.get(str(key).lower())
            if (
                field is not None
                and field.annotation is not str
                and isinstance(value, str)
                and value.strip() == ""
            ):
                continue  # skip → pydantic uses the field's declared default
            cleaned[key] = value
        return cleaned

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

    # How many years of price history to pull when TRAINING. Deeper = more labelled
    # rows + a richer similar-setups base rate. Tiingo's free tier already returns
    # ~35 years for US large-caps, so this costs nothing. (Fundamentals coverage from
    # SEC EDGAR effectively bounds usable rows to the XBRL era, ~2009+.)
    training_history_years: int = 30

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

    # Weekly walk-forward backtest that feeds the edge scorecard (historical half).
    backtest_weekly: bool = True
    backtest_hour: int = 4  # runs after the weekly retrain, same day

    # OpenRouter-backed LLM "AI analyst" second opinion (optional, has per-call cost).
    # Empty key = disabled. The legacy OpenAI setting is retained only for config compatibility.
    llm_analyst_enabled: bool = False
    openrouter_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OPEN_ROUTER_API_KEY", "OPENROUTER_API_KEY"),
    )
    openai_api_key: str = ""
    llm_model: str = "openai/gpt-5.6-luna"
    llm_explain_model: str = "openai/gpt-4o-mini"
    llm_base_url: str = "https://openrouter.ai/api/v1"

    # Optional data keys (activated when set).
    finnhub_api_key: str = ""
    eodhd_api_key: str = ""  # EODHD: deep history + historical news (one-month backfill)
    tiingo_api_key: str = ""  # Tiingo: reliable EOD prices (free tier is generous)
    alphavantage_api_key: str = ""  # Alpha Vantage NEWS_SENTIMENT: free 25/day gap backfill
    fred_api_key: str = ""  # FRED/ALFRED: free PIT macro series (rates, CPI, unemployment)

    # Preferred price source: "yfinance" (default, no key), "tiingo", or "eodhd".
    price_provider: str = "yfinance"

    # Persistent price cache (build a local Tiingo history store so retrains never
    # re-download decades of data and never brush Tiingo's ~50-req/hr free-tier cap).
    # When True, TRAINING reads prices purely from this cache (zero upstream calls);
    # a daily append job pulls only the newest bars so nothing is lost going forward.
    use_price_cache: bool = True
    price_cache_path: str = "data/prices.duckdb"
    price_cache_refresh_hour: int = 20  # daily hour to append the newest bars per name

    # Historical-news backfill (learn-from-history). Only used by the backfill job.
    news_backfill_years: int = 5  # how far back to pull + score news for the feature
    news_backfill_max_per_day: int = 50  # cap articles/day to keep scoring bounded

    # One-time gap backfill (Alpha Vantage) for 2024-01 -> ~Finnhub's 1-year reach.
    # AV free tier is 25 requests/day, so each run is capped and resumes via a state
    # table until every name is covered. Start = day after FNSPID's last date.
    news_gap_start: str = "2024-01-10"  # ISO date; FNSPID history ends 2024-01-09
    news_gap_backfill_max_calls: int = 24  # AV calls per run (stay under the 25/day cap)
    news_gap_backfill_hour: int = 2  # nightly hour to auto-continue the gap backfill

    # Macro/regime features (FRED). Refreshed nightly (idempotent, ~5 free calls); the
    # PIT lookup uses ALFRED vintages so no future data leaks into historical rows.
    macro_refresh_hour: int = 1

    # Shared secret protecting the API when exposed publicly. Empty = auth disabled
    # (local dev). When set, callers must send it as the `X-API-Key` header.
    api_shared_secret: str = ""

    # Operational metrics (Prometheus). Served on a localhost-only port so Prometheus
    # scrapes it without the API key and it never rides the public Tailscale funnel.
    metrics_enabled: bool = True
    metrics_port: int = 9137

    # News / sentiment pillar.
    news_lookback_days: int = 7
    sentiment_negative_threshold: float = -0.25  # below this = material negative news
    sentiment_backend: str = "vader"  # "vader" (default) or "finbert" (needs [finbert])

    # Scheduler + heartbeat.
    scheduler_timezone: str = "America/Chicago"
    scan_hour: int = 22  # local hour for the daily universe scan
    # Nightly-scan breadth: "default" (curated ~48, the ranked page) or "sp500"
    # (full index, slow discovery). The ranked page + paper picks read this list, so
    # "default" keeps it to the names we actually track and can score reliably.
    scan_universe: str = "default"
    # Hour to collect + archive today's news (default just before the scan, so the
    # sentiment feature is fresh). Runs daily so we never lose a day of headlines.
    news_collect_hour: int = 21
    heartbeat_max_age_hours: int = 26  # alert if no successful scan within this window
    # Run the scheduler in-process with the API (set True in production/containers).
    run_scheduler: bool = False

    # Holdings alerts — proactive Telegram pings on the positions you track.
    # Urgent (hourly): a holding's exit signal newly turns to "consider selling",
    # a take-profit milestone, or a sharp one-day move. Routine trim/hold reads go
    # into the daily digest instead. All alerts are debounced so you get one ping
    # per event, not repeated nagging.
    holdings_alert_debounce_hours: int = 24  # generic per-holding alert cooldown
    take_profit_pct: float = 0.20  # ping when a holding is up ≥ this vs your entry
    take_profit_cooldown_hours: int = 168  # re-nudge at most weekly as it keeps climbing
    sharp_move_pct: float = 0.07  # ping when a holding moves ≥ this in a single day


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
