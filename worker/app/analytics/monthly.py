"""Monthly report (1st of the month): what worked and what didn't, P&L, scorecard and calibration
(B3), post-mortem causes (B4), rejected-candidate counterfactuals, AI vs rules, AI spend, source
uptime, learning-loop suggestions. Rule-based; an optional cheap-model summary paragraph is kept
only if every figure it cites exists in the report data."""

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.ai.client import AiClient, AiOutputError, BudgetExceededError
from app.analytics import ai_vs_rules
from app.analytics.live import summary as live_summary
from app.calibration import service as calibration
from app.config import IST, get_settings
from app.db.models import (
    AiUsage,
    AiVsRules,
    JobRun,
    MonthlyReport,
    PortfolioSnapshot,
    PostMortem,
    RejectedCandidate,
    Suggestion,
)
from app.db.session import session_scope
from app.notify.outbox import enqueue_alert
from app.timeutil import as_ist
from app.tracking.scorecard import summary as scorecard_summary
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
PROMPT_VERSION = "report_summary_v1"
MIN_N = 3  # a trigger type / sector needs at least this many trades to be called best or worst


def month_bounds(month: date) -> tuple[datetime, datetime]:
    start = datetime.combine(month.replace(day=1), time(0), IST)
    nxt = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return start, datetime.combine(nxt, time(0), IST)


def previous_month(today: date) -> date:
    first = today.replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def _ranked(group: dict[str, dict[str, Any]]) -> tuple[list[Any], list[Any]]:
    ok = [(k, v) for k, v in group.items() if v.get("n", 0) >= MIN_N]
    ok.sort(key=lambda kv: kv[1]["expectancy_pct"], reverse=True)
    best = [{"name": k, "n": v["n"], "expectancy_pct": v["expectancy_pct"]} for k, v in ok[:3]]
    worst = [
        {"name": k, "n": v["n"], "expectancy_pct": v["expectancy_pct"]} for k, v in ok[::-1][:3]
    ]
    return best, worst


def build(s: Session, month: date) -> dict[str, Any]:
    start, end = month_bounds(month)
    perf = live_summary(s, start, end)
    snaps = [
        p
        for p in s.scalars(select(PortfolioSnapshot).order_by(PortfolioSnapshot.ts))
        if start <= as_ist(p.ts) < end
    ]
    best_t, worst_t = _ranked(perf["breakdowns"]["event_type"])
    best_s, worst_s = _ranked(perf["breakdowns"]["sector"])
    rej: dict[str, list[float]] = defaultdict(list)
    for r in s.scalars(
        select(RejectedCandidate).where(RejectedCandidate.outcome_status == "resolved")
    ):
        if (
            r.resolved_at
            and start <= as_ist(r.resolved_at) < end
            and r.option_return_pct is not None
        ):
            rej[r.stage].append(r.option_return_pct)
    runs: list[JobRun] = [
        r
        for r in s.scalars(
            select(JobRun).where(JobRun.started_at >= start, JobRun.started_at < end)
        )
    ]
    by_job: dict[str, list[bool]] = defaultdict(list)
    for jr in runs:
        by_job[jr.job].append(jr.status == "ok")
    spend = float(
        s.scalar(
            select(func.coalesce(func.sum(AiUsage.cost_inr), 0)).where(
                AiUsage.ts >= start, AiUsage.ts < end
            )
        )
        or 0
    )
    avr = [r for r in s.scalars(select(AiVsRules)) if start <= as_ist(r.created_at) < end]
    return {
        "month": month.strftime("%Y-%m"),
        "period": [start.date().isoformat(), (end - timedelta(days=1)).date().isoformat()],
        "trades": {"all": perf["all"], "excluding_unfillable": perf["excluding_unfillable"]},
        "risk": perf["risk"],
        "equity": {
            "start": round(snaps[0].equity, 0) if snaps else None,
            "end": round(snaps[-1].equity, 0) if snaps else None,
        },
        "worked": {"trigger_types": best_t, "sectors": best_s},
        "did_not_work": {"trigger_types": worst_t, "sectors": worst_s},
        "breakdowns": perf["breakdowns"],
        "scorecard": scorecard_summary(s, start.date()),
        "calibration": {t: calibration.report(s, t) for t in calibration.TARGETS},
        "post_mortem_causes": dict(
            Counter(
                p.primary_cause
                for p in s.scalars(select(PostMortem))
                if start <= as_ist(p.created_at) < end
            ).most_common()
        ),
        "rejected": {
            k: {
                "n": len(v),
                "would_win": sum(x > 0 for x in v),
                "avg_option_pct": round(sum(v) / len(v), 1),
            }
            for k, v in sorted(rej.items())
        },
        "ai_vs_rules": ai_vs_rules.summarize(avr),
        "ai_spend_inr": round(spend, 0),
        "ai_budget_inr": float(get_settings().ai_monthly_budget_inr),
        "uptime": {j: round(sum(v) / len(v), 3) for j, v in sorted(by_job.items()) if len(v) >= 5},
        "suggestions": [
            {
                "param": g.param,
                "kind": g.kind,
                "current": g.current_value,
                "proposed": g.proposed_value,
                "expected_effect": g.expected_effect,
                "status": g.status,
            }
            for g in s.scalars(select(Suggestion).where(Suggestion.month == end.date()))
        ],
    }


