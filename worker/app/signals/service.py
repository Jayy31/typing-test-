"""Signal cycle: candidates -> deep analysis -> score -> eligibility -> lifecycle -> alerts."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from app import flags
from app.ai import analyst
from app.ai.client import AiClient, AiOutputError, BudgetExceededError
from app.ai.panel import PANEL_PROMPT_VERSION, AgentStep, run_panel
from app.ai.schemas import AnalystOutput, JudgeOutput, Scenario
from app.analytics import ai_vs_rules
from app.calibration import service as calibration
from app.config import IST, get_settings
from app.db.models import (
    AgentRun,
    Analysis,
    CalibrationRecord,
    Instrument,
    MacroSnapshot,
    Signal,
    SignalCandidate,
)
from app.db.models import Scenario as ScenarioRow
from app.db.session import session_scope
from app.flags import ENGINE_VERSION
from app.history.analogues import AnalogueResult, lookup
from app.kg.propagate import propagate
from app.market.live import UpstoxOptionData
from app.notify import templates
from app.notify.outbox import enqueue_alert
from app.positioning.evidence import tilt as positioning_tilt
from app.positioning.service import evidence_from as positioning_evidence
from app.positioning.service import for_signal as positioning_for
from app.positioning.service import source_label as positioning_label
from app.regime.service import current_in as current_regime_in
from app.rules.engine import RuleContext, RuleHit, evaluate, fired_macro_rules
from app.rules.macro_changes import compute_changes
from app.signals import deferred
from app.signals.candidates import Candidate, build_candidates, evidence_for, weighted_sentiment
from app.signals.expiry import choose_expiry
from app.signals.filters import Check, check_eligibility, load_holidays
from app.signals.lifecycle import LifecycleEvent, apply_analysis, expire_stale, live_signal
from app.signals.options_stage import OptionPlan, candidate_rows_json, plan_options
from app.signals.prediction import Prediction, combine, from_scenarios, shrink
from app.signals.rejections import record_rejection
from app.signals.routing import choose_mode, prescreen
from app.signals.scoring import Score, compute_score
from app.timeutil import as_ist
from app.tracking.scorecard import create_score
from app.trading.exits import ExitRules
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


@dataclass
class WorkItem:
    cand: Candidate
    hits: list[RuleHit]
    message: str
    provided_ids: set[int]
    sector: str
    sub_sector: str | None
    analogue: AnalogueResult | None = None  # event-study base rate (Phase 4)
    prescreen: float = 0.0  # pre-AI rank for the daily panel / analyst caps (Phase 5)
    source: str = "single"  # agent / panel / single
    steps: list[AgentStep] | None = None  # agent outputs, stored in agent_runs
    research: Any = None  # Phase 13: AgentResult of the research agent (also kept on fallback)
    policy: calibration.Policy | None = None  # how the prediction is tempered (shrink/calibrated)
    deferred: dict[str, Any] | None = None  # off-hours: priced at the next open (Phase 7)
    positioning: dict[str, Any] | None = None  # Phase 12: option-market positioning evidence


def predict(item: WorkItem, out: AnalystOutput) -> tuple[Prediction | None, list[Scenario]]:
    """Predicted move in our direction (+ the distribution the simulator samples):
    scenario tree alone, or blended with historical analogues once there are enough."""
    agree = out.direction == item.cand.direction  # analyst agrees with the news' direction
    pred, dist = combine(out.scenarios, out.direction, item.analogue, agree)
    ev = positioning_evidence(item.positioning)
    if pred is None or not any(e.basis == "base_rate" and e.weight > 0 for e in ev):
        return pred, dist
    window = max(1, int(item.analogue.window if item.analogue else out.window_days or 3))
    mixed = positioning_tilt(dist, ev, window)  # learned base rates only; support-only states never
    tilted = from_scenarios(mixed, out.direction)
    assert tilted is not None
    return Prediction(tilted.move_pct, f"{pred.source}+positioning", tilted.p_move), mixed


def _last_analysis(s: Session, symbol: str) -> Analysis | None:
    return s.scalars(
        select(Analysis).where(Analysis.symbol == symbol).order_by(Analysis.id.desc())
    ).first()


def plan_work(
    s: Session, now: datetime, only: set[str] | None = None
) -> tuple[list[WorkItem], dict[str, float]]:
    """Pick candidates that are new/changed since their last analysis and past the cooldown.
    `only`: restrict to these stocks (results fast-path: an immediate cycle for one stock)."""
    cfg = load_yaml("signals.yaml")["candidates"]
    changes = compute_changes(s, now)
    macro = fired_macro_rules(changes)
    insts = {i.symbol: i for i in s.scalars(select(Instrument))}
    regime_now = current_regime_in(s, now.date())  # Phase 12 market regime (feat.regime)
    work: list[WorkItem] = []
    weak: list[tuple[Candidate, str]] = []
    accepted = build_candidates(s, now, weak)
    if only is not None:
        accepted = [c for c in accepted if c.symbol in only]
        weak = [(c, r) for c, r in weak if c.symbol in only]
    for cand, reason in weak:
        if cand.symbol in insts and insts[cand.symbol].is_fno:
            record_rejection(
                s,
                dedupe_key=f"candidate:{cand.symbol}:{cand.max_tag_id}",
                ts=now,
                symbol=cand.symbol,
                stage="candidate",
                reason=reason,
                direction=cand.direction,
                details_json={
                    "agg_sentiment": round(cand.agg_sentiment, 3),
                    "strength": round(cand.strength, 3),
                    "sources": sorted(cand.sources),
                },
            )
    for cand in accepted:
        inst = insts.get(cand.symbol)
        if inst is None or not inst.is_fno:
            continue
        last = _last_analysis(s, cand.symbol)
        if last is not None:
            seen = int((last.scores_json or {}).get("max_tag_id", 0))
            if cand.max_tag_id <= seen:
                continue
            if now - as_ist(last.created_at) < timedelta(
                minutes=int(cfg["reanalysis_cooldown_min"])
            ):
                continue
        text = " ".join(f"{i.title}. {i.summary or ''}" for i in cand.items)
        ctx = RuleContext(
            cand.symbol, inst.sector, inst.sub_sector, text, cand.themes, cand.event_types
        )
        hits = evaluate(ctx, macro)
        analogue = None
        reg = regime_now if flags.active(s, "feat.regime") else None
        if flags.active(s, "feat.event_study"):
            etypes = sorted(cand.event_types) or ["other"]
            analogue = lookup(
                etypes,
                inst.sector,
                cand.symbol,
                cand.direction,
                now.date(),
                regime_label=reg.label if reg else None,
            )
        message, ids = analyst.build_user_message(s, cand, hits, changes, now, analogue, reg)
        pos = positioning_for(s, cand.symbol, now) if flags.active(s, "feat.positioning") else None
        if pos:
            lines = "\n".join(f"- {e.line()}" for e in positioning_evidence(pos))
            message += (
                f"\n\nOPTION-MARKET POSITIONING (data only; {positioning_label(pos['source'], pos['as_of'])}):\n"
                f"{lines}"
            )
        work.append(
            WorkItem(
                cand,
                hits,
                message,
                ids,
                inst.sector,
                inst.sub_sector,
                analogue,
                prescreen(cand, hits),
                positioning=pos,
            )
        )
    work.sort(key=lambda w: -w.prescreen)  # strongest candidates get the panel first
    return work[: int(cfg["max_analyses_per_cycle"])], changes


def _option(direction: str) -> str | None:
    return {"bullish": "CALL", "bearish": "PUT"}.get(direction)


def price_analysis(
    item: WorkItem,
    out: AnalystOutput,
    now: datetime,
    engine: Engine | None,
    source: UpstoxOptionData | None,
) -> OptionPlan | None:
    """Options stage (network reads), only for a directional view with live data available."""
    pred, dist = predict(item, out)
    with session_scope(engine) as s:
        item.policy = calibration.policy(s, item.source, pred.p_move if pred else None)
        if pred is None or source is None or not source.available:
            return None
        if not flags.active(s, "feat.simulator"):
            return None
        if deferred.off_hours(now, load_holidays(s)):  # stale quotes: price at the next open
            item.deferred = deferred.deferred_json(item, out, dist, pred.move_pct, pred.p_move)
            return OptionPlan(status="pending", notes=["after hours: priced at the next open"])
        inst = s.get(Instrument, item.cand.symbol)
        lot = int(inst.lot_size or 0) if inst else 0
    news_time = min(i.published_at for i in item.cand.items)
    try:
        return plan_options(
            source,
            item.cand.symbol,
            out.direction,
            dist,
            pred.move_pct,
            out.window_days,
            news_time,
            lot,
            now,
            engine,
            item.policy,
        )
    except Exception as exc:  # noqa: BLE001 - degrade to "pricing pending", never lose the signal
        log.warning("options stage failed for %s: %s", item.cand.symbol, exc)
        return OptionPlan(status="pending", notes=[f"options stage error: {type(exc).__name__}"])


def store_result(
    s: Session,
    item: WorkItem,
    out: AnalystOutput | None,
    error: str | None,
    changes: dict[str, float],
    model: str,
    now: datetime,
    plan: OptionPlan | None = None,
) -> list[LifecycleEvent]:
    """Persist the analysis and run the lifecycle. Failed analyses are stored as a marker."""
    settings = get_settings()
    cand = item.cand
    base: dict[str, Any] = {
        "max_tag_id": cand.max_tag_id,
        "news_ids": sorted(item.provided_ids),
        "agg_sentiment": round(cand.agg_sentiment, 3),
        "sources": sorted(cand.sources),
        "has_official": cand.has_official,
        "macro_changes": changes,
        "rule_hits": [h.__dict__ for h in item.hits],
    }
    if item.deferred is not None:
        base["deferred_pricing"] = item.deferred
    if out is None:
        failed = Analysis(
            symbol=cand.symbol,
            thesis=f"Analysis failed: {error}",
            direction="neutral",
            scores_json={**base, "failed": True},
            model=model,
            prompt_ver=_prompt_ver(item),
            created_at=now,
            prediction_source=item.source,
            impact_order=cand.impact_order,
            **_ai_tags(item),
        )
        s.add(failed)
        s.flush()
        _store_agent_runs(s, failed.id, item)
        record_rejection(
            s,
            dedupe_key=f"analysis:{cand.symbol}:{failed.id}",
            ts=now,
            symbol=cand.symbol,
            stage="analysis",
            reason=f"analysis failed: {error}",
            analysis_id=failed.id,
        )
        return []

    priced = plan is not None and plan.status != "pending"
    score: Score = compute_score(
        out,
        cand,
        item.hits,
        liquidity=plan.liquidity_score if priced and plan else None,
        premove_fraction=plan.premove_fraction if priced and plan else None,
    )
    holidays = load_holidays(s)
    ex = load_yaml("signals.yaml")["expiry"]
    expiry = choose_expiry(now.date(), int(ex["weekday"]), int(ex["min_trading_days"]), holidays)
    if plan and plan.chosen:
        expiry = plan.chosen.row.expiry  # real broker expiry replaces the provisional one
    checks: list[Check] = check_eligibility(
        s, cand.symbol, out.window_days, expiry, now.date(), holidays
    )
    if plan and plan.rejection:
        checks.append(Check(f"options_{plan.rejection[0]}", False, plan.rejection[1]))
    elif plan and plan.chosen:
        checks = [c for c in checks if c.name != "option_liquidity"]
        checks.append(Check("option_liquidity", True, f"liquidity score {plan.liquidity_score}"))
    eligible = all(c.passed for c in checks)
    analysis = Analysis(
        cluster_id=cand.items[0].cluster_id,
        symbol=cand.symbol,
        thesis=out.thesis,
        direction=out.direction,
        move_size=out.expected_move_pct,
        window_days=out.window_days,
        risks=out.risks,
        counter_args=out.counter_arguments,
        analogues=[analogue_json(item.analogue)] if item.analogue else None,
        scores_json={
            **base,
            "components": score.components,
            "ai_total": score.ai_total,
            "rules_adjustment": score.rules_adjustment,
            "priced_in_penalty": score.priced_in_penalty,
            "final": score.final,
            "eligibility": [c.__dict__ for c in checks],
            "expiry": expiry.isoformat(),
            "invalidators": out.invalidators,
            "cited_news_ids": out.cited_news_ids,
            "analogue_notes": out.analogue_notes,
            "options": _plan_json(plan),
            "prediction_source": item.source,
            "calibration": _policy_json(item.policy),
            "panel": _panel_json(out),
            "positioning": item.positioning,
            "regime": getattr(current_regime_in(s, now.date()), "label", None),
            "research": _research_json(item),
        },
        confidence=out.confidence,
        model=model,
        prompt_ver=_prompt_ver(item),
        created_at=now,
        prediction_source=item.source,
        impact_order=cand.impact_order,
        **_ai_tags(item),
    )
    s.add(analysis)
    s.flush()
    _store_agent_runs(s, analysis.id, item)
    for sc in out.scenarios:
        s.add(
            ScenarioRow(
                analysis_id=analysis.id,
                name=sc.name,
                probability=sc.probability,
                stock_move_pct=sc.stock_move_pct,
                timing_days=sc.timing_days,
                triggers=sc.triggers,
                invalidation=sc.invalidation,
            )
        )
    events = apply_analysis(
        s,
        symbol=cand.symbol,
        sector=item.sector,
        option=_option(out.direction),
        score=score.final,
        eligible=eligible,
        threshold=float(settings.signal_score_threshold),
        expiry=expiry,
        analysis_id=analysis.id,
        reason=out.thesis,
        now=now,
    )
    for ev in events:
        sig = s.get(Signal, ev.signal_id)
        assert sig is not None
        if sig.status != "CANCELLED":
            stamp_signal(s, sig, out, item, plan, now)
    _score_prediction(s, analysis, out, plan, events, now, item)
    made = any(ev.kind == "new" for ev in events) or live_signal(s, cand.symbol) is not None
    ai_vs_rules.record(s, analysis, cand.direction, item.analogue, made)  # B3 (Phase 10)
    if not any(ev.kind == "new" for ev in events) and live_signal(s, cand.symbol) is None:
        _record_not_signalled(s, item, out, analysis, score, checks, plan, now)
        if flags.active(s, "feat.watchlist_book"):
            _open_watchlist(s, item, out, analysis, score.final, checks, plan, now)
    _enqueue_events(s, events, analysis, cand, out, item.analogue)
    return events


def _open_watchlist(
    s: Session,
    item: WorkItem,
    out: AnalystOutput,
    analysis: Analysis,
    score: float,
    checks: list[Any],
    plan: OptionPlan | None,
    now: datetime,
) -> Signal | None:
    """Phase 12: a near-miss (one soft gate missed within the margin) enters the watchlist book.
    Same contract the gates saw, same paper rules; no AI call, no alert."""
    from app.regime import gates as regime_gates
    from app.trading import watchlist

    hard_ok = all(
        c.passed
        for c in checks
        if not (
            c.name.startswith("options_") and c.name[len("options_") :] in watchlist.SOFT_STAGES
        )
    )
    reg = current_regime_in(s, now.date()) if flags.active(s, "feat.regime") else None
    gates = regime_gates.for_regime(reg.vol if reg else None)
    miss = watchlist.near_miss(score, hard_ok, plan, gates, now.date(), out.direction)
    if miss is None or plan is None:
        return None
    sig = Signal(
        symbol=item.cand.symbol,
        sector=item.sector,
        direction=_option(out.direction) or out.direction,
        score=score,
        status=watchlist.STATUS,
        book=watchlist.BOOK,
        analysis_id=analysis.id,
        created_at=now,
        updated_at=now,
    )
    s.add(sig)
    s.flush()
    stamp_signal(s, sig, out, item, watchlist.plan_for_book(plan), now)
    sig.flags_json = {
        **(sig.flags_json or {}),
        "watchlist": {"gate": miss.gate, "value": miss.value, "required": miss.required},
    }
    for r in s.scalars(
        select(CalibrationRecord).where(CalibrationRecord.analysis_id == analysis.id)
    ):
        r.signal_id, r.book = sig.id, watchlist.BOOK  # outcome from the watchlist trade's fills
    return sig


def _score_prediction(
    s: Session,
    analysis: Analysis,
    out: AnalystOutput,
    plan: OptionPlan | None,
    events: list[LifecycleEvent],
    now: datetime,
    item: WorkItem,
) -> None:
    """Every directional prediction with a known starting price goes on the scorecard (v2.1 B3)."""
    pred, _ = predict(item, out)
    new = next((ev.signal_id for ev in events if ev.kind == "new"), None)
    _calibration_records(s, analysis, item, pred, plan, new)
    if pred is None or plan is None or plan.spot is None:
        return
    shrunk = shrink(pred, _move_weight(item))
    create_score(
        s,
        analysis,
        out.direction,
        shrunk.move_pct,
        pred.move_pct,
        shrunk.p_move,
        plan.spot,
        now,
        new,
        pred.source,
        item.source,
    )


def _move_weight(item: WorkItem) -> float:
    if item.policy is not None:
        return item.policy.move_weight
    return min(max(float(get_settings().scenario_shrink_weight), 0.0), 1.0)


def _prompt_ver(item: WorkItem) -> str:
    if item.source == "agent":
        from app.research.agent import PROMPT_VERSION as AGENT_PROMPT

        return AGENT_PROMPT
    return PANEL_PROMPT_VERSION if item.source == "panel" else analyst.PROMPT_VERSION


def _ai_tags(item: WorkItem) -> dict[str, Any]:
    """Phase 13 prediction tags: preset (agent only), model per role, total AI cost of this
    analysis (a failed agent run that fell back to the single analyst counts too)."""
    models: dict[str, str] = {}
    cost = 0.0
    for st in item.steps or []:
        if st.usage:
            models[st.agent] = st.usage.model
            cost += st.cost_inr or st.usage.cost_inr
    preset = None
    run_id = None
    if item.research is not None:
        models[item.research.role] = item.research.model
        cost += item.research.cost_inr
        run_id = item.research.run_id
        preset = item.research.preset if item.source == "agent" else None
    return {
        "research_run_id": run_id,
        "ai_preset": preset,
        "ai_models_json": models or None,
        "ai_cost_inr": round(cost, 4),
    }


def _store_agent_runs(s: Session, analysis_id: int, item: WorkItem) -> None:
    for st in item.steps or []:
        s.add(
            AgentRun(
                analysis_id=analysis_id,
                agent=st.agent,
                model=st.usage.model if st.usage else "",
                prompt_ver=st.prompt_ver,
                input_refs={"news_ids": sorted(item.provided_ids), "source": item.source},
                output_json=st.output if st.output is not None else {"error": st.error},
                input_tokens=st.usage.input_tokens if st.usage else 0,
                output_tokens=st.usage.output_tokens if st.usage else 0,
                cost_inr=st.usage.cost_inr if st.usage else 0.0,
            )
        )


def _calibration_records(
    s: Session,
    analysis: Analysis,
    item: WorkItem,
    pred: Prediction | None,
    plan: OptionPlan | None,
    signal_id: int | None,
) -> None:
    """Raw model probabilities for later calibration (directional predictions only)."""
    pol = item.policy
    if pred is not None and pred.p_move is not None:
        used = shrink(pred, _move_weight(item)).p_move
        calibration.add_record(
            s,
            analysis,
            "p_move",
            pred.p_move,
            used,
            item.source,
            pol.move_cal.id if pol and pol.move_cal else None,
            signal_id,
        )
    best = plan.best_reference if plan else None
    if best is not None and best.raw is not None and best.result is not None:
        calibration.add_record(
            s,
            analysis,
            "p_t1_before_sl",
            best.raw.p_t1_before_sl,
            best.result.p_t1_before_sl,
            item.source,
            pol.t1_cal.id if pol and pol.t1_cal else None,
            signal_id,
        )


def _policy_json(p: calibration.Policy | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {
        "method": p.method,
        "move_weight": round(p.move_weight, 4),
        "shrink": p.shrink,
        "move_calibrator": p.move_cal.label() if p.move_cal else None,
        "t1_calibrator": p.t1_cal.label() if p.t1_cal else None,
        "calibrator_id": p.calibrator_id,
    }


def _research_json(item: WorkItem) -> dict[str, Any] | None:
    r = item.research
    if r is None:
        return None
    return {
        "run_id": r.run_id,
        "status": r.status,
        "used": item.source == "agent",
        "fallback": None if item.source == "agent" else f"single analyst ({r.error})",
        "role": r.role,
        "model": r.model,
        "preset": r.preset,
        "cost_inr": round(r.cost_inr, 2),
        "tools_used": r.tools_used,
    }


def _panel_json(out: AnalystOutput) -> dict[str, Any] | None:
    if not isinstance(out, JudgeOutput):
        return None
    return {"winning_side": out.winning_side, "rejected_side_reasons": out.rejected_side_reasons}


def _plan_json(plan: OptionPlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    best = plan.best_reference
    return {
        "status": plan.status,
        "spot": plan.spot,
        "prev_close": plan.prev_close,
        "pre_news_spot": plan.pre_news_spot,
        "premove_pct": plan.premove_pct,
        "premove_fraction": plan.premove_fraction,
        "atm_iv_pct": plan.atm_iv_pct,
        "iv_percentile": plan.iv_percentile,
        "event_iv_premium": plan.event_iv_premium,
        "implied_move_pct": plan.implied_move_pct,
        "ai_weight": plan.ai_weight,
        "predicted_move_raw": plan.predicted_move_raw,
        "predicted_move_shrunk": plan.predicted_move,
        "edge_ratio_raw": plan.edge_ratio_raw,
        "raw_result": plan.raw_result.__dict__ if plan.raw_result else None,
        "edge_ratio": plan.edge_ratio,
        "event_in_window": plan.event_in_window,
        "liquidity_score": plan.liquidity_score,
        "rejection": list(plan.rejection) if plan.rejection else None,
        "notes": plan.notes,
        "best": None
        if best is None
        else {
            "expiry": best.row.expiry.isoformat(),
            "strike": best.row.strike,
            "type": best.row.option_type,
            "bid": best.row.bid,
            "ask": best.row.ask,
            "iv": best.row.iv,
            "oi": best.row.oi,
            "volume": best.row.volume,
            "result": best.result.__dict__ if best.result else None,
        },
        "candidates": candidate_rows_json(plan),
    }


def _record_not_signalled(
    s: Session,
    item: WorkItem,
    out: AnalystOutput,
    analysis: Analysis,
    score: Score,
    checks: list[Check],
    plan: OptionPlan | None,
    now: datetime,
) -> None:
    """The analysis did not produce a signal: store why (v2.1 B1)."""
    failed = [c for c in checks if not c.passed]
    threshold = float(get_settings().signal_score_threshold)
    if out.direction == "neutral":
        stage, reason = "direction", "analyst view is neutral"
    elif plan and plan.rejection:
        stage, reason = plan.rejection
    elif failed:
        stage, reason = "eligibility", "; ".join(f"{c.name}: {c.detail}" for c in failed)
    elif score.final < threshold:
        stage, reason = "score", f"score {score.final:.1f} below {threshold:.0f}"
    else:
        return
    pred, _ = predict(item, out)
    best = plan.best_reference if plan else None
    record_rejection(
        s,
        dedupe_key=f"{stage}:{item.cand.symbol}:{analysis.id}",
        ts=now,
        symbol=item.cand.symbol,
        stage=stage,
        reason=reason,
        direction=out.direction,
        score=score.final,
        analysis_id=analysis.id,
        spot_at_reject=plan.spot if plan else None,
        predicted_move_pct=shrink(pred, _move_weight(item)).move_pct if pred else None,
        window_days=out.window_days,
        ref_expiry=best.row.expiry if best else None,
        ref_strike=best.row.strike if best else None,
        ref_option_type=best.row.option_type if best else None,
        ref_premium=best.row.ask if best else None,
        details_json={
            "predicted_move_raw_pct": pred.move_pct if pred else None,
            "edge_ratio": plan.edge_ratio if plan else None,
            "ev_pct": best.result.ev_pct if best and best.result else None,
            "p_t1_before_sl": best.result.p_t1_before_sl if best and best.result else None,
        },
    )


def stamp_signal(
    s: Session,
    sig: Signal,
    out: AnalystOutput,
    item: WorkItem | None = None,
    plan: OptionPlan | None = None,
    now: datetime | None = None,
) -> None:
    """Configuration provenance, prediction, option choice, EV and latency on a live signal."""
    sig.engine_version = ENGINE_VERSION
    if item is not None and item.source == "agent" and item.research is not None:
        sig.ai_preset = item.research.preset
    snap = flags.snapshot(s)
    if item is not None:  # what actually ran for THIS signal, not just what is switched on
        snap["feat.multi_agent"]["active"] = (
            snap["feat.multi_agent"]["active"] and item.source == "panel"
        )
        snap["feat.research_agent"]["active"] = (
            snap["feat.research_agent"]["active"] and item.source == "agent"
        )
        used_cal = item.policy is not None and item.policy.method != "shrink"
        snap["feat.probabilities"]["active"] = snap["feat.probabilities"]["active"] and used_cal
    sig.flags_json = snap
    w = _move_weight(item) if item is not None else float(get_settings().scenario_shrink_weight)
    pred = (
        predict(item, out)[0] if item is not None else from_scenarios(out.scenarios, out.direction)
    )
    if item is not None:
        sig.prediction_source = item.source
        sig.calibration_method = item.policy.method if item.policy else "shrink"
    if pred is not None:
        shrunk = shrink(pred, w)
        sig.shrink_weight = w
        sig.predicted_move_raw_pct = pred.move_pct
        sig.predicted_move_pct = shrunk.move_pct  # gating/ranking value until calibration
        sig.predicted_move_source = pred.source
        sig.p_move = shrunk.p_move
    if item is not None:
        sig.theme_key = theme_key(item)
        sig.impact_order = item.cand.impact_order
    if item is not None and now is not None:
        first_news = min(i.published_at for i in item.cand.items)
        newest = max(i.published_at for i in item.cand.items)
        sig.news_published_at = first_news
        sig.news_to_signal_s = round((now - first_news).total_seconds(), 1)
        ages: dict[str, Any] = {"news_s": round((now - newest).total_seconds(), 1)}
        if plan and plan.quote_ts:
            ages["quote_s"] = round((now - plan.quote_ts).total_seconds(), 1)
        macro_ts = s.scalar(select(func.max(MacroSnapshot.ts)))
        if macro_ts:
            ages["macro_s"] = round((now - as_ist(macro_ts)).total_seconds(), 1)
        sig.data_age_json = ages
    if item is not None and item.deferred is not None:
        sig.enter_after = deferred.next_open(now or datetime.now(IST), load_holidays(s))
    if plan is None:
        sig.pricing_status = "pending"
        return
    apply_plan(s, sig, plan, now or datetime.now(IST))


def apply_plan(s: Session, sig: Signal, plan: OptionPlan, now: datetime) -> None:
    """Option choice, probabilities and EV from the options stage (at creation or re-priced)."""
    sig.pricing_status = "priced" if plan.chosen else plan.status
    sig.calibrator_id = plan.calibrator_id
    sig.calibration_method = plan.calibration_method
    sig.ai_weight = plan.chosen.ai_weight if plan.chosen else None
    sig.premove_pct = plan.premove_pct
    sig.implied_move_pct = plan.implied_move_pct
    sig.edge_ratio = plan.edge_ratio
    sig.edge_ratio_raw = plan.edge_ratio_raw
    if plan.raw_result is not None:
        sig.expected_value_raw = plan.raw_result.ev_pct
        sig.p_t1_before_sl_raw = plan.raw_result.p_t1_before_sl
    sig.iv_percentile = plan.iv_percentile
    sig.event_iv_premium = plan.event_iv_premium
    if plan.chosen and plan.chosen.result:
        r = plan.chosen.result
        sig.strike = plan.chosen.row.strike
        sig.option_type = plan.chosen.row.option_type
        sig.expiry = plan.chosen.row.expiry
        sig.expected_value, sig.expected_value_inr = r.ev_pct, r.ev_inr
        sig.p_t1_before_sl, sig.p_t2, sig.p_t3 = r.p_t1_before_sl, r.p_t2, r.p_t3
    _store_candidates(s, sig, plan, now)


def analogue_json(a: AnalogueResult) -> dict[str, Any]:
    return {
        "level": a.level,
        "regime": a.regime,
        "window": a.window,
        "n": a.n,
        "n_eff": round(a.n_eff, 2),
        "weight": round(a.weight, 3),
        "mean": round(a.mean, 3),
        "median": round(a.median, 3),
        "hit_rate": round(a.hit_rate, 3),
        "std": round(a.std, 3),
        "p25": round(a.p25, 3),
        "p75": round(a.p75, 3),
        "usable": a.usable,
        "examples": a.examples,
    }


def theme_key(item: WorkItem) -> str:
    """Overlap grouping (v2.1 A5): first configured key that exists: theme, then sector."""
    keys = load_yaml("portfolio.yaml")["theme_keys"]
    for key in keys:
        if key == "theme" and item.cand.themes:
            return f"theme:{sorted(item.cand.themes)[0]}"
        if key == "sector":
            return f"sector:{item.sector}"
    return f"stock:{item.cand.symbol}"


def _store_candidates(s: Session, sig: Signal, plan: OptionPlan, now: datetime) -> None:
    """Full candidate table for the drill-down, replaced on each re-pricing."""
    s.execute(delete(SignalCandidate).where(SignalCandidate.signal_id == sig.id))
    for c in plan.candidates:
        r = c.result
        s.add(
            SignalCandidate(
                signal_id=sig.id,
                ts=now,
                expiry=c.row.expiry,
                strike=c.row.strike,
                option_type=c.row.option_type,
                bid=c.row.bid,
                ask=c.row.ask,
                iv=c.row.iv,
                oi=c.row.oi,
                volume=c.row.volume,
                spread_pct=c.row.spread_pct,
                ev_pct=r.ev_pct if r else None,
                ev_inr=r.ev_inr if r else None,
                p_t1_before_sl=r.p_t1_before_sl if r else None,
                p_t3=r.p_t3 if r else None,
                max_loss_inr=r.max_loss_inr if r else None,
                exp_hold_days=r.exp_hold_days if r else None,
                theta_per_day=r.theta_per_day_inr if r else None,
                breakeven_move_pct=r.breakeven_move_pct if r else None,
                iv_sensitivity=r.iv_sensitivity_pct if r else None,
                chosen=c is plan.chosen,
                rejected_reason=c.rejected_reason,
                details_json={"targets": list(r.targets), "exit_mix": r.exit_mix} if r else None,
            )
        )


def _enqueue_events(
    s: Session,
    events: list[LifecycleEvent],
    analysis: Analysis,
    cand: Candidate,
    out: AnalystOutput,
    analogue: AnalogueResult | None = None,
) -> None:
    high = float(load_yaml("signals.yaml")["high_conviction"])
    for ev in events:
        sig = s.get(Signal, ev.signal_id)
        assert sig is not None
        key = f"signal:{sig.id}:{ev.kind}:{analysis.id}"
        if ev.kind == "new":
            text = templates.new_signal(
                sig,
                out.thesis,
                cand.agg_sentiment,
                len(cand.sources),
                out.window_days,
                high,
                entry_line=entry_line(s, sig),
                analogues_line=analogue.line() if analogue and analogue.usable else None,
            )
        elif ev.kind == "cancelled":
            text = templates.signal_cancelled(sig, ev.reason)
        else:
            text = templates.signal_update(sig, ev.old_score, ev.kind.upper(), ev.reason)
        enqueue_alert(s, "signal", text, key, sig.id)


def entry_line(s: Session, sig: Signal) -> str | None:
    """'Entry premium / SL / T1 T2 T3' from the chosen candidate (None if not priced)."""
    chosen = s.scalars(
        select(SignalCandidate).where(
            SignalCandidate.signal_id == sig.id, SignalCandidate.chosen.is_(True)
        )
    ).first()
    if chosen is None or not chosen.ask:
        return None
    targets = (chosen.details_json or {}).get("targets") or []
    sl = chosen.ask * (1 + ExitRules.from_config().stop_loss_pct / 100)
    t = "  ".join(f"T{i + 1}: {v:.2f}" for i, v in enumerate(targets))
    return f"Entry premium: {chosen.ask:.2f}   SL: {sl:.2f}   {t}+"


def sector_watch(s: Session, now: datetime) -> int:
    """Alert when a sector's decayed sentiment is strongly one-sided (no AI, no options)."""
    cfg = load_yaml("signals.yaml")["sector_watch"]
    insts = {i.symbol: i.sector for i in s.scalars(select(Instrument))}
    per: dict[str, list[tuple[float, float]]] = {}
    syms: dict[str, set[str]] = {}
    for symbol, sector, item in evidence_for(s, now):
        sec = insts.get(symbol) if symbol else sector
        if sec:
            per.setdefault(sec, []).append((item.weight, item.sentiment))
            if symbol:
                syms.setdefault(sec, set()).add(symbol)
    sent = 0
    for sec, rows in per.items():
        agg = weighted_sentiment(rows)
        if len(rows) >= int(cfg["min_items"]) and abs(agg) >= float(cfg["threshold"]):
            direction = "up" if agg > 0 else "down"
            text = templates.sector_watch(
                sec, agg, len(rows), sorted(syms.get(sec, set())), now.date()
            )
            sent += enqueue_alert(s, "sector", text, f"sector:{sec}:{direction}:{now.date()}")
    return sent


