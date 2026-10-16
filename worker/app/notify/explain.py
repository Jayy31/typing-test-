"""/why drill-down: the full reasoning behind a signal, rebuilt from stored data."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AgentRun,
    Analysis,
    EventCalendar,
    NewsItem,
    NewsTag,
    Signal,
    SignalCandidate,
    SignalVersion,
)
from app.db.models import Scenario as ScenarioRow
from app.notify.templates import sid, v2_lines
from app.timeutil import as_ist
from app.yaml_config import load_yaml


def _bullets(items: list[str] | None, empty: str = "none recorded") -> str:
    return "\n".join(f"  - {x}" for x in items) if items else f"  - {empty}"


def analogue_lines(a: dict[str, Any]) -> list[str]:
    """n, effective n and the weight given to analogues: n / (n + ANALOGUE_PRIOR_N), 0 below the minimum."""
    weight = float(a.get("weight", 0.0))
    use = (
        f"weight {weight:.0%} in the prediction"
        if a.get("usable")
        else "too few events: shown only, weight 0%"
    )
    out = [
        f"  n={a['n']} (effective {float(a.get('n_eff', a['n'])):.1f}), {use}",
        f"  avg {a['mean']:+.1f}%, median {a['median']:+.1f}%, hit {a['hit_rate']:.0%}, "
        f"p25..p75 {a['p25']:+.1f}..{a['p75']:+.1f}% ({a['level']}, {a['regime']}, {a['window']}d)",
    ]
    return out + [
        f"  - {e['date']} {e['symbol']} ({e['direction']}): {e['abnormal_pct']:+.1f}% | {e['title']}"
        for e in a.get("examples", [])
    ]


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

    out += ["", "OPTION DATA"]
    opt = js.get("options") or {}
    chosen = s.scalars(
        select(SignalCandidate).where(
            SignalCandidate.signal_id == sig.id, SignalCandidate.chosen.is_(True)
        )
    ).first()
    if chosen is not None:
        out += [
            f"  Chosen: {sig.symbol} {chosen.strike:g} {chosen.option_type} {chosen.expiry}  "
            f"bid {chosen.bid} / ask {chosen.ask} (spread {chosen.spread_pct or 0:.1f}%)",
            f"  IV {chosen.iv or 0:.1f}% (percentile {sig.iv_percentile if sig.iv_percentile is not None else 'n/a, building history'}), "
            f"OI {chosen.oi}, volume {chosen.volume}",
            f"  EV {chosen.ev_pct:+.1f}% (Rs {chosen.ev_inr:,.0f}/lot), P(T1 before SL) {(chosen.p_t1_before_sl or 0) * 100:.0f}%, "
            f"max loss Rs {chosen.max_loss_inr:,.0f}, theta Rs {chosen.theta_per_day:,.0f}/day, "
            f"breakeven move {chosen.breakeven_move_pct}%",
            f"  Why this strike/expiry: best expected value among {len(opt.get('candidates') or [])} "
            "candidates that passed liquidity rules (see candidate table)",
        ]
    else:
        out.append(
            f"  Pricing {sig.pricing_status or 'pending'}: "
            + "; ".join(opt.get("notes") or ["no live option data"])
        )
    out.append(f"  Expiry: {sig.expiry}")
    if opt.get("implied_move_pct") is not None:
        out.append(
            f"  Implied move {opt['implied_move_pct']:.2f}% vs predicted {sig.predicted_move_pct or 0:.2f}% "
            f"-> edge ratio {opt.get('edge_ratio')}"
            + (
                f", event IV premium {opt['event_iv_premium']:+.1f} pts"
                if opt.get("event_iv_premium") is not None
                else ""
            )
        )
    if opt.get("premove_pct") is not None:
        out.append(
            f"  Stock already moved {opt['premove_pct']:+.2f}% since the pre-news level ({opt.get('pre_news_spot')})"
        )
    if sig.news_to_signal_s is not None:
        ages = ", ".join(f"{k} {v:.0f}s" for k, v in (sig.data_age_json or {}).items())
        out.append(
            f"  Latency: news -> signal {sig.news_to_signal_s / 60:.1f} min; data age: {ages}"
        )

    out += ["", "PREDICTION"]
    out += [f"  {line}" for line in v2_lines(sig)] or ["  no prediction yet"]
    if not sig.calibrator_id:
        out.append("  Probabilities are NOT YET CALIBRATED (no calibrator fitted on outcomes yet).")
    scen = s.scalars(select(ScenarioRow).where(ScenarioRow.analysis_id == an.id)).all()
    out += ["", "SCENARIO TREE"]
    out += [
        f"  {r.probability * 100:.0f}%  {r.name}: stock {r.stock_move_pct:+.1f}% in {r.timing_days}d"
        f" | invalidated by: {r.invalidation}"
        for r in scen
    ] or ["  none recorded"]
    runs = s.scalars(select(AgentRun).where(AgentRun.analysis_id == an.id)).all()
    if runs:
        out += ["", "DEBATE (bull / bear / judge)"]
        out += [f"  {r.agent}: {str((r.output_json or {}).get('summary', ''))[:200]}" for r in runs]
    out += [
        "",
        "ENGINE",
        f"  version {sig.engine_version or 'n/a'}; active stages: "
        + (
            ", ".join(k for k, v in (sig.flags_json or {}).items() if v.get("active"))
            or "base pipeline only"
        ),
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

    out += ["", "HISTORICAL ANALOGUES (event study, actual prices)"]
    stats = [a for a in (an.analogues or []) if isinstance(a, dict) and "n" in a]
    out += (
        analogue_lines(stats[0])
        if stats
        else ["  - none yet (history not loaded or no similar events)"]
    )
    if js.get("analogue_notes"):
        out.append(f"  Analyst: {js['analogue_notes']}")

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
