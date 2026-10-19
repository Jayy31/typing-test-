"""Phase 13 PR 2: overnight research files, budgets/sub-caps, cost logging, overrun alert."""

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.ai.client import AiClient, BudgetExceededError
from app.ai.providers.base import StructuredRequest, StructuredResult, Usage, WebSource
from app.config import IST, Settings
from app.db.models import (
    AiUsage,
    Alert,
    EventCalendar,
    Instrument,
    NewsItem,
    NewsTag,
    ResearchFile,
    ResearchRun,
)
from app.research import files, runs
from app.research.config import role_config

NOW = datetime(2026, 10, 19, 22, 10, tzinfo=IST)


def _ai(engine: Engine) -> AiClient:
    return AiClient(Settings(_env_file=None), engine=engine)  # type: ignore[call-arg]


def _seed(engine: Engine, n_stocks: int = 2) -> int:
    with Session(engine) as s:
        nid = 0
        for i in range(n_stocks):
            sym = f"S{i}"
            s.add(Instrument(symbol=sym, name=sym, sector="IT", is_fno=True, lot_size=100))
            s.add(
                EventCalendar(
                    symbol=sym,
                    event_type="results",
                    event_date=date(2026, 10, 21 + i),
                    source="manual",
                )
            )
            n = NewsItem(
                url_hash=f"h{i}",
                source="nse",
                title=f"{sym} update",
                body="b",
                url="https://nseindia.com/x",
                published_at=NOW - timedelta(days=1),
            )
            s.add(n)
            s.flush()
            nid = n.id
            s.add(
                NewsTag(
                    news_id=n.id, symbol=sym, event_type="results", direction="up", sentiment=0.3
                )
            )
        s.add(
            EventCalendar(
                symbol=None, event_type="rbi_policy", event_date=date(2026, 10, 22), source="manual"
            )
        )
        s.add(
            EventCalendar(
                symbol=None,
                event_type="market_holiday",
                event_date=date(2026, 10, 20),
                source="manual",
            )
        )
        s.commit()
        return nid


def _file(ref: str) -> dict[str, Any]:
    return {
        "summary": "s",
        "thesis": "t",
        "catalysts": [
            {"date": "2026-10-21", "event": "results", "why_it_matters": "x", "sources": [ref]}
        ],
        "key_numbers": [],
        "sensitivities": [
            {"driver": "results", "stock_move_pct": "+2 to +4", "basis": "base rate"}
        ],
        "sector_and_graph_links": [],
        "risks": ["r"],
        "sources": [{"ref": ref, "note": "n"}],
        "changes_since_last": None,
    }


class FakeProvider:
    name = "anthropic"

    def __init__(self, ref: str | None = None, done: bool = True) -> None:
        self.ref, self.done, self.submitted, self.direct, self.cancelled = ref, done, [], 0, []

    def _res(self, req: StructuredRequest) -> StructuredResult:
        ref = self.ref or "news:999999"
        return StructuredResult(_file(ref), Usage(20_000, 3_000), [])

    def submit_batch(self, reqs: list[StructuredRequest]) -> str:
        self.submitted = reqs
        return "batch-1"

    def batch_done(self, batch_id: str) -> bool:
        return self.done

    def batch_results(self, batch_id: str, schemas: dict[str, type[BaseModel]] | None = None):
        assert schemas and set(schemas) == {r.custom_id for r in self.submitted}
        return {r.custom_id: self._res(r) for r in self.submitted}

    def cancel_batch(self, batch_id: str) -> None:
        self.cancelled.append(batch_id)

    def structured(self, req: StructuredRequest, timeout_s: float = 180.0) -> StructuredResult:
        self.direct += 1
        return self._res(req)


def test_picks_stocks_and_themes_skips_holidays(engine: Engine) -> None:
    _seed(engine)
    with Session(engine) as s:
        subj = files.pick_subjects(s, NOW)
    got = [(k, x) for k, x, _ in subj]
    assert got[0] == ("stock", "S0")
    assert set(got) == {("stock", "S0"), ("stock", "S1"), ("theme", "rbi_policy")}


