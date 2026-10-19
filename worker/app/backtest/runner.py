"""Walk-forward runs: train 12 months, test the next 3, roll forward (config/backtest.yaml).

Rules mode: for each test fold, analogue statistics are rebuilt from event reactions whose whole
outcome window ended before the fold starts; events whose reaction day falls inside the fold
become signals in date order. Replay mode: stored live AI signals, only those made after
AI_MODEL_KNOWLEDGE_CUTOFF (R1); no training needed.
"""

import logging
from dataclasses import dataclass, field
from datetime import date

import duckdb
from sqlalchemy import Engine, select

from app.backtest.data import HistoryData
from app.backtest.engine import Signal, Skip, Trade, Variant, plan_entry, rules_signal, simulate
from app.db.models import Analysis
from app.db.models import Signal as LiveSignal
from app.db.session import session_scope
from app.history.event_study import (
    grouped_values,
    kg_reaction_rows,
    reaction_day,
    reaction_rows,
    regime_labels,
    summarize,
    vix_series,
)
from app.history.integrity import ai_predictions_allowed, knowledge_cutoff
from app.timeutil import as_ist
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


OVERLAP_REASON = "open trade on same stock"


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, 28))


@dataclass(frozen=True)
class Fold:
    train_start: date
    test_start: date
    test_end: date


def folds(start: date, end: date) -> list[Fold]:
    cfg = load_yaml("backtest.yaml")["walk_forward"]
    out, ts = [], add_months(start, int(cfg["train_months"]))
    while ts < end:
        te = min(add_months(ts, int(cfg["test_months"])), end)
        out.append(Fold(add_months(ts, -int(cfg["train_months"])), ts, te))
        ts = te
    return out


@dataclass
class RunResult:
    variant: Variant
    start: date
    end: date
    trades: list[Trade] = field(default_factory=list)
    skips: list[Skip] = field(default_factory=list)
    folds: list[Fold] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    counterfactuals: list[Trade] = field(default_factory=list)  # skipped: open trade on same stock


def _execute(sigs: list[Signal], data: HistoryData, variant: Variant, res: RunResult) -> None:
    rules = variant.rules()
    one = bool(load_yaml("backtest.yaml")["entry"]["one_trade_per_symbol"])
    busy: dict[str, date] = {t.signal.symbol: t.exit_day or date.max for t in res.trades}
    for sig in sorted(sigs, key=lambda s: (s.day, s.key)):
        overlap = one and sig.symbol in busy and sig.day <= busy[sig.symbol]
        if overlap:
            res.skips.append(Skip(sig.key, sig.symbol, sig.day, "overlap", OVERLAP_REASON))
        plan = plan_entry(sig, data, variant, rules)
        if isinstance(plan, Skip):
            if not overlap:
                res.skips.append(plan)
            continue
        trade = simulate(plan, data, variant, rules)
        if overlap:  # counterfactual: what the skipped signal would have done (not in P&L)
            res.counterfactuals.append(trade)
            continue
        res.trades.append(trade)
        busy[sig.symbol] = trade.exit_day or date.max


def run_rules(
    con: duckdb.DuckDBPyConnection,
    variant: Variant,
    start: date,
    end: date,
    data: HistoryData | None = None,
) -> RunResult:
    data = data or HistoryData(con)
    res = RunResult(variant, start, end, folds=folds(start, end))
    vix = vix_series(con)
    labels = regime_labels(con) if variant.regime_aware else None
    for f in res.folds:
        groups = grouped_values(  # nothing from the fold on
            reaction_rows(con, before=f.test_start) + kg_reaction_rows(con, before=f.test_start),
            vix,
            labels,
        )
        stats = summarize(groups)
        sigs: list[Signal] = []
        evs = data.events(f.test_start, f.test_end)
        if variant.kg_depth:  # graph-propagated events as of each date (no look-ahead)
            evs = evs + data.linked_events(evs, variant.kg_depth)
        for ev in evs:
            out = rules_signal(ev, stats, data, variant, groups)
            (res.skips.append(out) if isinstance(out, Skip) else sigs.append(out))
        _execute(sigs, data, variant, res)
    if not res.folds:
        res.notes.append("period shorter than the training window: no test fold")
    return res


def replay_signals(
    engine: Engine | None, data: HistoryData, start: date, end: date
) -> tuple[list[Signal], list[str]]:
    """Stored live signals with a priced contract; R1: only those after the AI knowledge cutoff."""
    sigs, notes, refused = [], [], 0
    with session_scope(engine) as s:
        rows = s.execute(
            select(LiveSignal, Analysis).join(Analysis, Analysis.id == LiveSignal.analysis_id)
        ).all()
        for sig, an in rows:
            if not (
                sig.strike
                and sig.option_type
                and sig.expiry
                and sig.direction in ("bullish", "bearish")
            ):
                continue
            created = as_ist(sig.created_at)
            if not ai_predictions_allowed(created.date()):
                refused += 1
                continue
            i, timing = reaction_day(data.calendar, created.date(), created.time())
            if i is None or not (start <= data.calendar[i] < end):
                continue
            opt = (an.scores_json or {}).get("options") or {}
            raw = sig.predicted_move_raw_pct or sig.predicted_move_pct or 0.0
            w = (sig.predicted_move_pct / raw) if raw and sig.predicted_move_pct else 1.0
            sigs.append(
                Signal(
                    f"live:{sig.id}",
                    sig.symbol,
                    sig.sector,
                    None,
                    sig.direction,
                    data.calendar[i],
                    timing,
                    float(raw),
                    int(an.window_days or 3),
                    float(w),
                    None,
                    an.prediction_source or "single",
                    None,
                    (sig.expiry, float(sig.strike), sig.option_type),
                    opt.get("pre_news_spot"),
                    an.thesis[:120],
                )
            )
    if refused:
        cutoff = knowledge_cutoff()
        notes.append(
            f"R1: {refused} live signal(s) on or before the AI knowledge cutoff ({cutoff or 'not set'}) refused"
        )
    return sigs, notes


def run_replay(
    con: duckdb.DuckDBPyConnection,
    engine: Engine | None,
    variant: Variant,
    start: date,
    end: date,
    data: HistoryData | None = None,
) -> RunResult:
    data = data or HistoryData(con)
    res = RunResult(variant, start, end)
    sigs, res.notes = replay_signals(engine, data, start, end)
    _execute(sigs, data, variant, res)
    return res


def run(
    con: duckdb.DuckDBPyConnection,
    variant: Variant,
    start: date,
    end: date,
    engine: Engine | None = None,
    data: HistoryData | None = None,
) -> RunResult:
    if variant.mode == "replay":
        return run_replay(con, engine, variant, start, end, data)
    return run_rules(con, variant, start, end, data)
