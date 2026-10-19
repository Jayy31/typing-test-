"""Calibration records, fitting and use (DESIGN_V2 section 5, owner rules Phase 5).

Targets:
  - p_move: P(stock moves our way over the window), resolved by the prediction scorecard;
  - p_t1_before_sl: P(option reaches T1 before the stop), resolved from the paper trade or, for
    rejected signals, from the counterfactual replay.
Each record stores the RAW model probability (the calibrator's input), what the gates used, the
prediction source (panel / single) and the news-event cluster. Fitting weights each event as
one, uses only records dated after AI_MODEL_KNOWLEDGE_CUTOFF (R1), Platt scaling from
CALIBRATION_MIN_RECORDS independent events and isotonic from CALIBRATION_ISOTONIC_MIN.
Calibrators are fitted for all predictions and per source; the per-source one is used once it
exists, otherwise the combined one.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app import flags
from app.calibration import fit as F
from app.config import IST, get_settings
from app.db.models import Analysis, CalibrationRecord, Calibrator, PaperTrade, RejectedCandidate
from app.db.session import session_scope
from app.history.integrity import knowledge_cutoff
from app.timeutil import as_ist
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
TARGETS = ("p_move", "p_t1_before_sl")
SOURCES = ("all", "panel", "single")
MARKET_P = {"p_move": 0.5}  # p_t1's market value is simulated per candidate


def cluster_key(analysis: Analysis, news_ids: list[int] | None = None) -> str:
    """The originating news event: the news cluster, else the earliest cited news item."""
    if analysis.cluster_id:
        return f"cluster:{analysis.cluster_id}"
    ids = news_ids or (analysis.scores_json or {}).get("news_ids") or []
    return f"news:{min(ids)}" if ids else f"analysis:{analysis.id}"


def add_record(
    s: Session,
    analysis: Analysis,
    target: str,
    raw_p: float,
    used_p: float | None,
    source: str | None,
    calibrator_id: int | None = None,
    signal_id: int | None = None,
) -> None:
    exists = s.scalar(
        select(CalibrationRecord.id).where(
            CalibrationRecord.analysis_id == analysis.id, CalibrationRecord.target == target
        )
    )
    if exists:
        return
    s.add(
        CalibrationRecord(
            analysis_id=analysis.id,
            signal_id=signal_id,
            symbol=analysis.symbol,
            target=target,
            predicted=float(raw_p),
            predicted_used=used_p,
            calibrator_id=calibrator_id,
            source=source,
            cluster_key=cluster_key(analysis),
            recorded_at=analysis.created_at,
        )
    )


# ---- using calibrators ------------------------------------------------------------------
@dataclass(frozen=True)
class ActiveCalibrator:
    id: int
    target: str
    source: str
    method: str
    params: dict[str, Any]
    n_samples: int
    n_events: int | None
    brier: float | None

    def apply(self, p: float) -> float:
        return F.apply_params(self.method, self.params, p)

    def label(self) -> str:
        return f"{self.method} ({self.source}, {self.n_events or self.n_samples} events)"


def active(s: Session, target: str, source: str | None) -> ActiveCalibrator | None:
    for src in ([source] if source in ("panel", "single") else []) + ["all"]:
        c = s.scalars(
            select(Calibrator).where(
                Calibrator.target == target, Calibrator.source == src, Calibrator.active.is_(True)
            )
        ).first()
        if c:
            return ActiveCalibrator(
                c.id,
                c.target,
                c.source,
                c.method,
                dict(c.params_json),
                c.n_samples,
                c.n_events,
                c.brier,
            )
    return None


def weight_for(raw_p: float | None, cal_p: float | None, market_p: float, default: float) -> float:
    """Share of AI paths that reproduces the calibrated probability: the simulated probability is
    linear in that share (p = w * p_ai + (1 - w) * p_market), so w = (cal - market) / (raw - market).
    Without a calibrator: the configured shrink weight."""
    if raw_p is None or cal_p is None or abs(raw_p - market_p) < 1e-6:
        return default
    return float(min(max((cal_p - market_p) / (raw_p - market_p), 0.0), 1.0))


@dataclass(frozen=True)
class Policy:
    """How AI predictions are tempered for one analysis: the move weight (prediction-level, from
    the p_move calibrator) and the per-candidate p_t1 calibrator."""

    move_weight: float
    method: str  # shrink / platt / isotonic
    move_cal: ActiveCalibrator | None
    t1_cal: ActiveCalibrator | None
    shrink: float

    @property
    def calibrator_id(self) -> int | None:
        c = self.t1_cal or self.move_cal
        return c.id if c else None


def policy(s: Session, source: str | None, raw_p_move: float | None) -> Policy:
    shrink = min(max(float(get_settings().scenario_shrink_weight), 0.0), 1.0)
    use = flags.active(s, "feat.probabilities")  # owner switch: off = shrink only
    move_cal = active(s, "p_move", source) if use else None
    t1_cal = active(s, "p_t1_before_sl", source) if use else None
    w = shrink
    if move_cal and raw_p_move is not None:
        w = weight_for(raw_p_move, move_cal.apply(raw_p_move), 0.5, shrink)
    used = t1_cal or move_cal
    method = used.method if used else "shrink"
    return Policy(w, method, move_cal, t1_cal, shrink)


# ---- resolving outcomes -----------------------------------------------------------------
def _t1_outcome(state: dict[str, Any] | None) -> bool | None:
    """True once T1 was reached; False if the position closed before T1; None while open."""
    if not state:
        return None
    if int(state.get("targets_hit") or 0) >= 1:
        return True
    return False if state.get("closed") else None


def resolve(
    engine: Engine | None = None, now: datetime | None = None, max_age_days: int = 45
) -> int:
    """p_t1_before_sl records from trades and counterfactual replays (p_move is resolved by the
    scorecard). Records with no outcome after `max_age_days` are closed as 'no outcome'."""
    now = now or datetime.now(IST)
    n = 0
    with session_scope(engine) as s:
        for r in s.scalars(
            select(CalibrationRecord).where(
                CalibrationRecord.target == "p_t1_before_sl",
                CalibrationRecord.resolved_at.is_(None),
            )
        ):
            outcome = None
            trade = (
                s.scalars(select(PaperTrade).where(PaperTrade.signal_id == r.signal_id)).first()
                if r.signal_id
                else None
            )
            if trade is not None:
                r.trade_id = trade.id
                outcome = _t1_outcome(trade.state_json)
            else:
                rej = (
                    s.scalars(
                        select(RejectedCandidate)
                        .where(
                            RejectedCandidate.analysis_id == r.analysis_id,
                            RejectedCandidate.ref_strike.is_not(None),
                        )
                        .order_by(RejectedCandidate.id)
                    ).first()
                    if r.analysis_id
                    else None
                )
                if rej is not None:
                    r.rejection_id = rej.id
                    outcome = _t1_outcome(((rej.details_json or {}).get("cf") or {}).get("state"))
                    if outcome is None and rej.outcome_status != "open":
                        outcome = False  # replay ended (horizon) without reaching T1
            if outcome is not None:
                r.outcome, r.resolved_at = outcome, now
                n += 1
            elif now - as_ist(r.recorded_at) > timedelta(days=max_age_days):
                r.resolved_at = now  # no trade and no replay: no outcome
    return n


def resolve_move(s: Session, analysis_id: int, hit: bool | None, now: datetime) -> None:
    for r in s.scalars(
        select(CalibrationRecord).where(
            CalibrationRecord.analysis_id == analysis_id,
            CalibrationRecord.target == "p_move",
            CalibrationRecord.resolved_at.is_(None),
        )
    ):
        r.outcome, r.resolved_at = hit, now


# ---- fitting ----------------------------------------------------------------------------
def eligible(rows: list[CalibrationRecord]) -> list[CalibrationRecord]:
    """R1: only predictions dated after the AI knowledge cutoff count (none if it is not set)."""
    cutoff = knowledge_cutoff()
    if cutoff is None:
        return []
    return [r for r in rows if as_ist(r.recorded_at).date() > cutoff and r.outcome is not None]


def dataset(
    s: Session, target: str, source: str, book: str | None = None
) -> list[CalibrationRecord]:
    """Records for fitting/reporting. `book`: "main" / "watchlist" only, or None = what the fit
    uses (main, plus the watchlist book when config/watchlist.yaml calibration.include_in_fit)."""
    q = select(CalibrationRecord).where(
        CalibrationRecord.target == target, CalibrationRecord.outcome.is_not(None)
    )
    if source != "all":
        q = q.where(CalibrationRecord.source == source)
    if book is not None:
        q = q.where(CalibrationRecord.book == book)
    elif not load_yaml("watchlist.yaml")["calibration"]["include_in_fit"]:
        q = q.where(CalibrationRecord.book == "main")
    return eligible(list(s.scalars(q)))


def fit_all(engine: Engine | None = None, now: datetime | None = None) -> list[str]:
    """Refit every (target, source) with enough independent events. Returns a summary line each."""
    st = get_settings()
    now = now or datetime.now(IST)
    out = []
    with session_scope(engine) as s:
        for target in TARGETS:
            for source in SOURCES:
                rows = dataset(s, target, source)
                keys = [r.cluster_key or f"record:{r.id}" for r in rows]
                n_events = float(F.cluster_weights(keys).sum()) if rows else 0.0
                if n_events < st.calibration_min_records:
                    out.append(
                        f"{target}/{source}: {len(rows)} records, {n_events:.0f} events (< {st.calibration_min_records}): not fitted"
                    )
                    continue
                res = F.fit(
                    [r.predicted for r in rows],
                    [bool(r.outcome) for r in rows],
                    keys,
                    st.calibration_isotonic_min,
                )
                version = (
                    int(
                        s.scalar(
                            select(func.coalesce(func.max(Calibrator.version), 0)).where(
                                Calibrator.target == target, Calibrator.source == source
                            )
                        )
                        or 0
                    )
                    + 1
                )
                for old in s.scalars(
                    select(Calibrator).where(
                        Calibrator.target == target,
                        Calibrator.source == source,
                        Calibrator.active.is_(True),
                    )
                ):
                    old.active = False
                s.add(
                    Calibrator(
                        target=target,
                        source=source,
                        method=res.method,
                        version=version,
                        params_json={
                            **res.params,
                            "brier_raw": res.brier_raw,
                            "reliability": res.reliability,
                        },
                        n_samples=res.n_records,
                        n_events=round(res.n_events),
                        brier=res.brier,
                        fitted_at=now,
                        active=True,
                    )
                )
                out.append(
                    f"{target}/{source}: {res.method} v{version} on {res.n_records} records / {res.n_events:.0f} events, "
                    f"Brier {res.brier_raw:.3f} -> {res.brier:.3f}"
                )
    log.info("calibration: %s", "; ".join(out))
    return out


def report(s: Session, target: str, since: date | None = None) -> dict[str, Any]:
    """Records, independent events and weighted Brier (raw vs used) per source, R1-filtered."""
    cutoff = knowledge_cutoff()
    res: dict[str, Any] = {"cutoff": cutoff.isoformat() if cutoff else None}
    for source in (*SOURCES, "book:main", "book:watchlist"):
        if source.startswith("book:"):  # Phase 12: main and watchlist books reported separately
            rows = dataset(s, target, "all", source.split(":")[1])
        else:
            rows = dataset(s, target, source)
        if since:
            rows = [r for r in rows if as_ist(r.recorded_at).date() >= since]
        if not rows:
            res[source] = {"records": 0, "events": 0}
            continue
        keys = [r.cluster_key or f"record:{r.id}" for r in rows]
        w = F.cluster_weights(keys)
        y = np.array([float(bool(r.outcome)) for r in rows])
        raw = np.array([r.predicted for r in rows])
        used = np.array(
            [r.predicted_used if r.predicted_used is not None else r.predicted for r in rows]
        )
        res[source] = {
            "records": len(rows),
            "events": round(float(w.sum()), 1),
            "brier_raw": round(F.brier(raw, y, w), 4),
            "brier_used": round(F.brier(used, y, w), 4),
            "hit_rate": round(float(np.average(y, weights=w)), 3),
            "mean_predicted": round(float(np.average(used, weights=w)), 3),
        }
    return res
