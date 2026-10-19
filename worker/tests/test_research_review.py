"""Phase 13 PR 4: weekly self-review -> lessons (post-cutoff only), digest, lesson delete."""

from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.ai.client import AiClient
from app.ai.providers.base import StructuredRequest, StructuredResult, Usage
from app.config import IST, Settings, get_settings
from app.db.models import Analysis, Instrument, PredictionScore, ResearchLesson, ResearchRun
from app.research import review
from app.research.agent import lessons_for

NOW = datetime(2026, 10, 25, 8, 20, tzinfo=IST)  # Sunday


@pytest.fixture
def cutoff(monkeypatch):
    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", "2026-06-30")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _seed(engine: Engine, made: datetime, n: int = 3) -> list[int]:
    ids = []
    with Session(engine) as s:
        if s.get(Instrument, "TCS") is None:
            s.add(Instrument(symbol="TCS", name="TCS", sector="IT", is_fno=True, lot_size=175))
        for i in range(n):
            an = Analysis(symbol="TCS", thesis=f"t{i}", direction="bullish", created_at=made,
                          prediction_source="agent", scores_json={"event_types": ["results"]})
            s.add(an)
            s.flush()
            ps = PredictionScore(analysis_id=an.id, symbol="TCS", direction="bullish",
                                 predicted_move_pct=3, predicted_move_raw_pct=4,
                                 prediction_source="agent", window_days=3, spot_start=100,
                                 created_at=made, status="scored", realised_move_pct=-1.0,
                                 direction_hit=False, scored_at=NOW - timedelta(days=2))
            s.add(ps)
            s.flush()
            ids.append(ps.id)
        s.commit()
    return ids


class FakeProvider:
    name = "anthropic"

    def __init__(self, lessons: list[dict[str, Any]]) -> None:
        self.lessons, self.reqs = lessons, []  # type: ignore[var-annotated]

    def structured(self, req: StructuredRequest, timeout_s: float = 180) -> StructuredResult:
        self.reqs.append(req)
        return StructuredResult({"lessons": self.lessons}, Usage(30_000, 2_000), [])


def _ai(engine: Engine) -> AiClient:
    return AiClient(Settings(_env_file=None), engine=engine)  # type: ignore[call-arg]


def test_review_stores_valid_lessons_only(engine: Engine, cutoff) -> None:
    ids = _seed(engine, NOW - timedelta(days=5))
    prov = FakeProvider([
        {"scope_kind": "stock", "scope": "TCS", "text": "Results beats faded within 3 days.",
         "evidence_ids": ids[:2]},
        {"scope_kind": "stock", "scope": "INFY", "text": "not in data", "evidence_ids": ids},
        {"scope_kind": "event_type", "scope": "results", "text": "invented", "evidence_ids": [999]},
        {"scope_kind": "sector", "scope": "IT", "text": "IT moves smaller than base rate.",
         "evidence_ids": ids},
    ])
    assert review.weekly_review(engine, _ai(engine), prov, NOW) == 2
    req = prov.reqs[0]
    assert req.model == "claude-sonnet-5-5" and "untrusted content" in req.user
    assert review.weekly_review(engine, _ai(engine), prov, NOW) == 0  # once per week
    with Session(engine) as s:
        les = s.scalars(select(ResearchLesson)).all()
        assert {x.scope for x in les} == {"TCS", "IT"}
        run = s.scalars(select(ResearchRun).where(ResearchRun.kind == "review")).one()
        assert run.status == "ok" and run.cost_inr > 0 and len(run.output_json["dropped"]) == 2
        assert review.lessons_for_digest(s, NOW.date() - timedelta(days=6))


def test_review_ignores_pre_cutoff_predictions(engine: Engine, monkeypatch) -> None:
    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", "2026-12-31")
    get_settings.cache_clear()
    _seed(engine, NOW - timedelta(days=5))
    prov = FakeProvider([])
    assert review.weekly_review(engine, _ai(engine), prov, NOW) == 0
    assert prov.reqs == []  # nothing resolved after the cutoff: no AI call
    get_settings.cache_clear()


def test_deleted_lessons_are_not_shown_to_the_agent(engine: Engine, cutoff) -> None:
    ids = _seed(engine, NOW - timedelta(days=5))
    prov = FakeProvider([{"scope_kind": "stock", "scope": "TCS", "text": "x", "evidence_ids": ids}])
    review.weekly_review(engine, _ai(engine), prov, NOW)
    assert len(lessons_for(engine, "TCS", "IT", [])) == 1
    with Session(engine) as s:
        les = s.scalars(select(ResearchLesson)).one()
        les.deleted = True
        s.commit()
    assert lessons_for(engine, "TCS", "IT", []) == []
