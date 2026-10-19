"""Phase 13 PR 3: live research agent loop, limits, validation, routing and fallback."""

import re
from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.ai.client import AiClient
from app.ai.providers.base import ToolCall, ToolResult, Turn, Usage, WebSource
from app.config import IST, Settings
from app.db.config_store import set_config
from app.db.models import Analysis, Instrument, ResearchRun, ResearchToolCall, Signal
from app.db.session import session_scope
from app.research import agent as agent_mod
from app.research.agent import run_live
from app.research.config import role_config
from app.signals.service import run_signal_cycle
from tests.helpers import FakeMessages, analyst_output, seed_instruments, seed_news

NOW = datetime(2026, 10, 19, 11, 0, tzinfo=IST)


def answer(news_id: int, **kw: Any) -> dict[str, Any]:
    a = analyst_output(cited_news_ids=[news_id]).model_dump(mode="json")
    a.update(
        base_rate="results beats: +2.1% avg, n=40",
        adjustments=["guidance raised: +1%"],
        bear_case="Peers may not follow; the move could fade.",
        cited_urls=[],
        lessons_applied=[],
    )
    a.update(kw)
    return a


class ScriptConv:
    def __init__(self, script: list[Any]) -> None:
        self.script, self.sent = script, []  # type: ignore[var-annotated]

    def send(self, text: str | None = None, tool_results: list[ToolResult] | None = None) -> Turn:
        self.sent.append((text, tool_results))
        step = self.script.pop(0)
        return step(text, tool_results) if callable(step) else step


class ScriptProvider:
    name = "anthropic"

    def __init__(self, script: list[Any]) -> None:
        self.conv = ScriptConv(script)
        self.started: dict[str, Any] = {}

    def start(self, **kw: Any) -> ScriptConv:
        self.started = kw
        return self.conv


def call(name: str, args: dict[str, Any], i: str = "c1", **usage: int) -> Turn:
    return Turn("", [ToolCall(i, name, args)], "tool_use", Usage(5_000, 500, **usage))


def submit(a: dict[str, Any], sources: list[WebSource] | None = None) -> Turn:
    return Turn(
        "", [ToolCall("s1", "submit_answer", a)], "tool_use", Usage(6_000, 800), sources or []
    )


def _seed(engine: Engine) -> int:
    seed_instruments(engine, {"TCS": 175})
    return seed_news(engine, "TCS", 0.8, "nse_announcements", title="TCS big order")[0]


def test_happy_path_tools_then_answer(engine: Engine) -> None:
    nid = _seed(engine)
    prov = ScriptProvider([call("get_news", {"symbol": "TCS"}), submit(answer(nid))])
    rc = role_config("research_live")
    r = run_live(rc, "TCS", "IT", ["order_win"], "evidence", {nid}, engine, prov, NOW)
    assert r.status == "ok" and r.out is not None and r.out.cited_news_ids == [nid]
    assert prov.started["model"] == "claude-sonnet-5-5"
    names = [t.name for t in prov.started["tools"]]
    assert "submit_answer" in names and "get_news" in names and "place_order" not in names
    assert prov.started["web"].enabled and "sebi.gov.in" in prov.started["web"].allowed_domains
    with Session(engine) as s:
        run = s.get(ResearchRun, r.run_id)
        assert run is not None and run.cost_inr > 0 and run.tool_calls == 1
        assert run.bear_case and run.output_json["base_rate"].startswith("results")
        assert [c.tool for c in s.scalars(select(ResearchToolCall))] == ["get_news"]


def test_invalid_answer_gets_one_retry(engine: Engine) -> None:
    nid = _seed(engine)
    prov = ScriptProvider([submit(answer(999)), submit(answer(nid))])
    r = run_live(role_config("research_live"), "TCS", "IT", [], "e", {nid}, engine, prov, NOW)
    assert r.status == "ok"
    rejected = prov.conv.sent[1][1][0]
    assert rejected.is_error and "unknown news ids" in rejected.content
    prov = ScriptProvider([submit(answer(999)), submit(answer(998))])
    r = run_live(role_config("research_live"), "TCS", "IT", [], "e", {nid}, engine, prov, NOW)
    assert r.status == "failed" and r.out is None and "after retry" in (r.error or "")


def test_uncited_or_off_list_urls_are_rejected(engine: Engine) -> None:
    nid = _seed(engine)
    seen = [WebSource("https://www.nseindia.com/a", "x"), WebSource("https://evil.com/x", "y")]
    ok = answer(nid, cited_urls=["https://www.nseindia.com/a"])
    bad = answer(nid, cited_urls=["https://evil.com/x"])
    prov = ScriptProvider([submit(bad, seen), submit(ok)])
    r = run_live(role_config("research_live"), "TCS", "IT", [], "e", {nid}, engine, prov, NOW)
    assert r.status == "ok"
    assert "not returned by your web tools" in prov.conv.sent[1][1][0].content


def test_limit_forces_answer_and_marks_limited(engine: Engine) -> None:
    nid = _seed(engine)
    rc = replace(role_config("research_live"), max_tool_calls=1)
    prov = ScriptProvider([call("get_news", {"symbol": "TCS"}), submit(answer(nid))])
    r = run_live(rc, "TCS", "IT", [], "e", {nid}, engine, prov, NOW)
    assert r.status == "limited"
    assert "LIMIT REACHED (tool_calls)" in (prov.conv.sent[1][0] or "")
    with Session(engine) as s:
        run = s.get(ResearchRun, r.run_id)
        assert run is not None and run.limited_reason == "tool_calls"


