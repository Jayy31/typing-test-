from datetime import datetime, timedelta

import httpx

from app.collectors import nse_http
from app.config import IST, get_settings
from app.db.config_store import get_config
from app.db.models import GoliveCheck
from app.db.session import session_scope
from app.golive import checks
from app.golive.probe import ProbeResult, classify, missing_fields, probe_all


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_classify_block_throttle_and_html_challenge() -> None:
    def r(code: int, body: str = "{}", ct: str = "application/json") -> httpx.Response:
        return httpx.Response(code, text=body, headers={"content-type": ct})

    assert classify(r(403), 10, 8000, None)[0] == "blocked"
    assert classify(r(429), 10, 8000, None)[0] == "throttled"
    assert classify(r(200), 9000, 8000, None)[0] == "throttled"
    page = "<html>Access Denied. Reference #18.abc</html>"
    assert classify(r(200, page, "text/html"), 10, 8000, None)[0] == "blocked"
    assert classify(None, 10, 8000, httpx.ReadTimeout("t"))[0] == "throttled"
    assert classify(r(200), 10, 8000, None) == ("ok", "")


def test_missing_fields_dotted_and_empty_list() -> None:
    assert missing_fields({"data": [{"a": {"b": 1}}]}, ["a.b", "c"]) == ["c"]
    assert missing_fields([], ["x"]) == []


def test_probe_all_reports_every_endpoint_blocked() -> None:
    res = probe_all(
        datetime(2026, 10, 19, 8, 0, tzinfo=IST), _client(lambda req: httpx.Response(403))
    )
    assert res and all(r.status == "blocked" and r.alternative for r in res)
    check = checks.nse_check(res)
    assert (
        check.status == "fail" and "BLOCKED" in check.summary and "alternative" in check.details[0]
    )


def test_nse_proxy_only_for_nse(monkeypatch) -> None:
    monkeypatch.setenv("NSE_HTTP_PROXY", "http://proxy.local:3128")
    get_settings.cache_clear()
    assert nse_http.proxy() == "http://proxy.local:3128"
    assert nse_http.is_nse("https://nsearchives.nseindia.com/x") and nse_http.is_nse(
        "https://www.nseindia.com"
    )
    assert not nse_http.is_nse("https://api.upstox.com/v2") and not nse_http.is_nse(
        "https://evilnseindia.com"
    )


def test_quick_and_full_run_once_from_first_start(engine) -> None:
    ok = [ProbeResult("announcements", "news", "ok")]
    start = datetime(2026, 10, 19, 8, 30, tzinfo=IST)
    checks.mark_first_start(engine, start)
    checks.mark_first_start(engine, start + timedelta(hours=1))  # restart: start time is kept
    orig = checks.run
    calls = []

    def run(kind, eng=None, now=None, probe=None):
        calls.append(kind)
        return orig(kind, eng, now, probe=lambda now: ok)

    checks.run = run  # type: ignore[assignment]
    try:
        assert checks.due_job(engine, start + timedelta(minutes=60)) == 0
        assert checks.due_job(engine, start + timedelta(minutes=125)) == 1
        assert checks.due_job(engine, start + timedelta(minutes=135)) == 0
        assert checks.due_job(engine, start + timedelta(hours=25)) == 1
        assert checks.due_job(engine, start + timedelta(hours=26)) == 0
    finally:
        checks.run = orig  # type: ignore[assignment]
    assert calls == ["quick", "full"]
    with session_scope(engine) as s:
        rows = s.query(GoliveCheck).order_by(GoliveCheck.id).all()
        assert [r.kind for r in rows] == ["quick", "full"]
        assert "FIRST-RUN REPORT" in rows[1].text and "unverified" in rows[1].text
        assert rows[0].text.index("nse_probe") < rows[0].text.index("database")  # NSE first
        assert isinstance(get_config(s, "unverified_alerts"), list)


def test_preflight_lists_missing_env() -> None:
    from app.preflight import missing_env

    miss = missing_env({"DATABASE_URL": "postgresql://x", "TELEGRAM_BOT_TOKEN": "  "})
    assert (
        "DATABASE_URL" not in miss and "TELEGRAM_BOT_TOKEN" in miss and "ANTHROPIC_API_KEY" in miss
    )
