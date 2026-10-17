"""Weekly review digest (DESIGN_V2 R3), Sunday on Telegram and stored for the dashboard.

Deterministic (no AI cost): last week's trades (with and without unfillable fills), total
gap/slippage, prediction scorecard and calibration by source (panel / single / combined, with
the number of independent news events), taken vs rejected outcomes, post-mortem causes, AI
spend, and SUGGESTIONS ONLY: nothing here changes configuration.
"""

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.calibration import service as calibration
from app.config import IST, get_settings
from app.db.models import (
    AiUsage,
    Calibrator,
    Digest,
    PaperTrade,
    PostMortem,
    RegulatoryAction,
    RejectedCandidate,
    Signal,
)
from app.db.session import session_scope
from app.notify.outbox import enqueue_alert
from app.timeutil import as_ist
from app.tracking.scorecard import summary as scorecard_summary


def _trades(s: Session, start: date, end: date) -> list[tuple[PaperTrade, str | None]]:
    rows = s.execute(
        select(PaperTrade, Signal.prediction_source)
        .join(Signal, Signal.id == PaperTrade.signal_id)
        .where(PaperTrade.status == "CLOSED")
    ).all()
    return [(t, src) for t, src in rows if t.exit_ts and start <= as_ist(t.exit_ts).date() < end]


def _trade_stats(trades: list[PaperTrade]) -> dict[str, Any]:
    if not trades:
        return {"n": 0}
    pnl = [t.pnl or 0.0 for t in trades]
    return {
        "n": len(trades),
        "win_rate": round(sum(p > 0 for p in pnl) / len(pnl), 3),
        "pnl_inr": round(sum(pnl), 0),
        "avg_pct": round(sum(t.pnl_pct or 0.0 for t in trades) / len(trades), 2),
    }


def build(s: Session, week_start: date) -> tuple[str, dict[str, Any]]:
    end = week_start + timedelta(days=7)
    rows = _trades(s, week_start, end)
    trades = [t for t, _ in rows]
    data: dict[str, Any] = {
        "week": [week_start.isoformat(), (end - timedelta(days=1)).isoformat()],
        "trades": {
            "all": _trade_stats(trades),
            "excluding_unfillable": _trade_stats(
                [t for t in trades if t.fillability != "unfillable"]
            ),
            "panel": _trade_stats([t for t, src in rows if src == "panel"]),
            "single": _trade_stats([t for t, src in rows if src == "single"]),
        },
        "slippage_inr": round(sum(t.slippage_inr or 0.0 for t in trades), 0),
        "gap_trades": sum(1 for t in trades if (t.slippage_inr or 0) < 0),
        "scorecard": scorecard_summary(s, week_start),
        "scorecard_all_time": scorecard_summary(s),
        "calibration": {t: calibration.report(s, t) for t in calibration.TARGETS},
        "calibrators": [
            {
                "target": c.target,
                "source": c.source,
                "method": c.method,
                "version": c.version,
                "records": c.n_samples,
                "events": c.n_events,
                "brier": c.brier,
                "brier_raw": (c.params_json or {}).get("brier_raw"),
            }
            for c in s.scalars(select(Calibrator).where(Calibrator.active.is_(True)))
        ],
    }
    rej = [
        r
        for r in s.scalars(
            select(RejectedCandidate).where(RejectedCandidate.outcome_status == "resolved")
        )
        if r.resolved_at and week_start <= as_ist(r.resolved_at).date() < end
    ]
    by_stage: dict[str, list[float]] = {}
    for r in rej:
        if r.option_return_pct is not None:
            by_stage.setdefault(r.stage, []).append(r.option_return_pct)
    data["rejected"] = {
        k: {
            "n": len(v),
            "avg_option_pct": round(sum(v) / len(v), 1),
            "would_win": sum(x > 0 for x in v),
        }
        for k, v in sorted(by_stage.items())
    }
    pms = [
        p for p in s.scalars(select(PostMortem)) if week_start <= as_ist(p.created_at).date() < end
    ]
    data["post_mortem_causes"] = dict(Counter(p.primary_cause for p in pms).most_common())
    start_dt = datetime.combine(week_start, datetime.min.time(), IST)
    data["ai_spend_inr"] = round(
        float(
            s.scalar(
                select(func.coalesce(func.sum(AiUsage.cost_inr), 0)).where(
                    AiUsage.ts >= start_dt, AiUsage.ts < start_dt + timedelta(days=7)
                )
            )
            or 0
        ),
        0,
    )
    data["regulator_review"] = [
        {
            "regulator": r.regulator,
            "title": r.title[:140],
            "confidence": r.match_confidence,
            "guesses": [f"{c['symbol']} {c['score']:.2f}" for c in (r.match_candidates or [])],
            "url": r.url,
        }
        for r in s.scalars(
            select(RegulatoryAction).where(
                RegulatoryAction.symbol.is_(None),
                RegulatoryAction.disclosed_at >= start_dt,
                RegulatoryAction.disclosed_at < start_dt + timedelta(days=7),
            )
        )
        if (r.match_confidence or 0) > 0  # items naming no stock at all are not worth a look
    ]
    data["suggestions"] = suggestions(data)
    return render(data), data