def test_rupee_limit(engine: Engine) -> None:
    nid = _seed(engine)
    rc = replace(role_config("research_live"), max_inr=0.01)
    prov = ScriptProvider([call("get_news", {"symbol": "TCS"}), call("get_regime", {}, "c2")])
    r = run_live(rc, "TCS", "IT", [], "e", {nid}, engine, prov, NOW)
    assert r.status == "failed" and "limit reached (rupees)" in (r.error or "")


def test_tool_results_go_back_to_the_model(engine: Engine) -> None:
    nid = _seed(engine)
    prov = ScriptProvider([call("get_news", {"symbol": "TCS"}), submit(answer(nid))])
    now = datetime.now(IST)  # seed_news stamps items relative to the wall clock
    run_live(role_config("research_live"), "TCS", "IT", [], "e", {nid}, engine, prov, now)
    res = prov.conv.sent[1][1]
    assert res and res[0].call_id == "c1" and "TCS big order" in res[0].content


# ---- integration --------------------------------------------------------------------------
def _client(engine: Engine, **kw: Any) -> AiClient:
    s = Settings(_env_file=None, ai_model_analyst="mid-m", **kw)  # type: ignore[call-arg]
    return AiClient(s, engine, FakeMessages(analyst_output()))


def _flag_on(engine: Engine) -> None:
    with session_scope(engine) as s:
        set_config(s, "feat.research_agent", True)


def _agent_provider(fail_for: set[str] | None = None) -> Any:
    def factory(name: str, **kw: Any) -> Any:
        def respond(text: str | None, results: Any) -> Turn:
            nid = int(re.findall(r"\[id=(\d+)\]", text or "")[0])
            sym = re.search(r"RESEARCH FILE \((\w+)\)", text or "").group(1)  # type: ignore[union-attr]
            if fail_for and sym in fail_for:
                return Turn("I think it is bullish.", [], "end", Usage(1000, 100))
            return submit(answer(nid))

        return ScriptProvider([respond, lambda t, r: Turn("still no", [], "end", Usage())])

    return factory


def test_cycle_routes_two_agent_runs_then_single(engine: Engine, monkeypatch) -> None:
    seed_instruments(engine, {"BHARTIARTL": 475, "TCS": 175, "SBIN": 750})
    for sym in ("BHARTIARTL", "TCS", "SBIN"):
        seed_news(engine, sym, 0.8, "nse_announcements", title=f"{sym} big news")
    _flag_on(engine)
    monkeypatch.setattr(agent_mod, "get_provider", _agent_provider())
    run_signal_cycle(engine, _client(engine))
    with session_scope(engine) as s:
        rows = s.scalars(select(Analysis)).all()
        assert sorted(a.prediction_source for a in rows) == ["agent", "agent", "single"]
        ag = next(a for a in rows if a.prediction_source == "agent")
        assert ag.ai_preset == "budget" and ag.research_run_id
        assert ag.ai_models_json == {"research_live": "claude-sonnet-5-5"}
        assert ag.ai_cost_inr and ag.ai_cost_inr > 0
        assert ag.scores_json["research"]["used"] is True
        assert ag.prompt_ver == "research_live_v1"
        single = next(a for a in rows if a.prediction_source == "single")
        assert single.ai_preset is None
        for sig in s.scalars(select(Signal)):
            fl = sig.flags_json["feat.research_agent"]
            assert fl["active"] is (sig.ai_preset == "budget")
            assert fl["enabled"] is True


def test_failed_agent_falls_back_to_single_not_panel(engine: Engine, monkeypatch) -> None:
    seed_instruments(engine, {"TCS": 175})
    seed_news(engine, "TCS", 0.8, "nse_announcements", title="TCS big news")
    _flag_on(engine)
    monkeypatch.setattr(agent_mod, "get_provider", _agent_provider(fail_for={"TCS"}))
    run_signal_cycle(engine, _client(engine, ai_model_judge="strong-m"))
    with session_scope(engine) as s:
        an = s.scalars(select(Analysis)).one()
        assert an.prediction_source == "single"
        assert an.scores_json["research"]["used"] is False
        assert "single analyst" in an.scores_json["research"]["fallback"]
        run = s.scalars(select(ResearchRun)).one()
        assert run.status == "failed" and an.research_run_id == run.id
        assert an.ai_models_json["research_live"] == "claude-sonnet-5-5"
        assert an.ai_models_json["analyst"] == "mid-m"


def test_flag_off_keeps_the_panel_path(engine: Engine, monkeypatch) -> None:
    seed_instruments(engine, {"TCS": 175})
    seed_news(engine, "TCS", 0.8, "nse_announcements", title="TCS big news")

    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("agent must not run with the flag off")

    monkeypatch.setattr(agent_mod, "get_provider", boom)
    run_signal_cycle(engine, _client(engine))
    with session_scope(engine) as s:
        assert s.scalars(select(Analysis)).one().prediction_source == "single"
    _ = (Instrument, pytest)


def test_why_shows_research_trail(engine: Engine, monkeypatch) -> None:
    from app.notify.explain import research_lines

    seed_instruments(engine, {"TCS": 175})
    seed_news(engine, "TCS", 0.8, "nse_announcements", title="TCS big news")
    _flag_on(engine)
    monkeypatch.setattr(agent_mod, "get_provider", _agent_provider())
    run_signal_cycle(engine, _client(engine))
    with session_scope(engine) as s:
        an = s.scalars(select(Analysis)).one()
        text = "\n".join(research_lines(s, an))
    assert "RESEARCH AGENT" in text and "claude-sonnet-5-5" in text and "preset budget" in text
    assert "Bear case: Peers may not follow" in text and "Research file: none yet" in text
    assert "Base rate: results beats" in text
