"""B3: AI vs rules. For every analysis, record what the rules-only path would have done at the same
moment (news direction confirmed by event-study analogues, no AI), then compare both with the
stock's realised move once the prediction scorecard has scored it."""

import math
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models import AiVsRules, Analysis, PredictionScore
from app.db.session import session_scope
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


def resolve(engine: Engine | None = None) -> int:
    """Copy the realised stock move (signed: + up) from the scored prediction."""
    n = 0
    with session_scope(engine) as s:
        for row in s.scalars(select(AiVsRules).where(AiVsRules.resolved_at.is_(None))):
            ps = s.scalar(
                select(PredictionScore).where(PredictionScore.analysis_id == row.analysis_id)
            )
            if ps is None or ps.status != "scored" or ps.realised_move_pct is None:
                continue
            sign = 1 if ps.direction == "bullish" else -1
            row.realised_move_pct = round(sign * ps.realised_move_pct, 3)
            row.resolved_at = datetime.now().astimezone()
            n += 1
    return n


def summarize(rows: list[AiVsRules]) -> dict[str, Any]:
    """Hit rate and average move captured (in each side's chosen direction) for the trades each
    side would take, plus disagreements."""
    done = [r for r in rows if r.realised_move_pct is not None]

    def side(pick: Any) -> dict[str, Any]:
        moves = []
        for r in done:
            d = pick(r)
            if d:
                moves.append(r.realised_move_pct * (1 if d == "bullish" else -1))  # type: ignore[operator]
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
