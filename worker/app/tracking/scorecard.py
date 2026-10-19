"""Prediction scorecard (v2.1 B3): every directional prediction, traded or not, is scored on
direction, move size and timing against the stock's actual daily closes."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.calibration.service import resolve_move
from app.config import IST
from app.db.models import Analysis, PredictionScore
from app.db.session import session_scope
from app.market.live import UpstoxOptionData
from app.signals.expiry import trading_days_between
from app.signals.filters import load_holidays
from app.timeutil import as_ist
from app.yaml_config import load_yaml


def create_score(
    s: Session,
    analysis: Analysis,
    direction: str,
    predicted_shrunk: float,
    predicted_raw: float,
    p_move: float | None,
    spot: float,
    now: datetime,
    signal_id: int | None = None,
    source: str | None = "scenario_tree",
    prediction_source: str | None = None,
) -> None:
    if s.scalar(select(PredictionScore.id).where(PredictionScore.analysis_id == analysis.id)):
        return
    s.add(
        PredictionScore(
            analysis_id=analysis.id,
            signal_id=signal_id,
            symbol=analysis.symbol,
            direction=direction,
            predicted_move_pct=predicted_shrunk,
            predicted_move_raw_pct=predicted_raw,
            predicted_source=source,
            prediction_source=prediction_source,
            ai_preset=analysis.ai_preset,
            p_move=p_move,
            window_days=int(analysis.window_days or 3),
            spot_start=spot,
            created_at=now,
            path_json=[],
        )
    )


def score(ps: PredictionScore, closes: list[tuple[str, float]], holidays: set[date]) -> None:
    """Fill the result columns from the daily closes recorded so far."""
    sign = 1 if ps.direction == "bullish" else -1
    moves = [sign * (c / ps.spot_start - 1) * 100 for _, c in closes]
    if not moves:
        return
    start = as_ist(ps.created_at).date()
    ps.realised_move_pct = round(moves[-1], 3)
    ps.direction_hit = moves[-1] > 0
    ps.move_error_pct = round(moves[-1] - ps.predicted_move_pct, 3)
    ps.max_favourable_pct = round(max(moves), 3)
    ps.max_adverse_pct = round(min(moves), 3)
    target = 0.5 * abs(ps.predicted_move_raw_pct or ps.predicted_move_pct)
    hit_day = next((d for (d, _), m in zip(closes, moves, strict=True) if m >= target), None)
    if hit_day is not None:
        ps.days_to_half_move = trading_days_between(start, date.fromisoformat(hit_day), holidays)
        ps.timing_error_days = ps.days_to_half_move - ps.window_days
    ps.status = "scored"


def update_scores(
    engine: Engine | None = None,
    source: UpstoxOptionData | None = None,
    now: datetime | None = None,
) -> int:
    """Daily after the close: append each open prediction's close; score when its window ends."""
    now = now or datetime.now(IST)
    source = source or UpstoxOptionData(engine)
    if not source.available or now.weekday() >= 5:
        return 0
    buffer = int(load_yaml("tracking.yaml")["scorecard"]["buffer_days"])
    with session_scope(engine) as s:
        symbols = sorted(
            set(s.scalars(select(PredictionScore.symbol).where(PredictionScore.status == "open")))
        )
    closes: dict[str, float] = {}
    for sym in symbols:
        q = source.quote(sym)
        if q:
            closes[sym] = q.last_price
    n = 0
    with session_scope(engine) as s:
        holidays = load_holidays(s)
        for ps in s.scalars(select(PredictionScore).where(PredictionScore.status == "open")):
            if ps.symbol not in closes:
                continue
            path: list[Any] = list(ps.path_json or [])
            today = now.date().isoformat()
            if not path or path[-1][0] != today:
                path.append([today, closes[ps.symbol]])
            ps.path_json = path
            elapsed = trading_days_between(as_ist(ps.created_at).date(), now.date(), holidays)
            if elapsed >= ps.window_days + buffer:
                score(ps, [(d, c) for d, c in path], holidays)
                if ps.status == "scored":
                    resolve_move(s, ps.analysis_id, ps.direction_hit, now)
            n += 1
    return n


def summary(s: Session, since: date | None = None) -> dict[str, Any]:
    """Scored predictions per source (panel / single / all): count, independent news events,
    direction hit rate, mean move error. R1: only predictions after AI_MODEL_KNOWLEDGE_CUTOFF."""
    from collections import Counter

    from app.history.integrity import knowledge_cutoff

    cutoff = knowledge_cutoff()
    out: dict[str, Any] = {"cutoff": cutoff.isoformat() if cutoff else None}
    rows = (
        []
        if cutoff is None
        else [
            (ps, an)
            for ps, an in s.execute(
                select(PredictionScore, Analysis)
                .join(Analysis, Analysis.id == PredictionScore.analysis_id)
                .where(PredictionScore.status == "scored")
            ).all()
            if as_ist(ps.created_at).date() > cutoff
            and (since is None or as_ist(ps.created_at).date() >= since)
        ]
    )
    for source in ("all", "agent", "panel", "single"):
        sel = [(ps, an) for ps, an in rows if source == "all" or ps.prediction_source == source]
        if not sel:
            out[source] = {"n": 0}
            continue
        keys = [
            f"cluster:{an.cluster_id}" if an.cluster_id else f"analysis:{an.id}" for _, an in sel
        ]
        counts = Counter(keys)
        w = [1 / counts[k] for k in keys]
        hits = [1.0 if ps.direction_hit else 0.0 for ps, _ in sel]
        errs = [ps.move_error_pct or 0.0 for ps, _ in sel]
        tw = sum(w)
        out[source] = {
            "n": len(sel),
            "events": round(tw, 1),
            "hit_rate": round(sum(h * x for h, x in zip(hits, w, strict=True)) / tw, 3),
            "mean_move_error_pct": round(sum(e * x for e, x in zip(errs, w, strict=True)) / tw, 3),
        }
    return out
