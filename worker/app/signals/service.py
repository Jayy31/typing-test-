"""Signal cycle: candidates -> deep analysis -> score -> eligibility -> lifecycle -> alerts."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app import flags
from app.ai import analyst
from app.ai.client import AiClient, AiOutputError, BudgetExceededError
from app.ai.schemas import AnalystOutput
from app.config import IST, get_settings
from app.db.models import Analysis, Instrument, Signal
from app.db.models import Scenario as ScenarioRow
from app.db.session import session_scope
from app.flags import ENGINE_VERSION
from app.notify import templates
from app.notify.outbox import enqueue_alert
from app.rules.engine import RuleContext, RuleHit, evaluate, fired_macro_rules
from app.rules.macro_changes import compute_changes
from app.signals.candidates import Candidate, build_candidates, evidence_for, weighted_sentiment
from app.signals.expiry import choose_expiry
from app.signals.filters import Check, check_eligibility, load_holidays
from app.signals.lifecycle import LifecycleEvent, apply_analysis, expire_stale
from app.signals.prediction import from_scenarios
from app.signals.scoring import Score, compute_score
from app.timeutil import as_ist
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
    for cand in build_candidates(s, now):
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
        message, ids = analyst.build_user_message(s, cand, hits, changes, now)
        work.append(WorkItem(cand, hits, message, ids, inst.sector, inst.sub_sector))
        if len(work) >= int(cfg["max_analyses_per_cycle"]):
            break
    return work, changes


def _option(direction: str) -> str | None:
    return {"bullish": "CALL", "bearish": "PUT"}.get(direction)


def store_result(
    s: Session,
    item: WorkItem,
    out: AnalystOutput | None,
    error: str | None,
    changes: dict[str, float],
    model: str,
    now: datetime,
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
        s.add(
            Analysis(
                symbol=cand.symbol,
                thesis=f"Analysis failed: {error}",
                direction="neutral",
                scores_json={**base, "failed": True},
                model=model,
                prompt_ver=analyst.PROMPT_VERSION,
                created_at=now,
            )
        )
        return []

    score: Score = compute_score(out, cand, item.hits)
    holidays = load_holidays(s)
    ex = load_yaml("signals.yaml")["expiry"]
    expiry = choose_expiry(now.date(), int(ex["weekday"]), int(ex["min_trading_days"]), holidays)
    checks: list[Check] = check_eligibility(
        s, cand.symbol, out.window_days, expiry, now.date(), holidays
    )
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
        analogues=analyst.load_analogues(cand.event_types, item.sector, 3),
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
            stamp_signal(s, sig, out)
    _enqueue_events(s, events, analysis, cand, out)
    return events


def stamp_signal(s: Session, sig: Signal, out: AnalystOutput) -> None:
    """Configuration provenance + V2 prediction slots (filled further in later phases)."""
    sig.engine_version = ENGINE_VERSION
    sig.flags_json = flags.snapshot(s)
    pred = from_scenarios(out.scenarios, out.direction)
    if pred is not None:
        sig.predicted_move_pct = pred.move_pct
        sig.predicted_move_source = pred.source
        sig.p_move = pred.p_move


def _enqueue_events(
    s: Session,
    events: list[LifecycleEvent],
    analysis: Analysis,
    cand: Candidate,
    out: AnalystOutput,
) -> None:
    high = float(load_yaml("signals.yaml")["high_conviction"])
    for ev in events:
        sig = s.get(Signal, ev.signal_id)
        assert sig is not None
        key = f"signal:{sig.id}:{ev.kind}:{analysis.id}"
        if ev.kind == "new":
            text = templates.new_signal(
                sig, out.thesis, cand.agg_sentiment, len(cand.sources), out.window_days, high
            )
        elif ev.kind == "cancelled":
            text = templates.signal_cancelled(sig, ev.reason)
        else:
            text = templates.signal_update(sig, ev.old_score, ev.kind.upper(), ev.reason)
        enqueue_alert(s, "signal", text, key, sig.id)


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


def run_signal_cycle(engine: Engine | None = None, ai: AiClient | None = None) -> int:
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
    for item in work:
        out, error = None, None
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
        with session_scope(engine) as s:
            store_result(s, item, out, error, changes, model, now)
        done += 1
    return done
