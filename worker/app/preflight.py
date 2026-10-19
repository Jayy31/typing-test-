"""make preflight / python -m app.preflight: env completeness, migrations at head, Telegram and
Supabase reachable. `--heartbeat <iso time>` exits 0 only if a heartbeat job succeeded after that
time (used by deploy/update.sh to confirm a new image before keeping it)."""

import os
import sys
from datetime import datetime

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db.models import JobRun
from app.db.session import session_scope
from app.golive.checks import db_check
from app.yaml_config import load_yaml


def missing_env(env: dict[str, str] | None = None) -> list[str]:
    env = dict(os.environ) if env is None else env
    return [k for k in load_yaml("golive.yaml")["required_env"] if not env.get(k, "").strip()]


def heartbeat_since(since: datetime) -> bool:
    with session_scope() as s:
        run = s.scalars(
            select(JobRun).where(
                JobRun.job == "heartbeat", JobRun.status == "ok", JobRun.started_at >= since
            )
        ).first()
    return run is not None


def main(argv: list[str]) -> int:
    if argv[:1] == ["--heartbeat"]:
        return 0 if heartbeat_since(datetime.fromisoformat(argv[1])) else 1
    st = get_settings()
    problems: list[str] = []
    if miss := missing_env():
        problems.append("env values missing: " + ", ".join(miss))
    from app.ai import models as ai_models

    print("AI models (resolved per role):")
    for role, model in ai_models.resolved(st).items():
        print(f"  {role}: {model}")
    problems += ["AI model: " + p for p in ai_models.problems(st)]
    db = db_check(None)
    print(f"database: {db.status} - {db.summary}")
    if db.status != "ok":
        problems.append("database: " + db.summary)
    for name, url in (
        ("telegram", f"https://api.telegram.org/bot{st.telegram_bot_token}/getMe"),
        ("supabase", f"{st.supabase_url.rstrip('/')}/rest/v1/"),
    ):
        try:
            headers = {"apikey": st.supabase_service_role_key} if name == "supabase" else {}
            code = httpx.get(url, headers=headers, timeout=10.0).status_code
        except httpx.HTTPError as exc:
            code, err = 0, type(exc).__name__  # never print the URL: it holds the bot token
        else:
            err = f"HTTP {code}"
        print(f"{name}: {'ok' if code == 200 else err}")
        if code != 200:
            problems.append(f"{name}: {err}")
    for p in problems:
        print("FAIL", p)
    print("preflight OK" if not problems else f"preflight: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
