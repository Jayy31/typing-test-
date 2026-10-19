"""Morning brief (08:45 IST) and evening summary (16:00 IST) on Telegram (Phase 9).

Owner switches: config keys brief.morning_enabled / brief.evening_enabled (editable from the
dashboard Settings page) override MORNING_BRIEF_ENABLED / EVENING_SUMMARY_ENABLED. Both skip
non-trading days, except the morning brief when results arrived since the previous close.
FII/DII: the morning brief shows the previous session's FINAL figures; the evening summary marks
today's as provisional (NSE publishes around 19:00) or pending.
"""

from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app import flags
from app.config import IST, get_settings
from app.db.config_store import get_config
from app.db.models import (
    EventCalendar,
    Instrument,
    KgPath,
    MacroRelease,
    MacroSnapshot,
    PaperTrade,
    RejectedCandidate,
    ResultsExtract,
    Signal,
    SignalVersion,
)
from app.db.session import session_scope
from app.notify.outbox import enqueue_alert
from app.rules.macro_changes import compute_changes
from app.signals.expiry import is_trading_day
from app.signals.filters import load_holidays
from app.signals.lifecycle import LIVE
from app.timeutil import as_ist

TOP_REJECTED = 3


def enabled(s: Session, which: str) -> bool:
    st = get_settings()
    default = st.morning_brief_enabled if which == "morning" else st.evening_summary_enabled
    value = get_config(s, f"brief.{which}_enabled")
    return default if value is None else bool(value)


def _prev_session(d: date, holidays: set[date]) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d, holidays):
        d -= timedelta(days=1)
    return d


def _fii_dii(s: Session, d: date) -> MacroSnapshot | None:
    start = datetime.combine(d, time(0), IST)
    return s.scalars(
        select(MacroSnapshot)
        .where(
            MacroSnapshot.ts >= start,
            MacroSnapshot.ts < start + timedelta(days=1),
            MacroSnapshot.fii_net.is_not(None),
        )
        .order_by(MacroSnapshot.ts.desc())
    ).first()


def _crore(x: float | None) -> str:
    return "n/a" if x is None else f"Rs {x:+,.0f} cr"


def _macro_lines(s: Session, now: datetime) -> list[str]:
    snap = s.scalars(select(MacroSnapshot).order_by(MacroSnapshot.ts.desc()).limit(1)).first()
    if snap is None:
        return ["  no macro snapshot yet"]
    chg = compute_changes(s, now)

    def item(name: str, label: str, v: float | None, unit: str = "") -> str | None:
        if v is None:
            return None
        c = chg.get(name)
        cs = "" if c is None else (f" ({c:+.2f} pp)" if name == "us10y" else f" ({c:+.1f}%)")
        return f"{label} {v:,.2f}{unit}{cs}"

    parts = [
        item("brent", "Brent", snap.brent),
        item("usdinr", "USD/INR", snap.usdinr),
        item("dxy", "DXY", snap.dxy),
        item("us10y", "US10Y", snap.us10y, "%"),
        item("india_vix", "VIX", snap.india_vix),
    ]
    lines = ["  " + ", ".join(p for p in parts if p)]
    comm = [item(k, k, v) for k, v in (snap.commodities_json or {}).items()]
    if comm:
        lines.append("  Commodities: " + ", ".join(c for c in comm if c))
    adrs = snap.adr_json or {}
    if adrs:
        lines.append(
            "  ADRs: " + ", ".join(f"{k} {v['change_pct']:+.1f}%" for k, v in adrs.items())
        )
    lines.append(f"  (as of {as_ist(snap.ts):%d-%b %H:%M})")
    return lines


def _rejected(s: Session, since: datetime) -> list[RejectedCandidate]:
    """B8: the strongest rejected candidates (pre-AI score, else evidence strength)."""
    rows = [r for r in s.scalars(select(RejectedCandidate).where(RejectedCandidate.ts >= since))]

    def strength(r: RejectedCandidate) -> float:
        return float(
            r.score if r.score is not None else (r.details_json or {}).get("strength", 0) * 100
        )

    return sorted(rows, key=strength, reverse=True)[:TOP_REJECTED]


