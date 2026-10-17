"""Go-live checks. Quick (2 h after the first start, run it on a trading-day morning before 09:00
so market-hours jobs fall in the window): NSE endpoints FIRST, then database, Telegram, Upstox,
AI smoke test, market-hours jobs, source health, job errors, rows collected. Full (24 h): all of
that plus row counts per table, history backfill progress, Upstox field names, XBRL share, DB size
and the status of every docs/UNVERIFIED.md item. Telegram report + golive_checks row + the
dashboard's "needs review" items."""

import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select, text

from app.config import IST, get_settings
from app.db.config_store import get_config, set_config
from app.db.models import (
    AiSmokeRun,
    Alert,
    Base,
    GoliveCheck,
    JobRun,
    MacroSnapshot,
    NewsItem,
    NewsTag,
    OptionQuote,
    ResultsExtract,
    Signal,
)
from app.db.session import session_scope
from app.golive.probe import ProbeResult, probe_all
from app.notify.outbox import enqueue_alert
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
FIRST_START = "golive.first_start"
DONE = "golive.done"
LEVEL = {"ok": 0, "warn": 1, "fail": 2}


@dataclass
class Check:
    name: str
    status: str  # ok / warn / fail
    summary: str
    details: list[str] = field(default_factory=list)


def worst(checks: list[Check]) -> str:
    return max((c.status for c in checks), key=lambda s: LEVEL[s], default="ok")


# ---- individual checks ---------------------------------------------------------------------
def nse_check(results: list[ProbeResult]) -> Check:
    bad = [r for r in results if r.status in ("blocked", "throttled")]
    odd = [r for r in results if r.status in ("field_mismatch", "error")]
    lines = []
    for r in results:
        extra = f" missing {r.missing}" if r.missing else ""
        lines.append(
            f"{r.name}: {r.status.upper()}{' (' + r.note + ')' if r.note else ''}{extra}"
            + (f" -> alternative: {r.alternative}" if r.status != "ok" else "")
        )
    proxy = (
        "via NSE_HTTP_PROXY" if any(r.via_proxy for r in results) else "direct (no NSE_HTTP_PROXY)"
    )
    if bad:
        return Check(
            "nse_probe",
            "fail",
            f"{len(bad)} of {len(results)} NSE endpoints BLOCKED/THROTTLED from this server "
            f"({proxy}). "
            "Set NSE_HTTP_PROXY or use the listed alternatives.",
            lines,
        )
    if odd:
        return Check(
            "nse_probe",
            "warn",
            f"{len(odd)} NSE endpoint(s) with field mismatches or errors ({proxy})",
            lines,
        )
    return Check("nse_probe", "ok", f"all {len(results)} NSE endpoints reachable ({proxy})", lines)


def db_check(engine: Engine | None) -> Check:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "app/db/migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    try:
        with session_scope(engine) as s:
            cur = s.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception as exc:  # noqa: BLE001
        return Check(
            "database", "fail", f"database not reachable or not migrated: {type(exc).__name__}"
        )
    return Check("database", "ok" if cur == head else "fail", f"migration {cur} (head {head})")