def _pct(x: Any) -> str:
    return "n/a" if x is None else f"{x:+.1f}%"


def render(d: dict[str, Any]) -> str:
    a, f = d["trades"]["all"], d["trades"]["excluding_unfillable"]

    def line(label: str, p: dict[str, Any]) -> str:
        if not p.get("n"):
            return f"  {label}: no closed trades"
        ci = f" (90% interval {p['ci'][0]:+.1f}..{p['ci'][1]:+.1f}%)" if p.get("ci") else ""
        return (
            f"  {label}: {p['n']} trades, win rate {p['win_rate']:.0%}, expectancy "
            f"{p['expectancy_pct']:+.1f}% of premium{ci}, P&L Rs {p['pnl_inr']:+,.0f}"
        )

    def ranked(items: list[dict[str, Any]]) -> str:
        return (
            ", ".join(f"{x['name']} {x['expectancy_pct']:+.1f}% (n={x['n']})" for x in items)
            or f"none with {MIN_N}+ trades"
        )

    r = d["risk"]
    out = [
        f"MONTHLY REPORT {d['month']} (paper trading)",
        "",
        "P&L",
        line("All", a),
        line("Excluding unfillable", f),
        f"  Equity {d['equity']['start'] or 'n/a'} -> {d['equity']['end'] or 'n/a'}; max drawdown "
        f"{r.get('max_drawdown_pct', 'n/a')}%; Sharpe {r.get('sharpe', 'n/a')}",
        "",
        "WHAT WORKED",
        f"  Trigger types: {ranked(d['worked']['trigger_types'])}",
        f"  Sectors: {ranked(d['worked']['sectors'])}",
        "WHAT DID NOT",
        f"  Trigger types: {ranked(d['did_not_work']['trigger_types'])}",
        f"  Sectors: {ranked(d['did_not_work']['sectors'])}",
        "",
        "AI VS RULES",
    ]
    av = d["ai_vs_rules"]
    for k in ("ai", "rules", "ai_only", "rules_only"):
        x = av[k]
        out.append(
            f"  {k}: "
            + (
                f"{x['n']} calls, hit {x['hit_rate']:.0%}, avg move {_pct(x['avg_move_pct'])}"
                if x.get("n")
                else "none resolved"
            )
        )
    out += ["", "POST-MORTEM CAUSES"] + (
        [f"  {k}: {v}" for k, v in d["post_mortem_causes"].items()] or ["  none"]
    )
    out += ["", "REJECTED (counterfactual)"] + (
        [
            f"  {k}: {v['n']} resolved, {v['would_win']} would have won, "
            f"avg {v['avg_option_pct']:+.1f}%"
            for k, v in d["rejected"].items()
        ]
        or ["  none resolved"]
    )
    out += ["", f"AI spend Rs {d['ai_spend_inr']:,.0f} of Rs {d['ai_budget_inr']:,.0f} budget"]
    weak = {k: v for k, v in d["uptime"].items() if v < 0.95}
    out.append(
        "Jobs below 95% success: " + (", ".join(f"{k} {v:.0%}" for k, v in weak.items()) or "none")
    )
    out += ["", "SUGGESTIONS (open the dashboard to accept or reject)"] + (
        [
            f"  {g['kind']} {g['param']}: {g['current']:g} -> {g['proposed']:g} "
            f"(expected {g['expected_effect']:+.1f}% of premium per month)"
            for g in d["suggestions"]
        ]
        or ["  none this month"]
    )
    return "\n".join(out)


# ---- AI summary with figure validation --------------------------------------------------
class SummaryOut(BaseModel):
    summary: str = Field(description="One paragraph")


NUM = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?")


