"""Strategy Lab on Telegram (Phase 14): /lab status, the weekly lab summary, the lab section of
the weekly digest and of the monthly report. EOD-incubation and real-fill results are always
reported separately and labelled."""

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.config import IST
from app.db.models import (
    LabBook,
    LabHypothesis,
    LabSpec,
    LabTrial,
    LabVerdict,
    PaperTrade,
    ResearchRun,
)
from app.db.session import session_scope
from app.notify.outbox import enqueue_alert


def _books(s: Session) -> list[dict[str, Any]]:
    out = []
    for b in s.scalars(select(LabBook).order_by(LabBook.id)):
        sp = s.get(LabSpec, b.spec_id)
        eod = (b.stats_json or {}).get("eod_incubation") or {}
        key = "main" if b.book == "news_core" else b.book
        real = [
            float(t.pnl_pct)
            for t in s.scalars(
                select(PaperTrade).where(PaperTrade.book == key, PaperTrade.status == "CLOSED")
            )
            if t.pnl_pct is not None
        ]
        out.append(
            {
                "book": b.book,
                "name": sp.name if sp else b.book,
                "status": b.status,
                "eod_n": int(eod.get("n") or 0),
                "eod_exp": eod.get("expectancy_pct"),
                "real_n": len(real),
                "real_exp": round(sum(real) / len(real), 2) if real else None,
                "alerts": b.alerts_enabled,
                "retire_reason": b.retire_reason,
            }
        )
    return out


def _fmt_book(b: dict[str, Any]) -> str:
    eod = f"EOD incubation {b['eod_n']} trades" + (
        f", {b['eod_exp']:+.1f}%" if b["eod_exp"] is not None else ""
    )
    real = f"real fills {b['real_n']}" + (
        f", {b['real_exp']:+.1f}%" if b["real_exp"] is not None else ""
    )
    extra = f" ({b['retire_reason']})" if b["retire_reason"] else ""
    return f"  {b['name']} [{b['status']}]: {eod}; {real}{extra}"


def lab_spend(s: Session, since: datetime) -> float:
    return float(
        s.scalar(
            select(func.coalesce(func.sum(ResearchRun.cost_inr), 0)).where(
                ResearchRun.kind.like("lab_%"), ResearchRun.created_at >= since
            )
        )
        or 0
    )


def status_text(s: Session, now: datetime | None = None) -> str:
    now = now or datetime.now(IST)
    stages = Counter(s.scalars(select(LabSpec.stage)))
    hyp = Counter(s.scalars(select(LabHypothesis.source)))
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    lines = [
        "STRATEGY LAB (paper research only)",
        "Hypotheses by source: "
        + (", ".join(f"{k} {v}" for k, v in sorted(hyp.items())) or "none"),
        "Specs by stage: " + (", ".join(f"{k} {v}" for k, v in sorted(stages.items())) or "none"),
        f"Lab AI this month: Rs {lab_spend(s, month):,.0f} (cap Rs 600)",
        "",
        "BOOKS (EOD incubation and real fills reported separately)",
    ]
    lines += [_fmt_book(b) for b in _books(s)] or ["  none yet"]
    return "\n".join(lines)


def digest_lines(s: Session, week_start: date) -> list[str]:
    """Weekly digest section: incubating strategies appear only here (no per-trade alerts)."""
    lines = ["", "STRATEGY LAB (incubation strategies; EOD incubation vs real fills)"]
    bs = [b for b in _books(s) if b["status"] in ("incubating", "validated")]
    lines += [_fmt_book(b) for b in bs] or ["  no strategy books yet"]
    return lines


def weekly_summary(engine: Engine | None = None, now: datetime | None = None) -> int:
    now = now or datetime.now(IST)
    since = now - timedelta(days=7)
    with session_scope(engine) as s:
        new = list(s.scalars(select(LabHypothesis).where(LabHypothesis.registered_at >= since)))
        trials = int(
            s.scalar(select(func.count(LabTrial.id)).where(LabTrial.created_at >= since)) or 0
        )
        verdicts = list(s.scalars(select(LabVerdict).where(LabVerdict.created_at >= since)))
        lines = [
            f"STRATEGY LAB WEEK ({since:%d %b} - {now:%d %b})",
            f"New hypotheses: {len(new)}"
            + (" (" + ", ".join(f"{h.name} [{h.source}]" for h in new[:6]) + ")" if new else ""),
            f"Trials run: {trials}",
            "Verdicts: "
            + (
                ", ".join(f"{v.stage} {'pass' if v.passed else 'fail'}" for v in verdicts[:10])
                or "none"
            ),
            f"Lab AI spend this week: Rs {lab_spend(s, since):,.0f}",
            "",
        ]
        lines.append(status_text(s, now))
        text = "\n".join(lines)
        return enqueue_alert(s, "digest", text, f"lab_week:{now:%G-W%V}")


def monthly_data(s: Session, start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "hypotheses": int(
            s.scalar(
                select(func.count(LabHypothesis.id)).where(
                    LabHypothesis.registered_at >= start, LabHypothesis.registered_at < end
                )
            )
            or 0
        ),
        "trials": int(
            s.scalar(
                select(func.count(LabTrial.id)).where(
                    LabTrial.created_at >= start, LabTrial.created_at < end
                )
            )
            or 0
        ),
        "passed_stage0": int(
            s.scalar(
                select(func.count(LabVerdict.id)).where(
                    LabVerdict.stage == "stage0",
                    LabVerdict.passed.is_(True),
                    LabVerdict.created_at >= start,
                    LabVerdict.created_at < end,
                )
            )
            or 0
        ),
        "books": _books(s),
        "ai_spend_inr": round(lab_spend(s, start), 0),
    }


def monthly_lines(d: dict[str, Any]) -> list[str]:
    if not d:
        return []
    out = [
        "",
        "STRATEGY LAB",
        f"  {d['hypotheses']} new hypotheses, {d['trials']} trials, "
        f"{d['passed_stage0']} passed Stage 0; lab AI Rs {d['ai_spend_inr']:,.0f}",
    ]
    out += [_fmt_book(b) for b in d["books"]] or ["  no strategy books yet"]
    return out
