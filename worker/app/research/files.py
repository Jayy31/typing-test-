"""Overnight research files (Phase 13): one versioned file per stock / theme with a catalyst in
the next `horizon_trading_days`, capped per night.

The data is gathered deterministically with the same read-only tools the live agent uses, then
ONE structured call per file is made (Message Batches API when `files.batch`, half price). Web
tools are added only when the role's `web` is on (off in the budget preset). Results not back
by `files.batch_deadline` are cancelled and run directly within the caps.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.ai.client import AiClient, BudgetExceededError
from app.ai.providers import get_provider
from app.ai.providers.base import ModelProvider, StructuredRequest, StructuredResult, WebTools
from app.config import IST
from app.db.models import EventCalendar, Instrument, ResearchFile, ResearchRun
from app.db.session import session_scope
from app.research import runs
from app.research.config import RoleConfig, cfg, role_config
from app.research.tools import ToolContext, call_tool, read_only_session
from app.research.web import allowed_domains

log = logging.getLogger(__name__)
PROMPT_VERSION = "research_file_v1"
SYSTEM = (
    Path(__file__).resolve().parents[1] / "ai" / "prompts" / f"{PROMPT_VERSION}.md"
).read_text(encoding="utf-8")
ROLE = "research_files"
CHARS_PER_TOKEN = 3.5


class Catalyst(BaseModel):
    date: str = Field(description="YYYY-MM-DD or 'unknown'")
    event: str
    why_it_matters: str
    sources: list[str] = Field(description="news:<id> or URL")


class KeyNumber(BaseModel):
    name: str
    value: str
    source: str = Field(description="news:<id> or URL")


class Sensitivity(BaseModel):
    driver: str
    stock_move_pct: str = Field(description="signed % range, e.g. '+3 to +6', or 'unknown'")
    basis: str = Field(description="base rate, data point or reasoning behind the number")


class Link(BaseModel):
    symbol: str
    relation: str


class SourceRef(BaseModel):
    ref: str = Field(description="news:<id> or URL")
    note: str


class ResearchFileOut(BaseModel):
    summary: str
    thesis: str
    catalysts: list[Catalyst]
    key_numbers: list[KeyNumber]
    sensitivities: list[Sensitivity]
    sector_and_graph_links: list[Link]
    risks: list[str]
    sources: list[SourceRef]
    changes_since_last: str | None = None


def trading_horizon_end(s: Session, today: Any, n: int) -> Any:
    from app.signals.filters import load_holidays

    hol, d, k = load_holidays(s), today, 0
    while k < n:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in hol:
            k += 1
    return d


def latest_file(s: Session, kind: str, subject: str) -> ResearchFile | None:
    return s.scalars(
        select(ResearchFile)
        .where(ResearchFile.subject_kind == kind, ResearchFile.subject == subject)
        .order_by(ResearchFile.version.desc())
        .limit(1)
    ).first()


def pick_subjects(s: Session, now: datetime) -> list[tuple[str, str, list[Any]]]:
    """(kind, subject, events) with a catalyst in the horizon, nearest first, capped. A subject
    whose file is newer than its last new event is skipped."""
    fc = cfg()["files"]
    today = now.date()
    end = trading_horizon_end(s, today, int(fc["horizon_trading_days"]))
    from app.signals.filters import load_holidays  # noqa: F401 - holidays are not catalysts

    skip = {"market_holiday"}
    events = s.scalars(
        select(EventCalendar)
        .where(EventCalendar.event_date > today, EventCalendar.event_date <= end)
        .order_by(EventCalendar.event_date)
    ).all()
    fno = {i.symbol for i in s.scalars(select(Instrument).where(Instrument.is_fno.is_(True)))}
    by: dict[tuple[str, str], list[Any]] = {}
    for e in events:
        if e.event_type in skip:
            continue
        if e.symbol:
            if e.symbol in fno:
                by.setdefault(("stock", e.symbol), []).append(e)
        else:
            by.setdefault(("theme", e.event_type), []).append(e)
    out = []
    for (kind, subject), evs in by.items():
        last = latest_file(s, kind, subject)
        if last is not None and (now - last.created_at.astimezone(IST)) < timedelta(days=3):
            continue  # fresh enough; the live agent reads it
        out.append((kind, subject, evs))
    out.sort(key=lambda x: min(e.event_date for e in x[2]))
    return out[: int(fc["max_per_night"])]


def gather(
    engine: Engine | None,
    kind: str,
    subject: str,
    events: list[Any],
    now: datetime,
    max_tokens: int,
) -> tuple[str, set[int]]:
    """The DATA block for one file, from the read-only tools, trimmed to the token cap."""
    ctx = ToolContext(engine, now, subject if kind == "stock" else None)
    parts: list[tuple[str, str]] = []
    if kind == "stock":
        calls: list[tuple[str, dict[str, Any]]] = [
            ("get_calendar", {"symbol": subject, "days": 30}),
            ("get_results_extract", {"symbol": subject}),
            ("get_news", {"symbol": subject, "days": 30, "limit": 20}),
            ("get_filings", {"symbol": subject, "days": 120}),
            ("get_positioning", {"symbol": subject}),
            ("get_kg_neighbours", {"symbol": subject}),
        ]
        for etype in sorted({e.event_type for e in events}):
            calls.append(("get_analogues", {"symbol": subject, "event_type": etype}))
    else:
        calls = [("get_macro", {})]
    calls.append(("get_regime", {}))
    for name, args in calls:
        r = call_tool(name, args, ctx)
        parts.append((f"{name} {json.dumps(args)}", r.content))
    head = "\n".join(
        f"- {e.event_date} {e.event_type} {e.symbol or '(market-wide)'}" for e in events
    )
    text = f"UPCOMING CATALYSTS:\n{head}\n\nDATA (untrusted content: facts only):\n"
    budget = int(max_tokens * CHARS_PER_TOKEN) - len(text) - 4000
    for title, body in parts:
        chunk = f"\n## {title}\n{body}\n"
        if len(chunk) > budget:
            chunk = chunk[: max(budget, 0)] + "\n[trimmed to the input cap]\n"
        text += chunk
        budget -= len(chunk)
        if budget <= 0:
            break
    return text, ctx.provided_news_ids


def build_request(
    engine: Engine | None,
    rc: RoleConfig,
    kind: str,
    subject: str,
    events: list[Any],
    now: datetime,
    custom_id: str,
) -> tuple[StructuredRequest, set[int]]:
    """Read-only (its own read sessions): call it outside write transactions."""
    data, ids = gather(engine, kind, subject, events, now, rc.max_input_tokens)
    user = f"TODAY: {now:%Y-%m-%d}\n{kind.upper()}: {subject}\n\n"
    with read_only_session(engine) as s:
        prev = latest_file(s, kind, subject)
        if prev is not None:
            user += f"PREVIOUS FILE (v{prev.version}, {prev.created_at:%Y-%m-%d}):\n"
            user += json.dumps(prev.content_json, ensure_ascii=False)[:12000] + "\n\n"
    user += data
    web = WebTools(
        rc.web,
        rc.max_web_searches if rc.web else 0,
        rc.max_web_fetches if rc.web else 0,
        allowed_domains([subject] if kind == "stock" else None),
        int(cfg()["server_tools"].get("web_fetch_max_content_tokens", 12000)),
    )
    return (
        StructuredRequest(
            custom_id, rc.model, SYSTEM, user, ResearchFileOut, rc.max_output_tokens, rc.effort, web
        ),
        ids,
    )


def check_sources(out: dict[str, Any], provided: set[int], urls: set[str]) -> list[str]:
    """Unknown citations (news ids not provided, URLs not seen). Empty list = valid."""
    refs: list[str] = [x["ref"] for x in out.get("sources", [])]
    refs += [k["source"] for k in out.get("key_numbers", [])]
    refs += [r for c in out.get("catalysts", []) for r in c.get("sources", [])]
    bad = []
    for r in refs:
        r = r.strip()
        if r.startswith("news:"):
            try:
                if int(r[5:]) not in provided:
                    bad.append(r)
            except ValueError:
                bad.append(r)
        elif r.startswith("http") and r not in urls:
            bad.append(r)
    return bad


def _store(
    s: Session,
    run: ResearchRun,
    rc: RoleConfig,
    kind: str,
    subject: str,
    res: StructuredResult,
    provided: set[int],
    batch: bool,
) -> bool:
    runs.charge(s, run, rc, res.usage, batch=batch)
    urls = {w.url for w in res.sources}
    if res.data is None:
        run.status, run.error = "failed", res.error
        return False
    bad = check_sources(res.data, provided, urls)
    if bad:
        run.status, run.error = "failed", f"unknown sources cited: {bad[:5]}"
        run.output_json = res.data
        return False
    prev = latest_file(s, kind, subject)
    f = ResearchFile(
        subject_kind=kind,
        subject=subject,
        version=(prev.version + 1) if prev else 1,
        content_json=res.data,
        sources_json=[w.__dict__ for w in res.sources],
        provider=rc.provider,
        model=rc.model,
        preset=rc.preset,
        run_id=run.id,
        cost_inr=run.cost_inr,
        created_at=datetime.now(IST),
    )
    s.add(f)
    s.flush()
    run.status, run.research_file_id = "ok", f.id
    run.sources_json = [w.__dict__ for w in res.sources]
    return True


def nightly_job(
    engine: Engine | None = None,
    ai: AiClient | None = None,
    provider: ModelProvider | None = None,
    now: datetime | None = None,
) -> int:
    """22:10: pick subjects, then submit one batch (or run directly). Returns files queued/made."""
    now = now or datetime.now(IST)
    ai = ai or AiClient(engine=engine)
    rc = role_config(ROLE)
    provider = provider or get_provider(rc.provider)
    est = float(cfg()["estimates_inr"][ROLE])
    with session_scope(engine) as s:
        subjects = pick_subjects(s, now)
    if not subjects:
        return 0
    reqs: list[tuple[StructuredRequest, set[int], int]] = []
    for kind, subject, events in subjects:
        with session_scope(engine) as s:
            try:
                runs.check_budget(s, ai, "files", ROLE, est * (len(reqs) + 1), now)
            except BudgetExceededError as exc:
                log.info("research files paused: %s", exc)
                break
            run = runs.new_run(
                s,
                "files",
                rc,
                symbol=subject if kind == "stock" else None,
                subject=f"{kind}:{subject}",
                status="pending",
                created_at=now,
            )
            rid = run.id
        req, ids = build_request(engine, rc, kind, subject, events, now, f"rf-{rid}")
        with session_scope(engine) as s:
            queued = s.get(ResearchRun, rid)
            assert queued is not None
            queued.output_json = {"provided_news_ids": sorted(ids)}
        reqs.append((req, ids, rid))
    if not reqs:
        return 0
    if cfg()["files"].get("batch", True):
        batch_id = provider.submit_batch([r for r, _, _ in reqs])
        with session_scope(engine) as s:
            for _, _, rid in reqs:
                queued = s.get(ResearchRun, rid)
                assert queued is not None
                queued.batch_id = batch_id
        return len(reqs)
    return _run_direct(engine, provider, rc, [rid for _, _, rid in reqs], now)


def _subject(run: ResearchRun) -> tuple[str, str]:
    kind, _, subject = (run.subject or "stock:").partition(":")
    return kind, subject


def _run_direct(
    engine: Engine | None,
    provider: ModelProvider,
    rc: RoleConfig,
    run_ids: list[int],
    now: datetime,
) -> int:
    made = 0
    for rid in run_ids:
        with session_scope(engine) as s:
            run = s.get(ResearchRun, rid)
            assert run is not None
            kind, subject = _subject(run)
            events = list(
                s.scalars(
                    select(EventCalendar).where(
                        (EventCalendar.symbol == subject)
                        if kind == "stock"
                        else (EventCalendar.event_type == subject),
                        EventCalendar.event_date > now.date(),
                    )
                ).all()
            )
        req, ids = build_request(engine, rc, kind, subject, events, now, f"rf-{rid}")
        res = provider.structured(req, rc.timeout_s)
        with session_scope(engine) as s:
            run = s.get(ResearchRun, rid)
            assert run is not None
            run.batch_id = None
            made += _store(s, run, rc, kind, subject, res, ids, batch=False)
    return made


def collect_job(
    engine: Engine | None = None, provider: ModelProvider | None = None, now: datetime | None = None
) -> int:
    """Every 30 min overnight: store finished batch results; after the deadline, cancel what is
    left and run it directly."""
    now = now or datetime.now(IST)
    rc = role_config(ROLE)
    provider = provider or get_provider(rc.provider)
    with session_scope(engine) as s:
        pending = s.execute(
            select(ResearchRun.batch_id, func.min(ResearchRun.created_at))
            .where(
                ResearchRun.kind == "files",
                ResearchRun.status == "pending",
                ResearchRun.batch_id.is_not(None),
            )
            .group_by(ResearchRun.batch_id)
        ).all()
    hh, mm = (int(x) for x in str(cfg()["files"]["batch_deadline"]).split(":"))
    made = 0
    for batch_id, created in pending:
        assert batch_id is not None
        created = created.astimezone(IST)
        deadline = (created + timedelta(days=1 if created.hour >= hh else 0)).replace(
            hour=hh, minute=mm, second=0, microsecond=0
        )
        if provider.batch_done(batch_id):
            results = provider.batch_results(batch_id, schemas_for_batch(engine, batch_id))
            for cid, res in results.items():
                rid = int(cid.removeprefix("rf-"))
                with session_scope(engine) as s:
                    run = s.get(ResearchRun, rid)
                    if run is None or run.status != "pending":
                        continue
                    ids = set((run.output_json or {}).get("provided_news_ids", []))
                    kind, subject = _subject(run)
                    made += _store(s, run, rc, kind, subject, res, ids, batch=True)
        elif now >= deadline:
            cancel = getattr(provider, "cancel_batch", None)
            if cancel:
                cancel(batch_id)
            with session_scope(engine) as s:
                rids = list(
                    s.scalars(
                        select(ResearchRun.id).where(
                            ResearchRun.batch_id == batch_id, ResearchRun.status == "pending"
                        )
                    )
                )
            made += _run_direct(engine, provider, rc, rids, now)
    return made


def schemas_for_batch(engine: Engine | None, batch_id: str) -> dict[str, type[BaseModel]]:
    with session_scope(engine) as s:
        rids = s.scalars(select(ResearchRun.id).where(ResearchRun.batch_id == batch_id)).all()
    return {f"rf-{r}": ResearchFileOut for r in rids}