def _threads_ok(engine: Engine | None) -> bool:
    """Bull and bear run in parallel threads, except on SQLite (tests share one connection)."""
    eng = engine
    if eng is None:
        from app.db.session import get_engine

        eng = get_engine()
    return eng.dialect.name != "sqlite"


def _run_agent(
    item: WorkItem, ai: AiClient, now: datetime, engine: Engine | None
) -> tuple[AnalystOutput | None, str | None, str]:
    """Phase 13: research agent; on a failed run or a full research sub-cap, the single analyst
    (never the panel). An exceeded OVERALL budget propagates (AI work pauses)."""
    from app.research import runs as research_runs
    from app.research.agent import run_live
    from app.research.config import cfg as research_cfg
    from app.research.config import role_config
    from app.signals.routing import agent_role

    with session_scope(engine) as s:
        role = agent_role(s, now)
        rc = role_config(role)
        est = float(research_cfg()["estimates_inr"].get(role, 0))
        try:
            research_runs.check_budget(s, ai, "live", role, est, now)
            capped = None
        except BudgetExceededError as exc:
            if not exc.role.startswith("research"):
                raise
            capped = str(exc)
    if capped is None:
        res = run_live(
            rc,
            item.cand.symbol,
            item.sector,
            sorted(item.cand.event_types),
            item.message,
            item.provided_ids,
            engine,
            now=now,
        )
        item.research = res
        if res.out is not None:
            item.source = "agent"
            return res.out, None, rc.model
        log.warning("research agent failed for %s: %s", item.cand.symbol, res.error)
    item.source = "single"
    from app.signals.routing import runs_today

    with session_scope(engine) as s:
        single_full = runs_today(s, "single", now) >= ai.settings.ai_single_analyst_max_per_day
    if single_full:
        why = capped or (item.research.error if item.research else "research agent unavailable")
        return None, f"{why}; single analyst cap reached", ai.model_for("analyst")
    try:
        out, usage = analyst.analyse_with_usage(ai, item.message, item.provided_ids)
    except AiOutputError as exc:
        return None, str(exc), ai.model_for("analyst")
    item.steps = [AgentStep("analyst", analyst.PROMPT_VERSION, out.model_dump(mode="json"), usage)]
    return out, None, ai.model_for("analyst")


