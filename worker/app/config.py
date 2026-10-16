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
    ai_model_researcher: str = ""  # cheap
    ai_model_novelty: str = ""  # cheap
    ai_model_bull: str = ""  # mid
    ai_model_bear: str = ""  # mid
    ai_model_judge: str = ""  # strongest
    ai_multi_agent_max_per_day: int = 30
    # Latest training cutoff of any AI model used (YYYY-MM-DD). Before it, AI gives labels only
    # (DESIGN_V2 "Research integrity" R1); empty = treat all history as contaminated.
    ai_model_knowledge_cutoff: str = ""
    ai_daily_budget_inr: float = 100
    ai_monthly_budget_inr: float = 2000

    # Broker (market data only; no order placement)
    broker: str = "upstox"
    broker_api_key: str = ""
    broker_api_secret: str = ""
    broker_client_id: str = ""
    broker_access_token: str = ""
    broker_totp_secret: str = ""
    broker_token_issued_on: str = ""  # YYYY-MM-DD; Upstox analytics token lasts 1 year

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

    # Engine (DESIGN_V2)
    risk_free_rate: float = 0.065
    edge_ratio_min: float = 1.2
    mc_paths: int = 10000
    # Until calibration (Phase 5): share of simulated paths from the AI scenario tree; the rest
    # follow the option-implied market distribution (no drift, ATM IV). 1.0 = AI only.
    scenario_shrink_weight: float = 0.5
    # Paper position size and booking (whole lots only)
    lots_per_signal: int = 3
    partial_booking_mode: str = "per_lot"  # per_lot | single_exit

    # History store (DuckDB + Parquet on the worker VM)
    history_data_dir: str = "/data/history"
    duckdb_path: str = "/data/history/history.duckdb"
    gdelt_enabled: bool = True
    # Analogue weight = n / (n + prior); analogues ignored below the minimum number of events
    analogue_prior_n: float = 20
    analogue_min_n: int = 5

    # Optional Cloudflare R2 backup of the Parquet history (Phase 4), off by default
    r2_backup_enabled: bool = False
    r2_account_id: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_bucket: str = ""

    # Health
    job_failure_alert_threshold: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
