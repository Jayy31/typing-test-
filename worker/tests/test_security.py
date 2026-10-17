"""Phase 10 security: no secrets in the repository, secrets redacted from logs and job errors."""

import logging
import re
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from app import logsafe
from app.config import get_settings
from app.db.models import JobRun
from app.db.session import session_scope
from app.jobs import run_job

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = {
    "anthropic key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "github token": r"(ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})",
    "aws key": r"AKIA[0-9A-Z]{16}",
    "telegram bot token": r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}\b",
    "private key": r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "jwt": r"eyJhbGciOi[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.",
}


def test_no_secrets_in_tracked_files() -> None:
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    hits = []
    for f in files:
        p = ROOT / f
        if (
            p.suffix in {".png", ".pdf", ".parquet", ".duckdb", ".ico"}
            or "lock" in p.name
            or not p.is_file()
        ):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{f}: {name}" for name, pat in PATTERNS.items() if re.search(pat, text)]
    assert not hits, f"possible secrets committed: {hits}"


def test_env_example_has_blank_values() -> None:
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            assert line.split("=", 1)[1] == "", (
                f".env.example must be keys only: {line.split('=')[0]}"
            )


@pytest.fixture
def secret_env(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:AAsecret-telegram-token-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-anthropic-secret-123")
    monkeypatch.setenv("GITHUB_TOKEN_CONFIG_PR", "gh-fine-grained-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_logs_are_redacted(secret_env, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    log = logging.getLogger("t")
    caplog.handler.addFilter(logsafe.RedactFilter())
    with caplog.at_level(logging.INFO):
        log.info(
            "calling https://api.telegram.org/bot%s/sendMessage with %s",
            "123456789:AAsecret-telegram-token-value",
            "sk-test-anthropic-secret-123",
        )
    assert "secret" not in caplog.text and caplog.text.count("***") == 2


def test_job_errors_are_redacted(secret_env, engine: Engine) -> None:  # type: ignore[no-untyped-def]
    def boom() -> int:
        raise RuntimeError("token gh-fine-grained-secret rejected")

    run_job("x", boom, engine=engine, retries=0)
    with session_scope(engine) as s:
        err = s.scalars(select(JobRun)).one().error
    assert err == "RuntimeError: token *** rejected"