def telegram_check(engine: Engine | None, since: datetime) -> Check:
    st = get_settings()
    if not (st.telegram_bot_token and st.telegram_chat_id):
        return Check("telegram", "fail", "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    with session_scope(engine) as s:
        rows = s.scalars(select(Alert).where(Alert.created_at >= since)).all()
    sent = sum(a.status == "sent" for a in rows)
    failed = [a.error or "" for a in rows if a.status == "failed"]
    if failed:
        return Check("telegram", "fail", f"{len(failed)} alert(s) failed to send", failed[:3])
    return Check("telegram", "ok", f"{sent} alert(s) delivered since start")


def upstox_check(engine: Engine | None, now: datetime, since: datetime) -> Check:
    st = get_settings()
    if not st.broker_access_token:
        return Check("upstox", "fail", "BROKER_ACCESS_TOKEN (Upstox analytics token) not set")
    details = []
    status = "ok"
    if st.broker_token_issued_on:
        left = (
            date.fromisoformat(st.broker_token_issued_on) + timedelta(days=365) - now.date()
        ).days
        details.append(f"token expires in {left} days")
        if left < 14:
            status = "warn"
    else:
        details.append("BROKER_TOKEN_ISSUED_ON not set: no expiry reminder")
        status = "warn"
    with session_scope(engine) as s:
        n = s.scalar(select(func.count(OptionQuote.id)).where(OptionQuote.ts >= since)) or 0
        health = (get_config(s, "source_health", {}) or {}).get("upstox_instruments", {})
    details.append(f"{n} option quotes stored since start")
    if health.get("consecutive_failures", 0) >= 3:
        return Check(
            "upstox", "fail", "instrument master keeps failing", [health.get("last_error", "")]
        )
    return Check("upstox", status, details[0] if details else "configured", details)


def smoke_check(engine: Engine | None) -> Check:
    with session_scope(engine) as s:
        r = s.scalars(select(AiSmokeRun).order_by(AiSmokeRun.id.desc()).limit(1)).first()
        if r is None:
            return Check(
                "ai_smoke", "warn", "AI smoke test has not run yet (it runs ~2 min after start)"
            )
        return Check(
            "ai_smoke",
            "ok" if r.passed else "fail",
            f"{'passed' if r.passed else 'FAILED'}: Rs {r.total_cost_inr:.0f}, "
            f"panel {r.panel_seconds or 0:.0f}s",
        )


def market_jobs_check(engine: Engine | None, since: datetime, now: datetime) -> Check:
    open_t = now.replace(hour=9, minute=20, second=0, microsecond=0)
    if now.weekday() >= 5 or now < open_t or since > open_t + timedelta(hours=6):
        return Check(
            "market_jobs",
            "warn",
            "market was not open during this window: start on a trading-day morning before 09:00",
        )
    with session_scope(engine) as s:
        runs = s.scalars(
            select(JobRun).where(
                JobRun.started_at >= since, JobRun.job.in_(("trading_cycle", "portfolio_snapshot"))
            )
        ).all()
    ok = sum(r.status == "ok" for r in runs)
    return Check(
        "market_jobs",
        "ok" if ok else "fail",
        f"{ok} successful market-hours job run(s) (trading cycle, snapshots)",
    )


def sources_check(engine: Engine | None) -> Check:
    with session_scope(engine) as s:
        health = dict(get_config(s, "source_health", {}) or {})
    bad = {k: v for k, v in health.items() if int(v.get("consecutive_failures", 0)) >= 3}
    never = [k for k, v in health.items() if not v.get("last_ok")]
    lines = [
        f"{k}: {v.get('consecutive_failures')} failures, last error {v.get('last_error', '')[:120]}"
        for k, v in bad.items()
    ]
    lines += [f"{k}: never succeeded" for k in never if k not in bad]
    status = "fail" if bad else "warn" if never else "ok"
    return Check(
        "sources", status, f"{len(health) - len(bad)} of {len(health)} sources healthy", lines
    )


def errors_check(engine: Engine | None, since: datetime) -> Check:
    with session_scope(engine) as s:
        errs = s.scalars(
            select(JobRun).where(JobRun.started_at >= since, JobRun.status.in_(("error", "stuck")))
        ).all()
    by = Counter(e.job for e in errs)
    first: dict[str, str] = {}
    for e in errs:
        first.setdefault(e.job, e.error or "")
    lines = [f"{j}: {n}x, e.g. {first[j][:140]}" for j, n in by.most_common(15)]
    return Check(
        "job_errors",
        "ok" if not errs else "warn",
        f"{len(errs)} failed job run(s) in {len(by)} job(s)",
        lines,
    )


def rows_check(engine: Engine | None, since: datetime) -> Check:
    with session_scope(engine) as s:
        counts = {
            "news_items": s.scalar(
                select(func.count(NewsItem.id)).where(NewsItem.fetched_at >= since)
            )
            or 0,
            "news_tags": s.scalar(select(func.count(NewsTag.id))) or 0,
            "macro_snapshots": s.scalar(
                select(func.count()).select_from(MacroSnapshot).where(MacroSnapshot.ts >= since)
            )
            or 0,
            "option_quotes": s.scalar(
                select(func.count(OptionQuote.id)).where(OptionQuote.ts >= since)
            )
            or 0,
            "signals": s.scalar(select(func.count(Signal.id))) or 0,
        }
        snap = s.scalars(select(MacroSnapshot).order_by(MacroSnapshot.ts.desc()).limit(1)).first()
    empty = [k for k in ("news_items", "macro_snapshots") if not counts[k]]
    details = [f"{k}: {v}" for k, v in counts.items()]
    if snap:
        nulls = [
            k for k in ("brent", "usdinr", "dxy", "us10y", "india_vix") if getattr(snap, k) is None
        ]
        details.append(
            f"macro nulls: {nulls or 'none'}; "
            f"commodities: {sorted((snap.commodities_json or {}).keys())}"
        )
    return Check(
        "rows",
        "fail" if empty else "ok",
        f"collected since start: news {counts['news_items']}, "
        f"macro {counts['macro_snapshots']}, quotes {counts['option_quotes']}",
        details,
    )


def table_counts(engine: Engine | None) -> Check:
    out = {}
    with session_scope(engine) as s:
        for t in sorted(Base.metadata.tables):
            try:
                out[t] = int(s.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() or 0)
            except Exception:  # noqa: BLE001
                out[t] = -1
    return Check(
        "table_counts",
        "ok",
        f"{sum(v for v in out.values() if v > 0)} rows in {len(out)} tables",
        [f"{k}: {v}" for k, v in out.items() if v],
    )


def history_check() -> Check:
    try:
        from app.history.store import connect

        with connect() as con:
            rows = con.execute(
                "SELECT kind, status, count(*) FROM loaded_files GROUP BY 1, 2 ORDER BY 1"
            ).fetchall()
            units = con.execute(
                "SELECT key, value FROM store_meta WHERE key LIKE 'units:%'"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return Check("history", "warn", f"history store not readable yet: {type(exc).__name__}")
    lines = [f"{k} {st}: {n}" for k, st, n in rows] + [f"{k} = {v}" for k, v in units]
    errors = sum(n for _, st, n in rows if st == "error")
    return Check(
        "history",
        "warn" if errors or not rows else "ok",
        f"{sum(n for *_, n in rows)} history files recorded, {errors} errors",
        lines,
    )


def upstox_fields_check() -> Check:
    folder = Path(get_settings().history_data_dir) / "raw_samples" / "upstox"
    want = load_yaml("golive.yaml")["upstox_fields"]
    lines, bad = [], 0
    for key, fields in want.items():
        files = sorted(folder.glob(f"*_{key}.json")) if folder.exists() else []
        if not files:
            lines.append(f"{key}: no sample yet")
            continue
        body = json.loads(files[-1].read_text())
        missing = [f for f in fields if f not in body]
        bad += bool(missing)
        lines.append(f"{key}: {'missing ' + str(missing) if missing else 'ok'} ({files[-1].name})")
    status = "fail" if bad else "warn" if any("no sample" in x for x in lines) else "ok"
    return Check(
        "upstox_fields",
        status,
        f"{len(want) - bad} of {len(want)} Upstox responses have the expected fields",
        lines,
    )


def xbrl_check(engine: Engine | None) -> Check:
    with session_scope(engine) as s:
        rows = s.scalars(select(ResultsExtract)).all()
    if not rows:
        return Check("xbrl", "warn", "no results extracted yet (needs a results day)")
    x = sum(r.method == "xbrl" for r in rows)
    return Check(
        "xbrl", "ok" if x else "warn", f"{x} of {len(rows)} results parsed from XBRL (rest by AI)"
    )


def db_size_check(engine: Engine | None) -> Check:
    from app.health.watchdog import db_size

    try:
        r = db_size(engine)
    except Exception as exc:  # noqa: BLE001
        return Check("db_size", "warn", f"size query failed: {type(exc).__name__}")
    if not r:
        return Check("db_size", "warn", "not Postgres")
    return Check("db_size", "ok", f"{r['total_mb']} MB of {r['limit_mb']} MB")


# ---- runs ----------------------------------------------------------------------------------
def run(
    kind: str, engine: Engine | None = None, now: datetime | None = None, probe: Any = probe_all
) -> tuple[str, list[Check]]:
    now = now or datetime.now(IST)
    with session_scope(engine) as s:
        start = get_config(s, FIRST_START)
    since = (
        datetime.fromisoformat(start)
        if start
        else now - timedelta(hours=2 if kind == "quick" else 24)
    )
    checks = [nse_check(probe(now))]  # NSE first: the most likely blocker on a data-centre IP
    checks += [
        db_check(engine),
        telegram_check(engine, since),
        upstox_check(engine, now, since),
        smoke_check(engine),
        market_jobs_check(engine, since, now),
        sources_check(engine),
        errors_check(engine, since),
        rows_check(engine, since),
    ]
    if kind == "full":
        checks += [
            table_counts(engine),
            history_check(),
            upstox_fields_check(),
            xbrl_check(engine),
            db_size_check(engine),
        ]
        checks.append(unverified_check(checks))
    text = render(kind, checks, now)
    with session_scope(engine) as s:
        s.add(
            GoliveCheck(
                kind=kind,
                status=worst(checks),
                text=text,
                created_at=now,
                results_json={"checks": [asdict(c) for c in checks]},
            )
        )
        review = [
            {"item": f"{c.name}: {c.summary}", "since": now.isoformat()}
            for c in checks
            if c.status == "fail"
        ]
        set_config(s, "unverified_alerts", review)  # dashboard Today -> "Needs review"
        enqueue_alert(s, "health", text, f"golive:{kind}:{now:%Y-%m-%d %H:%M}")
    return worst(checks), checks


def unverified_check(checks: list[Check]) -> Check:
    by = {c.name: c.status for c in checks}
    lines, todo = [], 0
    for item, how in sorted(load_yaml("golive.yaml")["unverified"].items()):
        if "check" in how:
            st = (
                "verified"
                if by.get(how["check"]) == "ok"
                else "FAILED"
                if by.get(how["check"]) == "fail"
                else "pending"
            )
            lines.append(f"#{item}: {st} ({how['check']})")
        else:
            todo += 1
            lines.append(f"#{item}: needs you - {how['manual']}")
    done = sum("verified" in x for x in lines)
    return Check(
        "unverified",
        "warn" if todo or any("FAILED" in x for x in lines) else "ok",
        f"docs/UNVERIFIED.md: {done} verified automatically, {todo} need a manual step",
        lines,
    )


def render(kind: str, checks: list[Check], now: datetime) -> str:
    icon = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
    head = "GO-LIVE QUICK CHECK (2 h)" if kind == "quick" else "FIRST-RUN REPORT (24 h)"
    out = [f"{head}  {now:%d-%b %H:%M}", f"Overall: {worst(checks).upper()}", ""]
    for c in checks:
        out.append(f"[{icon[c.status]}] {c.name}: {c.summary}")
        limit = 20 if c.name in ("nse_probe", "unverified") else 6
        out += [f"    - {d}" for d in c.details[:limit]]
    return "\n".join(out)


def mark_first_start(engine: Engine | None = None, now: datetime | None = None) -> None:
    with session_scope(engine) as s:
        if not get_config(s, FIRST_START):
            set_config(s, FIRST_START, (now or datetime.now(IST)).isoformat())


def due_job(engine: Engine | None = None, now: datetime | None = None) -> int:
    """Every 10 min: quick check 2 h and full report 24 h after the first start, once each."""
    now = now or datetime.now(IST)
    cfg = load_yaml("golive.yaml")["schedule"]
    with session_scope(engine) as s:
        start = get_config(s, FIRST_START)
        done = set(get_config(s, DONE, []) or [])
    if not start:
        return 0
    age = now - datetime.fromisoformat(start)
    for kind, mins in (("quick", cfg["quick_after_minutes"]), ("full", cfg["full_after_minutes"])):
        if kind not in done and age >= timedelta(minutes=int(mins)):
            run(kind, engine, now)
            with session_scope(engine) as s:
                set_config(s, DONE, sorted(done | {kind}))
            return 1
    return 0
