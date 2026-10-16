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
from app.ai.schemas import AnalystOutput, Scenario
from app.config import IST, get_settings
from app.db.models import Analysis, Instrument, MacroSnapshot, Signal, SignalCandidate
from app.db.models import Scenario as ScenarioRow
from app.db.session import session_scope
from app.flags import ENGINE_VERSION
from app.history.analogues import AnalogueResult, lookup
from app.market.live import UpstoxOptionData
from app.notify import templates
from app.notify.outbox import enqueue_alert
from app.rules.engine import RuleContext, RuleHit, evaluate, fired_macro_rules
from app.rules.macro_changes import compute_changes
from app.signals.candidates import Candidate, build_candidates, evidence_for, weighted_sentiment
from app.signals.expiry import choose_expiry
from app.signals.filters import Check, check_eligibility, load_holidays
from app.signals.lifecycle import LifecycleEvent, apply_analysis, expire_stale, live_signal
from app.signals.options_stage import OptionPlan, candidate_rows_json, plan_options
from app.signals.prediction import Prediction, combine, from_scenarios, shrink
from app.signals.rejections import record_rejection
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


def predict(item: WorkItem, out: AnalystOutput) -> tuple[Prediction | None, list[Scenario]]:
    """Predicted move in our direction (+ the distribution the simulator samples):
    scenario tree alone, or blended with historical analogues once there are enough."""
    weight = float(load_yaml("history.yaml")["blend"]["analogue_weight"])
    agree = out.direction == item.cand.direction  # analyst agrees with the news' direction
    return combine(out.scenarios, out.direction, item.analogue, agree, weight)


def _last_analysis(s: Session, symbol: str) -> Analysis | None:
    return s.scalars(
        select(Analysis).where(Analysis.symbol == symbol).order_by(Analysis.id.desc())
    ).first()


def plan_work(s: Session, now: datetime) -> tuple[list[WorkItem], dict[str, float]]:
    """Pick candidates that are new/changed since their last analysis and past the cooldown."""
    cfg = load_yaml("signals.yaml")["candidates"]
    changes = compute_changes(s, now)
    macro = fired_macro_rules(changes)
    insts = {i.symbol: i for i in s.scalars(select(Instrument))}
    work: list[WorkItem] = []
    weak: list[tuple[Candidate, str]] = []
    accepted = build_candidates(s, now, weak)
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
        if flags.active(s, "feat.event_study"):
            etypes = sorted(cand.event_types) or ["other"]
            analogue = lookup(etypes, inst.sector, cand.symbol, cand.direction, now.date())
        message, ids = analyst.build_user_message(s, cand, hits, changes, now, analogue)
        work.append(WorkItem(cand, hits, message, ids, inst.sector, inst.sub_sector, analogue))
        if len(work) >= int(cfg["max_analyses_per_cycle"]):
            break
    return work, changes


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
    if pred is None or source is None or not source.available:
        return None
    with session_scope(engine) as s:
        if not flags.active(s, "feat.simulator"):
            return None
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
    if out is None:
        failed = Analysis(
            symbol=cand.symbol,
            thesis=f"Analysis failed: {error}",
            direction="neutral",
            scores_json={**base, "failed": True},
            model=model,
            prompt_ver=analyst.PROMPT_VERSION,
            created_at=now,
        )
        s.add(failed)
        s.flush()
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
        },
        confidence=out.confidence,
        model=model,
        prompt_ver=analyst.PROMPT_VERSION,
        created_at=now,
    )
    s.add(analysis)
    s.flush()
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
    if not any(ev.kind == "new" for ev in events) and live_signal(s, cand.symbol) is None:
        _record_not_signalled(s, item, out, analysis, score, checks, plan, now)
    _enqueue_events(s, events, analysis, cand, out, item.analogue)
    return events


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
    if pred is None or plan is None or plan.spot is None:
        return
    shrunk = shrink(pred, float(get_settings().scenario_shrink_weight))
    new = next((ev.signal_id for ev in events if ev.kind == "new"), None)
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
    )


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
        predicted_move_pct=(
            shrink(pred, float(get_settings().scenario_shrink_weight)).move_pct if pred else None
        ),
        window_days=out.window_days,
        ref_expiry=best.row.expiry if best else None,
        ref_strike=best.row.strike if best else None,
        ref_option_type=best.row.option_type if best else None,
        ref_premium=best.row.ask if best else None,
        details_json={
            "predicted_move_raw_pct": pred.move_pct if pred else None,
            "edge_ratio": plan.edge_ratio if plan else None,
            "ev_pct": best.result.ev_pct if best and best.result else None,
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
    sig.flags_json = flags.snapshot(s)
    w = float(get_settings().scenario_shrink_weight)
    pred = (
        predict(item, out)[0] if item is not None else from_scenarios(out.scenarios, out.direction)
    )
    if pred is not None:
        shrunk = shrink(pred, w)
        sig.shrink_weight = w
        sig.predicted_move_raw_pct = pred.move_pct
        sig.predicted_move_pct = shrunk.move_pct  # gating/ranking value until calibration
        sig.predicted_move_source = pred.source
        sig.p_move = shrunk.p_move
    if item is not None:
        sig.theme_key = theme_key(item)
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
    if plan is None:
        sig.pricing_status = "pending"
        return
    sig.pricing_status = "priced" if plan.chosen else plan.status
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
    _store_candidates(s, sig, plan, now or datetime.now(IST))


def analogue_json(a: AnalogueResult) -> dict[str, Any]:
    return {
        "level": a.level,
        "regime": a.regime,
        "window": a.window,
        "n": a.n,
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


def run_signal_cycle(
    engine: Engine | None = None, ai: AiClient | None = None, source: UpstoxOptionData | None = None
) -> int:
    """Job entrypoint. Returns the number of analyses run."""
    now = datetime.now(IST)
    with session_scope(engine) as s:
        work, changes = plan_work(s, now)
        sector_watch(s, now)
        expire_stale(s, now)
    if not work:
        return 0
    ai = ai or AiClient(engine=engine)
    model = ai.model_for("analyst")
    done = 0
    source = source or UpstoxOptionData(engine)
    for item in work:
        out, error = None, None
        plan: OptionPlan | None = None
        try:
            out = analyst.analyse(ai, item.message, item.provided_ids)
        except BudgetExceededError as exc:
            log.warning("AI budget reached; deep analysis paused")
            with session_scope(engine) as s:
                enqueue_alert(
                    s,
                    "health",
                    f"SYSTEM PROBLEM\nAI budget reached ({exc}). Deep analysis is paused.",
                    f"budget:{now.date()}",
                )
            break
        except AiOutputError as exc:
            error = str(exc)
        if out is not None:
            plan = price_analysis(item, out, now, engine, source)
        with session_scope(engine) as s:
            store_result(s, item, out, error, changes, model, now, plan)
        done += 1
    return done
