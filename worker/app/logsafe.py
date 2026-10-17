"""Secret redaction (Phase 10 hardening): every configured secret value is replaced with *** in
log records and in stored error texts, whatever library produced them."""

import logging

from app.config import get_settings

SECRET_FIELDS = (
    "supabase_service_role_key",
    "anthropic_api_key",
    "broker_api_key",
    "broker_api_secret",
    "broker_access_token",
    "broker_totp_secret",
    "twelvedata_api_key",
    "alphavantage_api_key",
    "telegram_bot_token",
    "github_token_config_pr",
    "r2_access_key",
    "r2_secret_key",
    "healthcheck_ping_url",
    "database_url",
)
MIN_LEN = 8  # never redact short values (would mangle ordinary text)


def secrets() -> list[str]:
    st = get_settings()
    vals = [str(getattr(st, f, "") or "") for f in SECRET_FIELDS]
    return sorted({v for v in vals if len(v) >= MIN_LEN}, key=len, reverse=True)


def redact(text: str) -> str:
    for v in secrets():
        if v in text:
            text = text.replace(v, "***")
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        return True


def install() -> None:
    for h in logging.getLogger().handlers:
        h.addFilter(RedactFilter())
