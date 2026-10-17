"""Stage 2 analyst (strong model): full-context deep analysis of one stock's evidence."""

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import AiClient, AiOutputError, CallUsage
from app.ai.schemas import AnalystOutput
from app.db.models import EventCalendar, Instrument, MacroRelease, MacroSnapshot
from app.rules.engine import RuleHit
from app.signals.candidates import Candidate, evidence_for, weighted_sentiment
from app.yaml_config import load_yaml

if TYPE_CHECKING:
    from app.history.analogues import AnalogueResult

log = logging.getLogger(__name__)
PROMPT_VERSION = "analyst_v2"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "analyst_v2.md").read_text(encoding="utf-8")


def analogue_text(analogue: "AnalogueResult | None") -> str:
    """Base rate from the event study (direction-adjusted abnormal returns), or a clear 'none'."""
    if analogue is None:
        return "none available (history not loaded yet)"
    head = (
        f"{analogue.line()}; median {analogue.median:+.1f}%, p25 {analogue.p25:+.1f}%, "
        f"p75 {analogue.p75:+.1f}%, std {analogue.std:.1f}% (positive = moved in the news direction)"
    )
    if not analogue.usable:
        head += " [too few events: indicative only]"
    ex = "\n".join(
        f"  - {e['date']} {e['symbol']} ({e['direction']}): {e['abnormal_pct']:+.1f}% | {e['title']}"
        for e in analogue.examples
    )
    return head + ("\n" + ex if ex else "")


def sector_context(s: Session, sector: str, symbol: str, now: datetime) -> str:
    by_peer: dict[str, list[tuple[float, float]]] = {}
    insts = {i.symbol: i.sector for i in s.scalars(select(Instrument))}
    for sym, sec, item in evidence_for(s, now):
        member = sym if sym and insts.get(sym) == sector else None
        if member is None and sec != sector:
            continue
        by_peer.setdefault(member or f"(sector-level: {sector})", []).append(
            (item.weight, item.sentiment)
        )
    lines = [
        f"- {k}: sentiment {weighted_sentiment(v):+.2f} from {len(v)} item(s)"
        for k, v in sorted(by_peer.items())
        if k != symbol
    ]
    return "\n".join(lines) or "- no other tagged news in this sector"


def macro_text(s: Session, changes: dict[str, float]) -> str:
    snap = s.scalars(select(MacroSnapshot).order_by(MacroSnapshot.ts.desc()).limit(1)).first()
    if snap is None:
        return "- no macro snapshot available"
    vals = {
        k: getattr(snap, k)
        for k in ("brent", "usdinr", "dxy", "us10y", "india_vix", "fii_net", "dii_net")
    }
    line = ", ".join(f"{k}={v}" for k, v in vals.items() if v is not None)
    chg = ", ".join(f"{k} {v:+.2f}" for k, v in changes.items()) or "n/a"
    comm = ", ".join(f"{k}={v:g}" for k, v in (snap.commodities_json or {}).items()) or "n/a"
    since = snap.ts - timedelta(days=45)
    rel = s.scalars(
        select(MacroRelease)
        .where(MacroRelease.released_at >= since)
        .order_by(MacroRelease.released_at.desc())
        .limit(8)
    ).all()
    releases = "; ".join(
        f"{r.name} {r.period:%b-%Y} {r.value:g} (prior {r.prior if r.prior is not None else 'n/a'})"
        for r in rel
        if r.value is not None
    )
    return (
        f"- as of {snap.ts:%Y-%m-%d %H:%M}: {line}\n"
        f"- 24h changes: {chg}\n- ADRs: {snap.adr_json or 'n/a'}\n- commodities: {comm}\n"
        f"- recent releases: {releases or 'none recorded'}"
    )


