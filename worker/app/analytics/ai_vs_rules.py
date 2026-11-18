"""B3: AI vs rules. For every analysis, record what the rules-only path would have done at the same
moment (news direction confirmed by event-study analogues, no AI), then compare both with the
stock's realised move once the prediction scorecard has scored it."""

import math
from datetime import datetime, time
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models import AiVsRules, Analysis, PredictionScore
from app.db.session import session_scope
from app.timeutil import as_ist
from app.yaml_config import load_yaml


def rules_decision(analogue: Any, news_direction: str) -> tuple[str | None, str]:
    """Live version of the backtester's strict rule (owner): usable analogues, hit rate >= the
    strict minimum and a one-sided interval of the mean above 0 (std / sqrt(n_eff))."""
    if analogue is None or not analogue.usable:
        return None, "no usable analogues"
    t = load_yaml("backtest.yaml")["rules_mode"]["thresholds"]["strict"]
    z = float(load_yaml("tracking.yaml")["analytics"]["ai_vs_rules_ci_z"])
    if analogue.hit_rate < float(t["min_hit_rate"]):
        return (
            None,
            f"analogue hit rate {analogue.hit_rate:.0%} below {float(t['min_hit_rate']):.0%}",
        )
    lower = analogue.mean - z * analogue.std / math.sqrt(max(analogue.n_eff, 1.0))
    if lower <= 0:
        return (
            None,
            f"analogue mean {analogue.mean:+.2f}% not clearly above 0 (lower {lower:+.2f}%)",
        )
    return news_direction, "supported by analogues"


def record(
    s: Session, analysis: Analysis, news_direction: str, analogue: Any, ai_signal: bool
) -> None:
    if s.scalar(select(AiVsRules.id).where(AiVsRules.analysis_id == analysis.id)):
        return
    direction, why = rules_decision(analogue, news_direction)
    s.add(
        AiVsRules(
            analysis_id=analysis.id,
            symbol=analysis.symbol,
            created_at=analysis.created_at,
            news_direction=news_direction,
            ai_direction=analysis.direction
            if analysis.direction in ("bullish", "bearish")
            else None,
            ai_signal=ai_signal,
            rules_direction=direction,
            rules_reason=why,
            analogue_n=getattr(analogue, "n", None),
            analogue_mean=getattr(analogue, "mean", None),
        )
    )


def _stock_move(con: Any, symbol: str, created: datetime, window: int) -> float | None:
    """Raw stock move (%, + up) over `window` trading days from the last close known when the
    analysis was made (same day's close if made after 15:30 IST). None until the window ends."""
    ts = as_ist(created)
    after_close = ts.time() >= time(15, 30)
    op = "<=" if after_close else "<"
    ref = con.execute(
        f"SELECT date, close FROM hist_prices WHERE symbol = ? AND date {op} ? "
        "ORDER BY date DESC LIMIT 1",
        [symbol, ts.date()],
    ).fetchone()
    if not ref or not ref[1]:
        return None
    end = con.execute(
        "SELECT close FROM hist_prices WHERE symbol = ? AND date > ? "
        "ORDER BY date LIMIT 1 OFFSET ?",
        [symbol, ref[0], window - 1],
    ).fetchone()
    if not end or not end[0]:
        return None
    return (float(end[0]) / float(ref[1]) - 1) * 100


def resolve(engine: Engine | None = None, con: Any = None) -> int:
    """Copy the realised stock move (signed: + up) from the scored prediction. Analyses with no
    scorecard row (neutral AI: audit B10a) are resolved from the daily closes in the history
    store over the analysis window."""
    n = 0
    with session_scope(engine) as s:
        pending = []
        for row in s.scalars(select(AiVsRules).where(AiVsRules.resolved_at.is_(None))):
            ps = s.scalar(
                select(PredictionScore).where(PredictionScore.analysis_id == row.analysis_id)
            )
            if ps is None:
                pending.append(row)
                continue
            if ps.status != "scored" or ps.realised_move_pct is None:
                continue
            sign = 1 if ps.direction == "bullish" else -1
            row.realised_move_pct = round(sign * ps.realised_move_pct, 3)
            row.resolved_at = datetime.now().astimezone()
            n += 1
        if pending:
            n += _resolve_from_history(s, pending, con)
    return n


def _resolve_from_history(s: Session, rows: list[AiVsRules], con: Any) -> int:
    default_w = int(load_yaml("tracking.yaml")["analytics"].get("ai_vs_rules_window_days", 3))

    def body(c: Any) -> int:
        k = 0
        for row in rows:
            an = s.get(Analysis, row.analysis_id)
            w = int(an.window_days) if an is not None and an.window_days else default_w
            mv = _stock_move(c, row.symbol, row.created_at, max(w, 1))
            if mv is None:
                continue
            row.realised_move_pct = round(mv, 3)
            row.resolved_at = datetime.now().astimezone()
            k += 1
        return k

    if con is not None:
        return body(con)
    try:
        from app.history.store import connect

        with connect() as c:
            return body(c)
    except Exception:  # noqa: BLE001 - report only: retry on the next run
        return 0


def cluster_keys(s: Session, rows: list[AiVsRules]) -> dict[int, str]:
    """News event per row (the analysis's news cluster), so one story counts once."""
    ids = [r.analysis_id for r in rows]
    if not ids:
        return {}
    got = dict(
        s.execute(select(Analysis.id, Analysis.cluster_id).where(Analysis.id.in_(ids))).all()
    )
    return {i: f"c{got[i]}" if got.get(i) is not None else f"a{i}" for i in ids}


def summarize(rows: list[AiVsRules], clusters: dict[int, str] | None = None) -> dict[str, Any]:
    """Hit rate and average move captured (in each side's chosen direction) for the trades each
    side would take, plus disagreements. With `clusters` (audit B10a) rows from the same news
    event are averaged into one call per (event, stock, direction)."""
    done = [r for r in rows if r.realised_move_pct is not None]

    def side(pick: Any) -> dict[str, Any]:
        groups: dict[Any, list[float]] = {}
        for r in done:
            d = pick(r)
            if d:
                m = r.realised_move_pct * (1 if d == "bullish" else -1)  # type: ignore[operator]
                key = (clusters.get(r.analysis_id), r.symbol, d) if clusters else r.id
                groups.setdefault(key, []).append(m)
        moves = [sum(v) / len(v) for v in groups.values()]
        if not moves:
            return {"n": 0}
        return {
            "n": len(moves),
            "hit_rate": round(sum(m > 0 for m in moves) / len(moves), 3),
            "avg_move_pct": round(sum(moves) / len(moves), 2),
        }

    return {
        "resolved": len(done),
        "pending": len(rows) - len(done),
        "events_clustered": clusters is not None,
        "ai": side(lambda r: r.ai_direction if r.ai_signal else None),
        "rules": side(lambda r: r.rules_direction),
        "ai_only": side(
            lambda r: r.ai_direction if r.ai_signal and not r.rules_direction else None
        ),
        "rules_only": side(
            lambda r: r.rules_direction if r.rules_direction and not r.ai_signal else None
        ),
        "both_agree": side(
            lambda r: (
                r.ai_direction if r.ai_signal and r.rules_direction == r.ai_direction else None
            )
        ),
    }