def morning_brief_text(s: Session, now: datetime) -> str | None:
    holidays = load_holidays(s)
    today = now.date()
    prev = _prev_session(today, holidays)
    prev_close = datetime.combine(prev, time(15, 30), IST)
    overnight = s.scalars(
        select(ResultsExtract)
        .where(ResultsExtract.disclosed_at >= prev_close)
        .order_by(ResultsExtract.disclosed_at)
    ).all()
    if not is_trading_day(today, holidays) and not overnight:
        return None
    fno = {i.symbol for i in s.scalars(select(Instrument)) if i.is_fno}
    lines = [f"MORNING BRIEF  {today:%a %d-%b-%Y}", "", "GLOBAL CUES", *_macro_lines(s, now)]
    if flags.active(s, "feat.regime"):
        from app.regime.service import current_in

        reg = current_in(s, today)
        lines += ["", "MARKET REGIME", f"  {reg.text() if reg else 'not computed yet'}"]
    flows = _fii_dii(s, prev)
    lines += [
        "",
        f"FII/DII ({prev:%d-%b}, final)",
        f"  FII {_crore(flows.fii_net)}, DII {_crore(flows.dii_net)}"
        if flows
        else "  not published for the previous session",
    ]
    events = s.scalars(
        select(EventCalendar)
        .where(EventCalendar.event_date == today)
        .order_by(EventCalendar.event_type)
    ).all()
    results = [e.symbol for e in events if e.event_type == "results" and e.symbol in fno]
    other = [
        f"{e.event_type}{' ' + e.symbol if e.symbol else ''}"
        for e in events
        if e.event_type != "results"
    ]
    lines += [
        "",
        "TODAY",
        f"  Results: {', '.join(results) if results else 'none in F&O'}",
        f"  Events: {', '.join(other) if other else 'none'}",
    ]
    since = now - timedelta(days=45)
    rel = s.scalars(
        select(MacroRelease)
        .where(MacroRelease.released_at >= since)
        .order_by(MacroRelease.released_at.desc())
        .limit(3)
    ).all()
    if rel:
        lines.append(
            "  Latest data: "
            + "; ".join(f"{r.name} {r.value:g}" for r in rel if r.value is not None)
        )
    if overnight:
        lines += ["", "RESULTS SINCE THE LAST CLOSE"]
        lines += [
            f"  {r.symbol}: {r.subtype or 'n/a'} (surprise {r.surprise_score or 0:+.2f})"
            for r in overnight
        ]
    live = s.scalars(
        select(Signal).where(Signal.status.in_(LIVE)).order_by(Signal.score.desc())
    ).all()
    lines += ["", f"LIVE SIGNALS ({len(live)})"]
    for g in live[:8]:
        when = " - enters after the open (made after hours)" if g.enter_after else ""
        lines.append(f"  S-{g.id} {g.symbol} {g.direction} score {g.score:.0f}{when}")
    if not live:
        lines.append("  none")
    lines += ["", "TOP REJECTED (last 24 h)"]
    rej = _rejected(s, now - timedelta(hours=24))
    lines += [
        f"  {r.symbol} ({r.direction or '-'}): {r.stage} - {r.reason[:110]}" for r in rej
    ] or ["  none"]
    return "\n".join(lines)


