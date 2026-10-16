"""Application settings, loaded from environment variables (and .env for local runs)."""

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

IST = ZoneInfo("Asia/Kolkata")


class Settings(BaseSettings):
    # Blank values (KEY=) fall back to the defaults below, so .env can hold keys only.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # App
    app_env: str = "development"
    tz: str = "Asia/Kolkata"
    log_level: str = "INFO"

    # Database (Supabase Postgres)
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # AI (Anthropic); model ids come from env, never hard-coded
    anthropic_api_key: str = ""
    ai_model_tagger: str = ""
    ai_model_analyst: str = ""
    ai_model_reviewer: str = ""
    ai_daily_budget_inr: float = 100
    ai_monthly_budget_inr: float = 2000

    # Broker (market data only; no order placement)
    broker: str = "upstox"
    broker_api_key: str = ""
    broker_api_secret: str = ""
    broker_client_id: str = ""
    broker_access_token: str = ""
    broker_totp_secret: str = ""

    # Market / macro data
    macro_provider: str = "yfinance"
    twelvedata_api_key: str = ""
    alphavantage_api_key: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    dashboard_base_url: str = ""

    # Schedules and thresholds
    news_poll_min: int = 5
    option_poll_active_min: int = 2
    signal_score_threshold: int = 65

    # Health
    job_failure_alert_threshold: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
