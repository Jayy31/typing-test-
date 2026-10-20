"""Trial registry and validation ladder orchestration (Phase 14, no AI).

hypothesis (pre-registered) -> spec versions (max 10 per hypothesis) -> trials (every run, kept)
-> verdicts. Spec stages: stage0 -> holdout_pending -> incubating -> validated, or rejected /
retired. The lab writes only its own lab_* tables.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any

import duckdb
import numpy as np
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.backtest.data import HistoryData
from app.backtest.runner import RunResult
from app.config import IST
from app.db.models import LabHypothesis, LabSpec, LabTrial, LabVerdict
from app.db.session import session_scope
from app.lab import validate as V
from app.lab.backtest import RunOptions, run_spec
from app.lab.spec import StrategySpec, cfg, parse

log = logging.getLogger(__name__)


class RegistryError(RuntimeError):
    pass


def register(
    s: Session,
    name: str,
    source: str,
    rationale: str,
    expected_effect: str,
    expected: dict[str, Any] | None = None,
    origin: dict[str, Any] | None = None,
) -> LabHypothesis:
    """Pre-registration: the rationale and expected effect are written BEFORE any test and are
    never updated afterwards (Postgres trigger lab_preregistration)."""
    if not rationale.strip() or not expected_effect.strip():
        raise RegistryError("a hypothesis needs a rationale and an expected effect before testing")
    h = LabHypothesis(
        name=name,
        source=source,
        rationale=rationale.strip(),
        expected_effect=expected_effect.strip(),
        expected_json=expected,
        ai_memory_risk=source == "ai_proposal",
        status="registered",
        origin_json=origin,
        registered_at=datetime.now(IST),
    )
    s.add(h)
    s.flush()
    return h


def add_spec(
    s: Session,
    h: LabHypothesis,
    spec: StrategySpec,
    parent: int | None = None,
    note: str | None = None,
) -> LabSpec:
    if spec.source != h.source:
        raise RegistryError("spec source must match its hypothesis")
    n = int(s.scalar(select(func.count(LabSpec.id)).where(LabSpec.hypothesis_id == h.id)) or 0)
    if n >= int(cfg()["registry"]["max_variants_per_hypothesis"]):
        raise RegistryError(f"hypothesis {h.name} already has {n} variants (max)")
    hsh = spec.spec_hash()
    if s.scalar(select(LabSpec.id).where(LabSpec.spec_hash == hsh)):
        raise RegistryError("an identical spec was already tested")
    row = LabSpec(
        hypothesis_id=h.id,
        version=n + 1,
        name=spec.name,
        spec_yaml=spec.to_yaml(),
        spec_hash=hsh,
        parent_spec_id=parent,
        change_note=note,
        stage="stage0",
        created_at=datetime.now(IST),
    )
    s.add(row)
    s.flush()
    if h.status == "registered":
        h.status, h.updated_at = "testing", datetime.now(IST)
    return row


def record(
    s: Session,
    spec_id: int,
    kind: str,
    res: RunResult,
    label: str | None = None,
    counts_only: bool = False,
) -> LabTrial:
    st = V.stats(res.trades)
    t = LabTrial(
        spec_id=spec_id,
        kind=kind,
        label=label,
        period_start=res.start,
        period_end=res.end,
        n_trades=st["n"],
        n_events=st["events"],
        created_at=datetime.now(IST),
    )
    if not counts_only:
        t.expectancy_pct, t.ci_lo, t.ci_hi = st["expectancy_pct"], st["ci_lo"], st["ci_hi"]
        t.win_rate, t.sharpe = st["win_rate"], st["sharpe"]
        t.metrics_json = {"skips": len(res.skips), "notes": res.notes}
    s.add(t)
    return t


def trial_population(s: Session) -> tuple[int, float]:
    """Number of Stage-0 trials so far and the variance of their Sharpe ratios."""
    rows = [
        r
        for r in s.scalars(select(LabTrial.sharpe).where(LabTrial.kind == "stage0"))
        if r is not None
    ]
    n = int(s.scalar(select(func.count(LabTrial.id)).where(LabTrial.kind == "stage0")) or 0)
    from app.lab.sources import all_scanned  # data-mining groups scanned count as trials too

    return n + all_scanned(s), float(np.var(rows, ddof=1)) if len(rows) > 1 else 0.0


def _start(data: HistoryData) -> date:
    want = date.fromisoformat(str(cfg()["registry"]["history_start"]))
    return max(want, data.calendar[0]) if data.calendar else want


def screen(
    con: duckdb.DuckDBPyConnection,
    spec_id: int,
    engine: Engine | None = None,
    data: HistoryData | None = None,
) -> LabVerdict:
    """Stage 0 + luck tests on pre-cutoff data. Every run stored as a trial."""
    data = data or HistoryData(con)
    with session_scope(engine) as s:
        row = s.get(LabSpec, spec_id)
        assert row is not None
        spec = parse(row.spec_yaml)
    start = _start(data)
    end = V.screening_end(data.calendar[-1] + timedelta(days=1) if data.calendar else start)
    lk = cfg()["luck"]
    main = run_spec(con, spec, start, end, data)
    stress = run_spec(
        con, spec, start, end, data, RunOptions(spread_mult=float(lk["cost_stress_spread_mult"]))
    )
    plateau = [(lbl, run_spec(con, n, start, end, data)) for lbl, n in V.neighbours(spec)]
    placebo: list[float] = []
    for i in range(int(lk["placebo"]["runs"])):
        r = run_spec(con, spec, start, end, data, RunOptions(shift=V.placebo_shift(i)))
        e = V.stats(r.trades)["expectancy_pct"]
        placebo.append(e if e is not None else 0.0)
    with session_scope(engine) as s:
        t0 = record(s, spec_id, "stage0", main)
        t0.metrics_json = {
            **(t0.metrics_json or {}),
            "diagnostics": V.diagnose(main.trades, data.regime_label),
        }
        record(s, spec_id, "cost", stress, f"spread x{lk['cost_stress_spread_mult']}")
        for lbl, r in plateau:
            record(s, spec_id, "plateau", r, lbl)
        n_trials, var = trial_population(s)
        checks, st = V.stage0_checks(
            main, stress, plateau, placebo, n_trials, var, data.regime_label
        )
        checks.items["placebo"]["distribution_mean"] = (
            round(float(np.mean(placebo)), 3) if placebo else None
        )
        v = LabVerdict(
            spec_id=spec_id,
            stage="stage0",
            passed=checks.passed,
            checks_json={"checks": checks.items, "stats": st},
            summary=_summary("Stage 0", checks),
            created_at=datetime.now(IST),
        )
        s.add(v)
        sp = s.get(LabSpec, spec_id)
        assert sp is not None
        sp.stage = "holdout_pending" if checks.passed else "rejected"
        _sync_hypothesis(s, sp.hypothesis_id)
        s.flush()
        s.expunge(v)
    return v


def holdout(
    con: duckdb.DuckDBPyConnection,
    spec_id: int,
    engine: Engine | None = None,
    data: HistoryData | None = None,
) -> LabVerdict | dict[str, Any]:
    """Stage 1 on post-cutoff data. Pending -> counts only. Evaluated exactly once."""
    data = data or HistoryData(con)
    with session_scope(engine) as s:
        done = s.scalars(
            select(LabVerdict).where(LabVerdict.spec_id == spec_id, LabVerdict.stage == "holdout")
        ).first()
        if done is not None:
            raise RegistryError(f"holdout of spec {spec_id} was already used")
        row = s.get(LabSpec, spec_id)
        assert row is not None
        if row.stage != "holdout_pending":
            raise RegistryError(f"spec {spec_id} is not waiting for its holdout ({row.stage})")
        spec = parse(row.spec_yaml)
    start = V.cutoff() + timedelta(days=1)
    end = data.calendar[-1] + timedelta(days=1) if data.calendar else start
    lk = cfg()["luck"]
    main = run_spec(con, spec, start, end, data, _post_cutoff())
    stress = run_spec(
        con, spec, start, end, data, _post_cutoff(float(lk["cost_stress_spread_mult"]))
    )
    checks, st = V.holdout_checks(main, stress)
    with session_scope(engine) as s:
        if checks is None:  # pending: counts only, never results
            record(s, spec_id, "holdout_count", main, counts_only=True)
            return {"pending": True, **st}
        record(s, spec_id, "holdout", main)
        v = LabVerdict(
            spec_id=spec_id,
            stage="holdout",
            passed=checks.passed,
            checks_json={"checks": checks.items, "stats": st},
            summary=_summary("Stage 1 holdout", checks),
            created_at=datetime.now(IST),
        )
        s.add(v)
        sp = s.get(LabSpec, spec_id)
        assert sp is not None
        sp.stage = "incubating" if checks.passed else "rejected"
        _sync_hypothesis(s, sp.hypothesis_id)
        s.flush()
        s.expunge(v)
    return v


def _post_cutoff(mult: float = 1.0) -> RunOptions:
    return RunOptions(spread_mult=mult, walk_forward=False)


ORDER = ["rejected", "stage0", "holdout_pending", "incubating", "validated", "retired"]


def _sync_hypothesis(s: Session, hid: int) -> None:
    h = s.get(LabHypothesis, hid)
    if h is None:
        return
    stages = list(s.scalars(select(LabSpec.stage).where(LabSpec.hypothesis_id == hid)))
    best = max(stages, key=lambda x: ORDER.index(x) if x in ORDER else 0, default="testing")
    new = {"stage0": "testing"}.get(best, best)
    if h.status != new:
        h.status, h.updated_at = new, datetime.now(IST)


def _summary(stage: str, checks: V.Checks) -> str:
    if checks.passed:
        return f"{stage}: passed every check"
    return f"{stage}: failed {', '.join(checks.failed())}"