def evening_summary_text(s: Session, now: datetime) -> str | None:
    holidays = load_holidays(s)
    today = now.date()
    if not is_trading_day(today, holidays):
        return None
    start = datetime.combine(today, time(0), IST)
    opened = s.scalars(
        select(PaperTrade).where(PaperTrade.status == "OPEN", PaperTrade.book == "main")
    ).all()
    closed = [
        t
        for t in s.scalars(
            select(PaperTrade).where(PaperTrade.status == "CLOSED", PaperTrade.book == "main")
        )
        if t.exit_ts and as_ist(t.exit_ts) >= start
    ]
    lines = [f"EVENING SUMMARY  {today:%a %d-%b-%Y}", "", f"OPEN TRADES ({len(opened)})"]
    unreal = 0.0
    for t in opened:
        left = 1 - sum(f.get("fraction", 0) for f in (t.state_json or {}).get("fills", []))
        u = (
            ((t.last_premium or t.entry_premium) - t.entry_premium)
            * (t.lot_size or 0)
            * t.lots
            * left
        )
        unreal += u
        pct = ((t.last_premium or t.entry_premium) / t.entry_premium - 1) * 100
        lines.append(f"  {t.symbol} {t.strike:g}{t.option_type}: {pct:+.1f}% (Rs {u:+,.0f})")
    if not opened:
        lines.append("  none")
    realised = sum(t.pnl or 0 for t in closed)
    lines += [
        "",
        "TODAY'S P&L",
        f"  Realised Rs {realised:+,.0f} ({len(closed)} closed), open Rs {unreal:+,.0f}",
    ]
    raised = s.scalars(
        select(Signal).where(Signal.created_at >= start, Signal.book == "main")
    ).all()
    cancelled = s.scalars(
        select(SignalVersion).where(SignalVersion.ts >= start, SignalVersion.status == "CANCELLED")
    ).all()
    lines += [
        "",
        "SIGNALS",
        f"  Raised: {', '.join(f'S-{g.id} {g.symbol}' for g in raised) or 'none'}",
        f"  Cancelled: {', '.join(f'S-{v.signal_id}' for v in cancelled) or 'none'}",
    ]
    lines += ["", "TOP REJECTED TODAY (counterfactual so far)"]
    for r in _rejected(s, start):
        cf = "open" if r.option_return_pct is None else f"{r.option_return_pct:+.1f}%"
        lines.append(f"  {r.symbol}: {r.stage} - {r.reason[:90]} | option {cf}")
    if lines[-1].startswith("TOP REJECTED"):
        lines.append("  none")
    paths = s.scalars(
        select(KgPath).where(KgPath.created_at >= start, KgPath.role == "propagated")
    ).all()
    lines += ["", f"GRAPH PROPAGATIONS ({len(paths)})"]
    lines += [f"  {p.path_key.replace('>', ' > ')} ({p.sign_basis})" for p in paths[:5]] or [
        "  none"
    ]
    if flags.active(s, "feat.watchlist_book"):
        lines += ["", *_watchlist_lines(s, start)]
    flows = _fii_dii(s, today)
    lines += [
        "",
        "FII/DII (today)",
        f"  provisional: FII {_crore(flows.fii_net)}, DII {_crore(flows.dii_net)}"
        if flows
        else "  pending (NSE publishes around 19:00; final figures in tomorrow's brief)",
    ]
    return "\n".join(lines)


def _watchlist_lines(s: Session, start: datetime) -> list[str]:
    """Phase 12: near-miss book, kept apart from the main book (no alert per trade)."""
    opened = s.scalars(
        select(PaperTrade).where(PaperTrade.book == "watchlist", PaperTrade.entry_ts >= start)
    ).all()
    closed = [
        t
        for t in s.scalars(
            select(PaperTrade).where(PaperTrade.book == "watchlist", PaperTrade.status == "CLOSED")
        )
        if t.exit_ts and as_ist(t.exit_ts) >= start
    ]
    still = s.scalars(
        select(PaperTrade).where(PaperTrade.book == "watchlist", PaperTrade.status == "OPEN")
    ).all()
    out = ["WATCHLIST BOOK (near-misses, separate from the main book)"]
    out.append(
        f"  Opened today {len(opened)}, closed today {len(closed)} "
        f"(Rs {sum(t.pnl or 0 for t in closed):+,.0f}), open {len(still)}"
    )
    out += [f"  {t.symbol} {t.strike:g}{t.option_type}: {t.pnl_pct or 0:+.1f}%" for t in closed[:5]]
    return out


def _send(engine: Engine | None, which: str, build: Any, now: datetime | None) -> int:
    now = now or datetime.now(IST)
    with session_scope(engine) as s:
        if not enabled(s, which):
            return 0
        text = build(s, now)
        if text is None:
            return 0
        return int(enqueue_alert(s, "brief", text, f"brief:{which}:{now.date()}"))


def morning_brief_job(engine: Engine | None = None, now: datetime | None = None) -> int:
    return _send(engine, "morning", morning_brief_text, now)


def evening_summary_job(engine: Engine | None = None, now: datetime | None = None) -> int:
    return _send(engine, "evening", evening_summary_text, now)