def suggestions(d: dict[str, Any]) -> list[str]:
    """Rule-based observations for the owner to consider. Never applied automatically."""
    out: list[str] = []
    tr = d["trades"]
    if d["scorecard"].get("cutoff") is None:
        out.append(
            "Set AI_MODEL_KNOWLEDGE_CUTOFF: until then no prediction counts for evaluation or calibration (R1)."
        )
    if (
        tr["all"].get("n", 0)
        and d["slippage_inr"] < 0
        and abs(d["slippage_inr"]) > 0.25 * abs(tr["all"].get("pnl_inr") or 1)
    ):
        out.append(
            "Gap/slippage is over a quarter of P&L: consider a shorter OPTION_POLL_ACTIVE_MIN or wider stops."
        )
    for stage, v in d["rejected"].items():
        if v["n"] >= 5 and v["would_win"] / v["n"] > 0.6 and v["avg_option_pct"] > 10:
            out.append(
                f"Candidates rejected at '{stage}' would mostly have won ({v['would_win']}/{v['n']}, avg {v['avg_option_pct']:+.0f}%): review that gate."
            )
    p, sgl = d["scorecard_all_time"].get("panel", {}), d["scorecard_all_time"].get("single", {})
    if p.get("n", 0) >= 20 and sgl.get("n", 0) >= 20 and sgl["hit_rate"] >= p["hit_rate"]:
        out.append(
            "The single analyst is hitting direction as often as the panel: the panel's extra cost may not be paying off."
        )
    causes = d["post_mortem_causes"]
    if causes:
        top, n = next(iter(causes.items()))
        if n >= 3 and top not in ("thesis_right",):
            out.append(f"Most common post-mortem cause this week: {top} ({n} trades).")
    for c in d["calibrators"]:
        if (
            c.get("brier_raw") is not None
            and c["brier"] is not None
            and c["brier"] > c["brier_raw"]
        ):
            out.append(f"Calibrator {c['target']}/{c['source']} is not improving Brier: check it.")
    return out or ["No suggestions this week."]


def _fmt(st: dict[str, Any]) -> str:
    if not st.get("n"):
        return "none"
    return f"{st['n']} trades, win {st['win_rate']:.0%}, P&L Rs {st['pnl_inr']:+,.0f} (avg {st['avg_pct']:+.1f}% of premium)"


def _cal_line(target: str, rep: dict[str, Any]) -> str:
    parts = []
    for src in ("all", "panel", "single"):
        r = rep.get(src) or {}
        if r.get("records"):
            parts.append(
                f"{src}: {r['records']} records / {r['events']} events, Brier {r['brier_used']:.3f} (raw {r['brier_raw']:.3f})"
            )
        else:
            parts.append(f"{src}: none")
    return f"  {target}: " + "; ".join(parts)


def render(d: dict[str, Any]) -> str:
    tr = d["trades"]
    sc = d["scorecard"]
    lines = [
        f"WEEKLY REVIEW {d['week'][0]} to {d['week'][1]}",
        "",
        "PAPER TRADES",
        f"  All: {_fmt(tr['all'])}",
        f"  Excluding unfillable: {_fmt(tr['excluding_unfillable'])}",
        f"  Panel: {_fmt(tr['panel'])}",
        f"  Single analyst: {_fmt(tr['single'])}",
        f"  Gap/slippage vs trigger levels: Rs {d['slippage_inr']:+,.0f} across {d['gap_trades']} trade(s)",
        "",
        "PREDICTIONS (post-cutoff only, R1)"
        + ("" if sc.get("cutoff") else ": AI_MODEL_KNOWLEDGE_CUTOFF not set, none counted"),
    ]
    for src in ("all", "panel", "single"):
        x = sc.get(src) or {}
        lines.append(
            f"  {src}: "
            + (
                f"{x['n']} scored / {x['events']} events, direction hit {x['hit_rate']:.0%}, "
                f"move error {x['mean_move_error_pct']:+.2f}%"
                if x.get("n")
                else "none"
            )
        )
    lines += ["", "CALIBRATION (records weighted by news event)"]
    lines += [_cal_line(t, d["calibration"][t]) for t in calibration.TARGETS]
    if d["calibrators"]:
        lines += [
            f"  active: {c['target']}/{c['source']} {c['method']} v{c['version']} ({c['events']} events)"
            for c in d["calibrators"]
        ]
    else:
        lines.append(
            "  no calibrator yet: probabilities are NOT YET CALIBRATED (shrunk toward the market)"
        )
    lines += ["", "REJECTED CANDIDATES (counterfactual)"]
    lines += [
        f"  {k}: {v['n']} resolved, {v['would_win']} would have won, avg {v['avg_option_pct']:+.1f}%"
        for k, v in d["rejected"].items()
    ] or ["  none resolved"]
    lines += ["", "REGULATOR ITEMS TO REVIEW (name match below threshold: no evidence created)"]
    lines += [
        f"  {x['regulator'].upper()}: {x['title']} | best {x['confidence']:.2f}: "
        f"{', '.join(x['guesses'])}"
        for x in d.get("regulator_review", [])
    ] or ["  none"]
    lines += ["", "POST-MORTEM CAUSES"]
    lines += [f"  {k}: {v}" for k, v in d["post_mortem_causes"].items()] or ["  none"]
    lines += [
        "",
        f"AI spend: Rs {d['ai_spend_inr']:,.0f} (daily cap Rs {get_settings().ai_daily_budget_inr:,.0f})",
        "",
        "SUGGESTIONS (not applied; change config yourself if you agree)",
    ]
    lines += [f"  - {x}" for x in d["suggestions"]]
    return "\n".join(lines)


def weekly_digest(engine: Engine | None = None, now: datetime | None = None) -> int:
    """Sunday: digest of the week that just ended (Mon-Sun), stored once and sent once."""
    now = now or datetime.now(IST)
    week_start = now.date() - timedelta(days=now.weekday())  # this Monday
    with session_scope(engine) as s:
        if s.scalar(select(Digest.id).where(Digest.week_start == week_start)):
            return 0
        text, data = build(s, week_start)
        s.add(Digest(week_start=week_start, text=text, data_json=data))
        enqueue_alert(s, "digest", text, f"digest:{week_start}")
    return 1
