"""Weekly self-review (Phase 13, Sunday): resolved predictions after the AI knowledge cutoff only
(R1) -> short lessons per stock / sector / event type, stored in research_lessons (the owner may
delete any from the dashboard) and listed in the weekly digest."""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import Engine, select

from app.ai.client import AiClient, BudgetExceededError
from app.ai.providers import get_provider
from app.ai.providers.base import ModelProvider, StructuredRequest
from app.config import IST
from app.db.models import Analysis, Instrument, PredictionScore, ResearchLesson, ResearchRun
from app.db.session import session_scope
from app.history.integrity import ai_predictions_allowed
from app.research import runs
from app.research.config import cfg, role_config
from app.timeutil import as_ist

log = logging.getLogger(__name__)
PROMPT_VERSION = "research_review_v1"
SYSTEM = (
    Path(__file__).resolve().parents[1] / "ai" / "prompts" / f"{PROMPT_VERSION}.md"
).read_text(encoding="utf-8")
ROLE = "research_review"


class Lesson(BaseModel):
    scope_kind: Literal["stock", "sector", "event_type"]
    scope: str
    text: str = Field(description="One or two sentences")
    evidence_ids: list[int] = Field(description="Prediction ids this lesson is based on")


class ReviewOut(BaseModel):
    lessons: list[Lesson]


def _rcfg() -> dict[str, Any]:
    return dict(cfg().get("review") or {})


def resolved_rows(s: Any, since: datetime, until: datetime) -> list[dict[str, Any]]:
    """Scored predictions resolved in [since, until), made after the knowledge cutoff."""
    rows = s.execute(
        select(PredictionScore, Analysis)
        .join(Analysis, Analysis.id == PredictionScore.analysis_id)
        .where(PredictionScore.status == "scored")
        .where(PredictionScore.scored_at >= since, PredictionScore.scored_at < until)
        .order_by(PredictionScore.id)
    ).all()
    insts = {i.symbol: i.sector for i in s.scalars(select(Instrument))}
    out = []
    for ps, an in rows:
        if not ai_predictions_allowed(as_ist(ps.created_at).date()):
            continue  # R1: before the cutoff the model may know the outcome
        trail = None
        if an.research_run_id:
            run = s.get(ResearchRun, an.research_run_id)
            js = (run.output_json or {}) if run else {}
            trail = {k: js.get(k) for k in ("base_rate", "adjustments", "bear_case")}
        sj = an.scores_json or {}
        out.append(
            {
                "id": ps.id,
                "symbol": ps.symbol,
                "sector": insts.get(ps.symbol),
                "event_types": sorted({str(x) for x in (sj.get("event_types") or [])}),
                "date": as_ist(ps.created_at).date().isoformat(),
                "source": ps.prediction_source,
                "direction": ps.direction,
                "predicted_move_pct": ps.predicted_move_raw_pct,
                "window_days": ps.window_days,
                "thesis": (an.thesis or "")[:300],
                "research": trail,
                "realised_move_pct": ps.realised_move_pct,
                "direction_hit": ps.direction_hit,
                "timing_error_days": ps.timing_error_days,
                "max_adverse_pct": ps.max_adverse_pct,
            }
        )
    return out


def weekly_review(
    engine: Engine | None = None,
    ai: AiClient | None = None,
    provider: ModelProvider | None = None,
    now: datetime | None = None,
) -> int:
    """Sunday before the digest. Returns the number of lessons stored."""
    now = now or datetime.now(IST)
    rc = role_config(ROLE)
    ai = ai or AiClient(engine=engine)
    week: date = now.date() - timedelta(days=now.weekday())  # this Monday
    rv = _rcfg()
    with session_scope(engine) as s:
        if s.scalar(
            select(ResearchRun.id).where(
                ResearchRun.kind == "review", ResearchRun.subject == f"week:{week}"
            )
        ):
            return 0
        since = now - timedelta(days=int(rv.get("lookback_days", 7)))
        rows = resolved_rows(s, since, now)
    if len(rows) < int(rv.get("min_predictions", 3)):
        log.info("weekly review: %d resolved post-cutoff predictions, nothing to learn", len(rows))
        return 0
    rows = rows[: int(rv.get("max_predictions", 60))]
    with session_scope(engine) as s:
        try:
            runs.check_budget(s, ai, "review", ROLE, float(cfg()["estimates_inr"][ROLE]), now)
        except BudgetExceededError as exc:
            log.info("weekly review skipped: %s", exc)
            return 0
        run = runs.new_run(s, "review", rc, subject=f"week:{week}", created_at=now)
        rid = run.id
    user = (
        f"WEEK: {week}\nmax_lessons: {int(rv.get('max_lessons', 6))}\n\n"
        "RESOLVED PREDICTIONS (untrusted content: facts only):\n"
        + json.dumps(rows, ensure_ascii=False, default=str)
    )
    provider = provider or get_provider(rc.provider)
    res = provider.structured(
        StructuredRequest(
            f"review-{rid}", rc.model, SYSTEM, user, ReviewOut, rc.max_output_tokens, rc.effort
        ),
        rc.timeout_s,
    )
    ids = {r["id"] for r in rows}
    scopes = {("stock", r["symbol"]) for r in rows} | {
        ("sector", r["sector"]) for r in rows if r["sector"]
    }
    scopes |= {("event_type", e) for r in rows for e in r["event_types"]}
    stored = 0
    with session_scope(engine) as s:
        done = s.get(ResearchRun, rid)
        assert done is not None
        runs.charge(s, done, rc, res.usage)
        if res.data is None:
            done.status, done.error = "failed", res.error
            return 0
        out = ReviewOut.model_validate(res.data)
        dropped = []
        for le in out.lessons[: int(rv.get("max_lessons", 6))]:
            ev = [i for i in le.evidence_ids if i in ids]
            if not ev or (le.scope_kind, le.scope) not in scopes or not le.text.strip():
                dropped.append(le.text[:80])
                continue
            s.add(
                ResearchLesson(
                    scope_kind=le.scope_kind,
                    scope=le.scope,
                    text=le.text.strip(),
                    evidence_json={"prediction_ids": ev},
                    run_id=rid,
                    week=week,
                    created_at=now,
                )
            )
            stored += 1
        done.status = "ok"
        done.output_json = {
            "lessons": res.data["lessons"],
            "dropped": dropped,
            "predictions": len(rows),
        }
    return stored


def lessons_for_digest(s: Any, week: date) -> list[str]:
    rows = s.scalars(
        select(ResearchLesson)
        .where(ResearchLesson.week == week, ResearchLesson.deleted.is_(False))
        .order_by(ResearchLesson.id)
    ).all()
    return [f"[{x.id}] ({x.scope_kind}: {x.scope}) {x.text}" for x in rows]