def calendar_text(s: Session, symbol: str, today: date) -> str:
    rows = s.scalars(
        select(EventCalendar)
        .where(
            EventCalendar.event_date >= today,
            EventCalendar.event_date <= today + timedelta(days=30),
        )
        .where((EventCalendar.symbol == symbol) | (EventCalendar.symbol.is_(None)))
        .order_by(EventCalendar.event_date)
    ).all()
    return (
        "\n".join(f"- {e.event_date}: {e.event_type} {e.symbol or '(market-wide)'}" for e in rows)
        or "- none in the next 30 days"
    )


def build_user_message(
    s: Session,
    cand: Candidate,
    hits: list[RuleHit],
    changes: dict[str, float],
    now: datetime,
    analogue: "AnalogueResult | None" = None,
) -> tuple[str, set[int]]:
    """Assemble the full context. Returns (message, ids of provided news items)."""
    cfg = load_yaml("signals.yaml")["candidates"]
    inst = s.get(Instrument, cand.symbol)
    sector = inst.sector if inst else "unknown"
    items = cand.items[: int(cfg["max_evidence_items"])]
    news = "\n".join(
        f"[id={i.news_id}] {i.source} ({i.kind}), {i.published_at:%Y-%m-%d %H:%M}, "
        f"tagger sentiment {i.sentiment:+.2f}\n"
        f"  {i.title}\n  {(i.summary or '')[:600]}"
        for i in items
    )
    rules = "\n".join(f"- {h.name} ({h.kind}): {h.direction}, {h.why}" for h in hits) or "- none"
    msg = (
        f"Today: {now:%Y-%m-%d %H:%M} IST\n"
        f"Stock: {cand.symbol} ({inst.name if inst else ''}), sector {sector}, "
        f"sub-sector {inst.sub_sector if inst else ''}, "
        f"lot size {inst.lot_size if inst else ''}\n\n"
        f"NEWS EVIDENCE:\n{news}\n\n"
        f"SENTIMENT: weighted {cand.agg_sentiment:+.2f} over {len(cand.items)} item(s), "
        f"{len(cand.sources)} source(s), official filing present: {cand.has_official}\n\n"
        f"SECTOR CONTEXT:\n{sector_context(s, sector, cand.symbol, now)}\n\n"
        f"MACRO:\n{macro_text(s, changes)}\n\n"
        f"UPCOMING EVENTS:\n{calendar_text(s, cand.symbol, now.date())}\n\n"
        f"RULES ENGINE (for your information):\n{rules}\n\n"
        f"HISTORICAL ANALOGUES (event study, actual prices):\n{analogue_text(analogue)}"
    )
    return msg, {i.news_id for i in items}


def validate_output(out: AnalystOutput, provided_ids: set[int]) -> None:
    """Reject unsupported reasoning: every cited id must have been provided."""
    if not out.cited_news_ids:
        raise AiOutputError("analysis cites no news items")
    unknown = set(out.cited_news_ids) - provided_ids
    if unknown:
        raise AiOutputError(f"analysis cites unknown news ids {sorted(unknown)}")
    if not out.thesis.strip():
        raise AiOutputError("empty thesis")


def analyse(ai: AiClient, message: str, provided_ids: set[int], retries: int = 1) -> AnalystOutput:
    """One deep analysis; retries once when the output fails validation."""
    return analyse_with_usage(ai, message, provided_ids, retries)[0]


def analyse_with_usage(
    ai: AiClient, message: str, provided_ids: set[int], retries: int = 1
) -> tuple[AnalystOutput, CallUsage]:
    last: AiOutputError | None = None
    max_tokens = int(
        (load_yaml("ai.yaml").get("agents") or {}).get("analyst", {}).get("max_tokens", 8000)
    )
    for _ in range(retries + 1):
        try:
            out, usage = ai.parse_with_usage(
                "analyst", SYSTEM_PROMPT, message, AnalystOutput, max_tokens
            )
            validate_output(out, provided_ids)
            return out, usage
        except AiOutputError as exc:
            last = exc
            log.warning("analyst output rejected: %s", exc)
    assert last is not None
    raise last