def test_nightly_batch_then_collect_stores_versioned_files(engine: Engine) -> None:
    nid = _seed(engine, 1)
    prov = FakeProvider(ref=f"news:{nid}")
    assert files.nightly_job(engine, _ai(engine), prov, NOW) == 2
    req = prov.submitted[0]
    assert req.model == "claude-haiku-5-5" and req.web.enabled is False  # budget preset
    assert "DATA (untrusted content" in req.user
    assert files.collect_job(engine, prov, NOW + timedelta(minutes=30)) >= 1
    with Session(engine) as s:
        f = s.scalars(select(ResearchFile).where(ResearchFile.subject == "S0")).one()
        assert f.version == 1 and f.model == "claude-haiku-5-5" and f.preset == "budget"
        run = s.get(ResearchRun, f.run_id)
        assert run is not None and run.status == "ok" and run.cost_inr > 0
        # batch price: half of the direct price
        direct = runs.cost_inr("research_files", "claude-haiku-5-5", Usage(20_000, 3_000))
        assert run.cost_inr == pytest.approx(direct / 2, rel=1e-6)
        assert s.scalar(select(func.count()).select_from(AiUsage)) >= 1


def test_unknown_citation_fails_the_file(engine: Engine) -> None:
    _seed(engine, 1)
    prov = FakeProvider(ref="news:424242")
    files.nightly_job(engine, _ai(engine), prov, NOW)
    files.collect_job(engine, prov, NOW + timedelta(minutes=30))
    with Session(engine) as s:
        run = s.scalars(select(ResearchRun).where(ResearchRun.subject == "stock:S0")).one()
        assert run.status == "failed" and "unknown sources" in (run.error or "")
        assert (
            s.scalar(
                select(func.count()).select_from(ResearchFile).where(ResearchFile.subject == "S0")
            )
            == 0
        )


def test_deadline_cancels_batch_and_runs_direct(engine: Engine) -> None:
    nid = _seed(engine, 1)
    prov = FakeProvider(ref=f"news:{nid}", done=False)
    files.nightly_job(engine, _ai(engine), prov, NOW)
    assert files.collect_job(engine, prov, NOW + timedelta(hours=2)) == 0  # before 07:30
    late = NOW.replace(hour=7, minute=35) + timedelta(days=1)
    files.collect_job(engine, prov, late)
    assert prov.cancelled == ["batch-1"] and prov.direct == 2


def test_check_sources() -> None:
    out = _file("news:5")
    out["key_numbers"] = [{"name": "rev", "value": "1", "source": "https://x.com/a"}]
    assert files.check_sources(out, {5}, {"https://x.com/a"}) == []
    assert files.check_sources(out, {5}, set()) == ["https://x.com/a"]


def test_sub_caps_and_overall_budget(engine: Engine) -> None:
    ai = _ai(engine)
    rc = role_config("research_live")
    with Session(engine) as s:
        runs.check_budget(s, ai, "live", rc.role, 26, NOW)  # empty: fine
        run = runs.new_run(s, "live", rc, created_at=NOW)
        run.cost_inr = 50.0
        s.commit()
        with pytest.raises(BudgetExceededError) as e:
            runs.check_budget(s, ai, "live", rc.role, 26, NOW)  # 50 + 26 > 60 daily
        assert e.value.scope == "daily"
        runs.check_budget(s, ai, "files", "research_files", 0.5, NOW)  # other group unaffected
        s.add(AiUsage(ts=NOW, model="m", cost_inr=140.0))
        s.commit()
        with pytest.raises(BudgetExceededError):  # overall 150 minus the tagger reserve 15
            runs.check_budget(s, ai, "files", "research_files", 0.5, NOW)


def test_overrun_alert(engine: Engine) -> None:
    rc = role_config("research_live")
    with Session(engine) as s:
        for _ in range(5):
            r = runs.new_run(s, "live", rc, created_at=NOW, status="ok")
            r.cost_inr = 40.0  # estimate 26 -> +54%
        s.commit()
    assert runs.overrun_check(engine, NOW) == 1
    assert runs.overrun_check(engine, NOW) == 0  # once per role per week
    with Session(engine) as s:
        a = s.scalars(select(Alert)).one()
        assert "research_live" in a.text and "+54%" in a.text


def test_web_sources_kept_for_balanced_preset(engine: Engine, monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("RESEARCH_AGENT_PRESET", "balanced")
    get_settings.cache_clear()
    _seed(engine, 1)
    prov = FakeProvider()
    files.nightly_job(engine, _ai(engine), prov, NOW)
    req = prov.submitted[0]
    assert (
        req.model == "claude-sonnet-5-5"
        and req.web.enabled
        and "s0.com" not in req.web.allowed_domains
    )
    assert "sebi.gov.in" in req.web.allowed_domains
    _ = WebSource