def run_signal_cycle(
    engine: Engine | None = None,
    ai: AiClient | None = None,
    source: UpstoxOptionData | None = None,
    only: set[str] | None = None,
) -> int:
    """Job entrypoint. Returns the number of analyses run. `only` limits it to some stocks."""
    now = datetime.now(IST)
    with session_scope(engine) as s:
        if flags.active(s, "feat.knowledge_graph"):
            propagate(s, now)  # 2nd/3rd-order evidence along graph edges (Phase 8)
    with session_scope(engine) as s:
        work, changes = plan_work(s, now, only)
        sector_watch(s, now)
        expire_stale(s, now)
    if not work:
        return 0
    ai = ai or AiClient(engine=engine)
    done = 0
    source = source or UpstoxOptionData(engine)
    for item in work:
        with session_scope(engine) as s:
            mode, why = choose_mode(s, ai, now, item.cand.impact_order)
            if mode is None:
                record_rejection(
                    s,
                    dedupe_key=f"ai_cap:{item.cand.symbol}:{item.cand.max_tag_id}",
                    ts=now,
                    symbol=item.cand.symbol,
                    stage="ai_cap",
                    reason=why,
                    direction=item.cand.direction,
                    score=item.prescreen,
                )
                continue
        item.source = mode
        out: AnalystOutput | None = None
        error: str | None = None
        plan: OptionPlan | None = None
        try:
            if mode == "agent":
                out, error, model = _run_agent(item, ai, now, engine)
            elif mode == "panel":
                res = run_panel(
                    ai, item.message, item.provided_ids, None if _threads_ok(engine) else False
                )
                out, error, item.steps = res.out, res.error, res.steps
                model = ai.model_for("judge")
            else:
                model = ai.model_for("analyst")
                try:
                    out, usage = analyst.analyse_with_usage(ai, item.message, item.provided_ids)
                    item.steps = [
                        AgentStep(
                            "analyst", analyst.PROMPT_VERSION, out.model_dump(mode="json"), usage
                        )
                    ]
                except AiOutputError as exc:
                    error = str(exc)
        except BudgetExceededError as exc:
            log.warning("AI budget reached; deep analysis paused (%s)", exc)
            with session_scope(engine) as s:
                enqueue_alert(
                    s, "health", exc.alert_text(), f"budget:{exc.scope}:{exc.role}:{now.date()}"
                )
            break
        if out is not None:
            plan = price_analysis(item, out, now, engine, source)
        with session_scope(engine) as s:
            store_result(s, item, out, error, changes, model, now, plan)
        done += 1
    return done
