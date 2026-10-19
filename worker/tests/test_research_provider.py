"""Phase 13 PR 0: research-agent config, provider-neutral layer (Anthropic impl), costing."""

from types import SimpleNamespace as NS
from typing import Any

import pytest
from pydantic import BaseModel

from app.ai.providers import get_provider
from app.ai.providers.anthropic_provider import AnthropicProvider, web_tool_params
from app.ai.providers.base import StructuredRequest, ToolResult, ToolSpec, Usage, WebTools
from app.research import config as rc
from app.research.costing import cost_inr


def test_budget_preset_is_default_and_has_no_opus() -> None:
    roles = rc.all_roles()
    assert {r.preset for r in roles.values()} == {"budget"}
    assert roles["research_files"].model == "claude-haiku-5-5"
    assert roles["research_files"].web is False
    assert roles["research_live"].model == "claude-sonnet-5-5"
    assert roles["research_review"].model == "claude-sonnet-5-5"
    assert not rc.top_enabled()
    assert roles["research_top"].model == "claude-sonnet-5-5"  # blank = research_live
    assert all("opus" not in r.model for r in roles.values())


def test_other_presets_kept_and_switchable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("RESEARCH_AGENT_PRESET", "balanced")
    get_settings.cache_clear()
    try:
        r = rc.all_roles()
        assert r["research_top"].model == "claude-opus-5-5" and rc.top_enabled()
        assert r["research_files"].web is True
        assert rc.role_config("research_live", "full").model == "claude-opus-5-5"
    finally:
        get_settings.cache_clear()


def test_env_overrides_role_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_LIVE_MAX_INR", "12.5")
    monkeypatch.setenv("RESEARCH_LIVE_EFFORT", "low")
    monkeypatch.setenv("RESEARCH_FILES_WEB", "true")
    c = rc.role_config("research_live")
    assert c.max_inr == 12.5 and c.effort == "low"
    assert rc.role_config("research_files").web is True


def test_unknown_preset_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("RESEARCH_AGENT_PRESET", "nope")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError):
            rc.preset_name()
    finally:
        get_settings.cache_clear()


def test_unknown_provider() -> None:
    with pytest.raises(ValueError):
        get_provider("other")


def test_web_tools_versions_and_domains() -> None:
    web = WebTools(True, 2, 3, ("sebi.gov.in", "nseindia.com"))
    sonnet = web_tool_params("claude-sonnet-5-5", web)
    assert [t["type"] for t in sonnet] == ["web_search_20260209", "web_fetch_20260209"]
    assert sonnet[0]["allowed_domains"] == ["nseindia.com", "sebi.gov.in"]
    assert sonnet[0]["max_uses"] == 2 and sonnet[1]["max_uses"] == 3
    haiku = web_tool_params("claude-haiku-5-5", web)
    assert haiku[0]["type"] == "web_search_20250305"
    assert web_tool_params("claude-sonnet-5-5", WebTools()) == []
    assert not any(t["type"].startswith("code_execution") for t in sonnet)


def test_cost_counts_web_search_and_haiku_long_rate() -> None:
    base = cost_inr("research_live", "claude-sonnet-5-5", Usage(10_000, 1_000))
    with_search = cost_inr(
        "research_live", "claude-sonnet-5-5", Usage(10_000, 1_000, web_searches=4)
    )
    assert with_search - base == pytest.approx(4 * 0.01 * 88.0, rel=1e-6)
    short = cost_inr("research_files", "claude-haiku-5-5", Usage(99_000, 0))
    long = cost_inr("research_files", "claude-haiku-5-5", Usage(101_000, 0))
    assert long / 101_000 == pytest.approx(5 * short / 99_000, rel=1e-6)


class _Blk(NS):
    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _usage(i: int = 100, o: int = 10, searches: int = 0) -> NS:
    return NS(
        input_tokens=i,
        output_tokens=o,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        server_tool_use=NS(web_search_requests=searches, web_fetch_requests=0),
    )


class FakeMessages:
    def __init__(self, responses: list[NS]) -> None:
        self.responses, self.calls = responses, []  # type: ignore[var-annotated]

    def create(self, **kw: Any) -> NS:
        self.calls.append(kw)
        return self.responses.pop(0)


def test_conversation_tools_pause_and_sources() -> None:
    paused = NS(
        stop_reason="pause_turn",
        usage=_usage(searches=1),
        content=[
            _Blk(
                type="server_tool_use", name="web_search", id="s1", input={"query": "TCS results"}
            ),
            _Blk(
                type="web_search_tool_result",
                tool_use_id="s1",
                content=[NS(url="https://nseindia.com/x", title="NSE")],
            ),
        ],
    )
    tool = NS(
        stop_reason="tool_use",
        usage=_usage(),
        content=[
            _Blk(type="text", text="checking"),
            _Blk(type="tool_use", id="t1", name="get_news", input={"symbol": "TCS"}),
        ],
    )
    fm = FakeMessages([paused, tool])
    prov = AnthropicProvider(client=NS(messages=fm))
    conv = prov.start(
        model="claude-sonnet-5-5",
        system="sys",
        tools=[
            ToolSpec(
                "get_news",
                "news",
                {"type": "object", "properties": {}, "additionalProperties": False},
            )
        ],
        web=WebTools(True, 2, 0, ("nseindia.com",)),
        max_tokens=1000,
        effort="medium",
        timeout_s=30,
    )
    turn = conv.send("go")
    assert turn.stop == "tool_use" and turn.tool_calls[0].name == "get_news"
    assert turn.usage.web_searches == 1 and turn.usage.input_tokens == 200
    assert turn.sources[0].url == "https://nseindia.com/x" and turn.web_queries == ["TCS results"]
    assert len(fm.calls) == 2  # resumed after pause_turn without a new user message
    assert fm.calls[0]["output_config"] == {"effort": "medium"}
    assert "tool_choice" not in fm.calls[0]
    assert fm.calls[0]["tools"][0]["strict"] is True
    fm.responses.append(
        NS(stop_reason="end_turn", usage=_usage(), content=[_Blk(type="text", text="ok")])
    )
    conv.send(tool_results=[ToolResult("t1", "[]")])
    last_user = fm.calls[-1]["messages"][-1]
    assert last_user["content"][0]["type"] == "tool_result"


class Out(BaseModel):
    answer: str


def test_structured_validates_json() -> None:
    good = NS(
        stop_reason="end_turn", usage=_usage(), content=[_Blk(type="text", text='{"answer": "x"}')]
    )
    bad = NS(stop_reason="end_turn", usage=_usage(), content=[_Blk(type="text", text="not json")])
    prov = AnthropicProvider(client=NS(messages=FakeMessages([good, bad])))
    req = StructuredRequest("c1", "claude-haiku-5-5", "s", "u", Out, 500, "low")
    assert prov.structured(req).data == {"answer": "x"}
    r = prov.structured(req)
    assert r.data is None and r.error
