"""Live AI smoke test harness (run with fakes here; real models on the VM)."""

from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, select

from app.ai import smoke
from app.ai.client import AiClient
from app.ai.schemas import AnalystOutput, CaseOutput, PostMortemOutput
from app.config import Settings, get_settings
from app.db.config_store import get_config, set_config
from app.db.models import AiSmokeRun, Alert, Base
from app.db.session import session_scope
from tests.helpers import FakeByType, analyst_output, panel_outputs

PANEL = dict(
    ai_model_analyst="mid-m",
    ai_model_researcher="cheap-m",
    ai_model_novelty="cheap-m",
    ai_model_bull="mid-m",
    ai_model_bear="mid-m",
    ai_model_judge="strong-m",
    ai_daily_budget_inr=1000,
)


@pytest.fixture
def engine(tmp_path) -> Engine:
    """The smoke test runs bull and bear in parallel threads: give each thread its own SQLite
    connection (a file database) instead of the shared in-memory one, which races."""
    eng = create_engine(f"sqlite:///{tmp_path / 'smoke.db'}")
    Base.metadata.create_all(eng)
    return eng


PM = PostMortemOutput(
    primary_cause="slippage_gap",
    secondary_causes=[],
    thesis_correct=False,
    summary="s",
    what_went_right=[],
    what_went_wrong=["gap"],
    lessons=["l"],
)


def outputs(over: dict[type, Any] | None = None) -> dict[type, Any]:
    o = panel_outputs(910001)
    o[AnalystOutput] = analyst_output(cited_news_ids=[910001])
    o[PostMortemOutput] = PM
    o.update(over or {})
    return o


def client(engine: Engine, fake: Any) -> AiClient:
    return AiClient(Settings(_env_file=None, **PANEL), engine, fake)  # type: ignore[call-arg]


def case_by_side(kw: dict[str, Any]) -> CaseOutput:
    return panel_outputs(910001)[CaseOutput][0 if "BULL advocate" in kw["system"][0]["text"] else 1]


def test_smoke_records_every_call_and_passes(engine: Engine) -> None:
    fake = FakeByType(outputs({CaseOutput: case_by_side}))
    res = smoke.run_smoke(engine, "manual", client(engine, fake))
    assert res.passed and res.panel_seconds is not None
    roles = [c["role"] for c in res.calls]
    assert sorted(roles) == sorted(
        ["researcher", "novelty", "bull", "bear", "judge", "analyst", "postmortem"]
    )
    c = next(c for c in res.calls if c["role"] == "judge")
    assert (
        c["first_try"]
        and c["attempts"] == 1
        and c["input_tokens"] == 3000
        and c["output_tokens"] == 500
    )
    assert (
        c["effort"] == "medium"
        and c["estimate_inr"] == 9.5
        and c["cost_inr"] > 0
        and c["seconds"] >= 0
    )
    assert 0 <= c["thinking_tokens_est"] <= 500
    efforts = {k["model"]: k["output_config"]["effort"] for k in fake.calls if "output_config" in k}
    assert efforts  # effort sent per role
    assert res.total_cost_inr == pytest.approx(sum(x["cost_inr"] for x in res.calls), abs=0.01)
    with session_scope(engine) as s:
        run = s.scalars(select(AiSmokeRun)).one()
        assert run.passed and len(run.results_json["calls"]) == 7
        text = s.scalars(select(Alert)).one().text
    assert text.startswith("AI SMOKE TEST: PASSED") and "vs est Rs 9.50" in text


def test_schema_failure_lowers_effort_and_reruns(engine: Engine) -> None:
    bad = panel_outputs(910001)[CaseOutput][0].model_copy(update={"cited_news_ids": [1]})
    fake = FakeByType(
        outputs(
            {CaseOutput: lambda kw: bad if "BULL" in kw["system"][0]["text"] else case_by_side(kw)}
        )
    )
    res = smoke.run_smoke(engine, "manual", client(engine, fake))
    assert not res.passed and any("bull: failed its schema" in f for f in res.fixes)
    failed = [c for c in res.calls if c["role"] == "bull" and not c["ok"]]
    assert failed and failed[0]["attempts"] == 2  # first try + 1 retry
    assert {c["run"] for c in res.calls} == {"first", "after_fix"}
    with session_scope(engine) as s:
        assert get_config(s, "ai.effort.bull") == "low"
    # the override is used by the client on the re-run
    assert any(k.get("output_config", {}).get("effort") == "low" for k in fake.calls)


def test_hard_cost_limit_skips_calls(engine: Engine, monkeypatch) -> None:
    cfg = dict(smoke._cfg(), limit_inr=0.5)
    monkeypatch.setattr(smoke, "_cfg", lambda: cfg)
    res = smoke.run_smoke(
        engine, "manual", client(engine, FakeByType(outputs({CaseOutput: case_by_side})))
    )
    assert not res.passed and res.total_cost_inr <= 0.5
    skipped = [c for c in res.calls if c["error"] and c["error"].startswith("skipped")]
    assert skipped and not res.fixes  # a cost skip is not a schema failure


def test_slow_panel_lowers_effort_on_slowest_roles(engine: Engine) -> None:
    res = smoke.SmokeResult(panel_seconds=240, panel_ok=True, single_ok=True, postmortem_ok=True)
    res.calls = [
        {"role": r, "ok": True, "error": None, "seconds": s}
        for r, s in (("bull", 90), ("bear", 80), ("judge", 140))
    ]
    fixes = smoke.apply_fixes(res, engine)
    assert (
        len(fixes) == 2 and fixes[0].startswith("judge: 140s") and fixes[1].startswith("bull: 90s")
    )
    with session_scope(engine) as s:
        assert get_config(s, "ai.effort.judge") == "low" and get_config(s, "ai.effort.bear") is None


def test_startup_runs_only_until_passed(engine: Engine, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    assert smoke.startup_check(engine) == "skipped: ANTHROPIC_API_KEY not set"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    for k, v in PANEL.items():
        monkeypatch.setenv(k.upper(), str(v))
    get_settings.cache_clear()
    calls: list[str] = []
    monkeypatch.setattr(
        smoke, "run_smoke", lambda e, trig, base: calls.append(trig) or smoke.SmokeResult()
    )
    assert smoke.startup_check(engine) == "failed" and calls == ["startup"]
    with session_scope(engine) as s:
        s.add(
            AiSmokeRun(
                started_at=__import__("datetime").datetime.now(),
                trigger="startup",
                passed=True,
                total_cost_inr=1,
                results_json={},
            )
        )
    assert smoke.startup_check(engine) == "skipped: already passed"
    get_settings.cache_clear()


def test_effort_override_from_config_table(engine: Engine) -> None:
    c = client(engine, FakeByType(outputs()))
    assert c.effort_for("judge") == "medium"
    with session_scope(engine) as s:
        set_config(s, "ai.effort.judge", "low")
    assert c.effort_for("judge") == "low"
