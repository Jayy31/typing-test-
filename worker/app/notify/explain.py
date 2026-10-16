"""/why drill-down: the full reasoning behind a signal, rebuilt from stored data."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Analysis,
    EventCalendar,
    NewsItem,
    NewsTag,
    Signal,
    SignalVersion,
)
from app.notify.templates import sid
from app.timeutil import as_ist
from app.yaml_config import load_yaml


def _bullets(items: list[str] | None, empty: str = "none recorded") -> str:
    return "\n".join(f"  - {x}" for x in items) if items else f"  - {empty}"


def build_why(s: Session, signal_id: int) -> str | None:
    sig = s.get(Signal, signal_id)
    if sig is None:
        return None
    an = s.get(Analysis, sig.analysis_id) if sig.analysis_id else None
    js = (an.scores_json if an else None) or {}
    weights = load_yaml("signals.yaml")["weights"]
    out = [
        f"WHY #{sid(sig.id)}  {sig.symbol} {sig.direction}  ({sig.status})",
        f"Score {sig.score:.0f}/100 | Expiry {sig.expiry} | Sector {sig.sector}",
    ]
    if an is None:
        return "\n".join(out + ["No analysis stored."])

    out += ["", "WHY IT WAS FILTERED", an.thesis]
    hits = js.get("rule_hits") or []
    out.append(
        "Rules matched:\n"
        + _bullets(
            [f"{h['name']} ({h['kind']}, {h['direction']}): {h['why']}" for h in hits], "none"
        )
    )

    out += ["", "LINKED NEWS"]
    ids = js.get("cited_news_ids") or js.get("news_ids") or []
    rows = s.execute(
        select(NewsItem, NewsTag)
        .join(NewsTag, NewsTag.news_id == NewsItem.id)
        .where(NewsItem.id.in_(ids), NewsTag.symbol == sig.symbol)
    ).all()
    for item, tag in rows:
        when = as_ist(item.published_at or item.fetched_at)
        out.append(
            f"  - [{item.source}, {when:%d-%b %H:%M}] {item.title} (sentiment {tag.sentiment:+.2f})\n    {item.url}"
        )
    if not rows:
        out.append("  - none recorded")

    out += [
        "",
        "SENTIMENT",
        f"  Weighted {js.get('agg_sentiment', 0):+.2f} across {len(js.get('sources', []))} source(s); "
        f"official filing: {js.get('has_official')}",
    ]

    out += [
        "",
        "MACRO AT SIGNAL TIME",
        "  "
        + (
            ", ".join(f"{k} {v:+.2f}" for k, v in (js.get("macro_changes") or {}).items())
            or "no notable moves"
        ),
    ]

    events = s.scalars(
        select(EventCalendar)
        .where(EventCalendar.symbol == sig.symbol)
        .order_by(EventCalendar.event_date)
    ).all()
    out += [
        "",
        "COMPANY EVENTS",
        _bullets(
            [
                f"{e.event_date}: {e.event_type}"
                for e in events
                if e.event_date >= an.created_at.date()
            ][:5],
            "none scheduled",
        ),
    ]

    out += [
        "",
        "OPTION DATA",
        "  Strike, premium, IV, OI and spread arrive with live options (Phase 3).",
        f"  Chosen expiry: {js.get('expiry')}",
    ]

    comps = js.get("components") or {}
    out += ["", "SCORE BREAKDOWN"]
    out += [f"  {k}: {v:.0f} x {weights.get(k, 0):.2f}" for k, v in comps.items()]
    out += [
        f"  AI score {js.get('ai_total')} + rules {js.get('rules_adjustment'):+} - priced-in {js.get('priced_in_penalty')} = {js.get('final')}"
        if comps
        else "  n/a"
    ]

    out += ["", "ELIGIBILITY"]
    out += [
        f"  {'PASS' if c['passed'] else 'FAIL'} {c['name']}: {c['detail']}"
        for c in js.get("eligibility", [])
    ]

    out += [
        "",
        "RISKS",
        _bullets(an.risks),
        "COUNTER-ARGUMENTS",
        _bullets(an.counter_args),
        "WOULD BREAK THE THESIS",
        _bullets(js.get("invalidators")),
    ]

    out += ["", "HISTORICAL ANALOGUES"]
    out += [_bullets([str(a) for a in an.analogues or []], "none in the library yet")]
    if js.get("analogue_notes"):
        out.append(f"  {js['analogue_notes']}")

    out += ["", "TIMELINE"]
    for v in s.scalars(
        select(SignalVersion)
        .where(SignalVersion.signal_id == sig.id)
        .order_by(SignalVersion.version)
    ):
        out.append(
            f"  {as_ist(v.ts):%d-%b %H:%M} v{v.version} {v.status} score {v.score:.0f}: {(v.reason or '')[:90]}"
        )
    return "\n".join(out)