def allowed_numbers(data: Any) -> set[float]:
    """Every number in the report data, plus 0-2 decimal roundings, percentages of fractions and
    the calendar numbers of the period."""
    vals: set[float] = set()

    def walk(x: Any) -> None:
        if isinstance(x, bool):
            return
        if isinstance(x, int | float):
            for v in (float(x), abs(float(x))):
                for dp in (0, 1, 2):
                    vals.add(round(v, dp))
                if abs(v) <= 1:  # rates stored as fractions are written as percentages
                    for dp in (0, 1):
                        vals.add(round(v * 100, dp))
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v)
                walk_str(str(k))
        elif isinstance(x, list | tuple):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            walk_str(x)

    def walk_str(s: str) -> None:
        for m in NUM.findall(s):
            try:
                vals.add(abs(float(m.replace(",", ""))))
            except ValueError:
                pass

    walk(data)
    return vals


def unknown_figures(text: str, data: dict[str, Any]) -> list[str]:
    ok = allowed_numbers(data)
    bad = []
    for m in NUM.findall(text):
        v = abs(float(m.replace(",", "")))
        if not any(abs(v - a) < 1e-9 for a in ok):
            bad.append(m)
    return bad


def ai_summary(ai: AiClient, data: dict[str, Any]) -> tuple[str | None, str]:
    """(summary, status): ok / rejected (cited a figure not in the data, after one retry) /
    failed / off."""
    cfg = load_yaml("ai.yaml")["agents"]["report_writer"]
    if not cfg.get("enabled", True):
        return None, "off"
    system = (
        Path(__file__).resolve().parents[1] / "ai" / "prompts" / f"{PROMPT_VERSION}.md"
    ).read_text(encoding="utf-8")
    system = system.replace("{max_words}", str(cfg["max_words"]))
    compact = {k: v for k, v in data.items() if k != "breakdowns"}
    user = json.dumps(compact, default=str)
    for attempt in range(2):
        try:
            out = ai.parse("report_writer", system, user, SummaryOut, int(cfg["max_tokens"]))
        except (AiOutputError, BudgetExceededError, RuntimeError) as exc:
            log.info("report summary failed: %s", exc)
            return None, "failed"
        bad = unknown_figures(out.summary, compact)
        if not bad:
            return out.summary.strip(), "ok"
        user += (
            f"\n\nYour previous summary cited figures that are not in the data: {', '.join(bad)}. "
            "Use only figures from the data."
        )
        log.info("report summary attempt %d cited unknown figures %s", attempt + 1, bad)
    return None, "rejected"


# ---- PDF ---------------------------------------------------------------------------------
def pdf(text: str, summary: str | None, path: Path) -> Path:
    from fpdf import FPDF

    doc = FPDF(format="A4")
    doc.set_auto_page_break(True, margin=15)
    doc.add_page()
    lines = text.splitlines()
    doc.set_font("Helvetica", "B", 15)
    doc.multi_cell(0, 8, _latin(lines[0]), new_x="LMARGIN", new_y="NEXT")
    doc.ln(2)
    if summary:
        doc.set_font("Helvetica", "I", 10)
        doc.multi_cell(0, 5, _latin(summary), new_x="LMARGIN", new_y="NEXT")
        doc.ln(3)
    for ln in lines[1:]:
        if ln and ln.isupper() and not ln.startswith(" "):
            doc.ln(2)
            doc.set_font("Helvetica", "B", 11)
        else:
            doc.set_font("Helvetica", "", 9)
        doc.multi_cell(0, 5, _latin(ln) or " ", new_x="LMARGIN", new_y="NEXT")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.output(str(path))
    return path


def _latin(s: str) -> str:
    return s.replace("₹", "Rs ").replace("→", "->").encode("latin-1", "replace").decode("latin-1")


def monthly_report_job(
    engine: Engine | None = None, now: datetime | None = None, ai: AiClient | None = None
) -> int:
    """1st of the month: report on the previous month (once)."""
    now = now or datetime.now(IST)
    month = previous_month(now.date())
    with session_scope(engine) as s:
        if s.scalar(select(MonthlyReport.id).where(MonthlyReport.month == month)):
            return 0
        data = build(s, month)
    text = render(data)
    summary, status = ai_summary(ai or AiClient(engine=engine), data)
    path = pdf(
        text,
        summary,
        Path(get_settings().history_data_dir) / "reports" / f"monthly_{data['month']}.pdf",
    )
    with session_scope(engine) as s:
        s.add(
            MonthlyReport(
                month=month,
                data_json=json.loads(json.dumps(data, default=str)),
                text=text,
                ai_summary=summary,
                ai_summary_status=status,
            )
        )
        head = f"MONTHLY REPORT {data['month']}"
        short = "\n".join(text.splitlines()[2:6])
        enqueue_alert(
            s,
            "report",
            f"{head}\n{summary or short}",
            f"report:{data['month']}",
            file_path=str(path),
        )
    return 1
