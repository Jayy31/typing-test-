"""Phase 14 PR 2: idea sources, lab AI budget (paused first), refinement, nightly gating,
and the lab's safety boundary."""

import ast
import copy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.ai.client import AiClient, BudgetExceededError
from app.ai.providers.base import ToolCall, ToolResult, Turn, Usage
from app.config import IST, Settings, get_settings
from app.db.models import AiUsage, LabHypothesis, LabSpec, PostMortem, ResearchRun
from app.lab import ai as lab_ai
from app.lab import nightly, refine, sources
from app.lab import registry as R
from app.lab.spec import cfg
from tests import bt_fixture as F

NOW = datetime(2026, 10, 20, 23, 10, tzinfo=IST)


@pytest.fixture
def cutoff(monkeypatch):
    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", "2026-06-30")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ai(engine: Engine, **kw: Any) -> AiClient:
    return AiClient(Settings(_env_file=None, **kw), engine=engine)  # type: ignore[call-arg]


def patch_cfg(monkeypatch, **over: Any) -> dict[str, Any]:
    c = copy.deepcopy(cfg())
    for path, v in over.items():
        node = c
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = v
    import app.lab.spec as S

    for mod in (S, sources, refine, lab_ai, R, nightly):
        monkeypatch.setattr(mod, "cfg", lambda c=c: c)
    return c


def test_seed_known_patterns_once(engine: Engine) -> None:
    assert sources.seed_patterns(engine) == 7
    assert sources.seed_patterns(engine) == 0
    with Session(engine) as s:
        hs = {h.name: h for h in s.scalars(select(LabHypothesis))}
        assert hs["news_core_calm_regime"].source == "news_core"
        assert hs["pre_results_iv_runup"].rationale.startswith("Implied volatility")
        assert all(not h.ai_memory_risk for h in hs.values())
        assert s.scalar(select(LabSpec.id).where(LabSpec.name == "index_inclusion"))


def test_mining_uses_pre_cutoff_reactions_only(engine: Engine, monkeypatch) -> None:
    con, cal = F.build()
    F.add_training_reactions(con, cal, n=30, abn=8.0)
    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", cal[250].isoformat())
    get_settings.cache_clear()
    patch_cfg(monkeypatch, **{"mining.min_n": 10, "mining.splits": ["none", "sector"]})
    # a post-cutoff wipe-out must not change what mining sees
    F.add_event(con, "late", cal[300])
    for w in (0, 1, 3, 5, 10):
        con.execute(
            "INSERT INTO event_reactions VALUES ('late', 'ABC', ?, ?, 'intraday', -90, -90, "
            "-90, -90, 'x', NULL, NULL, NULL, NULL, false, 1.0)",
            [w, cal[300]],
        )
    assert sources.mine(con, engine) >= 1
    with Session(engine) as s:
        h = s.scalars(select(LabHypothesis).where(LabHypothesis.source == "data_mining")).first()
        assert h is not None and h.origin_json["scanned"] >= 1 and h.origin_json["mean"] > 0
        assert "+8.00%" in h.rationale
        assert R.trial_population(s)[0] >= h.origin_json["scanned"]
    get_settings.cache_clear()


def test_system_evidence_iv_crush(engine: Engine) -> None:
    with Session(engine) as s:
        for i in range(4):
            s.add(
                PostMortem(
                    trade_id=i + 1,
                    primary_cause="iv_crush",
                    summary="x",
                    output_json={},
                    model="m",
                    prompt_ver="p",
                    created_at=NOW - timedelta(days=i),
                )
            )
        s.commit()
    assert sources.detect_evidence(engine, NOW) == 1
    with Session(engine) as s:
        h = s.scalars(select(LabHypothesis)).one()
        assert h.source == "system_evidence" and "IV crush" in h.rationale


class FakeConv:
    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer, self.sent = answer, []  # type: ignore[var-annotated]

    def send(self, text: str | None = None, tool_results: list[ToolResult] | None = None) -> Turn:
        self.sent.append(text)
        return Turn("", [ToolCall("s", "submit", self.answer)], "tool_use", Usage(20_000, 2_000))


class FakeProvider:
    name = "anthropic"

    def __init__(self, answer: dict[str, Any]) -> None:
        self.conv, self.started = FakeConv(answer), []  # type: ignore[var-annotated]

    def start(self, **kw: Any) -> FakeConv:
        self.started.append(kw)
        return self.conv


PROPOSAL = {
    "proposals": [
        {
            "name": "ai_block_deal_fii_drift",
            "rationale": "FII block buys reveal informed demand.",
            "expected_effect": "Calls after FII block buys earn +5% of premium.",
            "spec": {
                "name": "x_x",
                "source": "ai_proposal",
                "trigger": {"event_types": ["deal_buy_fii"], "direction": "bullish"},
            },
        }
    ]
}


def test_ai_proposals_flagged_and_capped(engine: Engine, cutoff) -> None:
    con, _ = F.build()
    prov = FakeProvider(PROPOSAL)
    assert sources.propose(con, engine, _ai(engine), prov, NOW) == 1
    assert prov.started[0]["model"] == "claude-sonnet-5-5"
    with Session(engine) as s:
        h = s.scalars(select(LabHypothesis)).one()
        assert h.source == "ai_proposal" and h.ai_memory_risk
        run = s.scalars(select(ResearchRun)).one()
        assert run.kind == "lab_hypothesis" and run.cost_inr > 0
    assert sources.propose(con, engine, _ai(engine), prov, NOW) == 0  # duplicate name


def test_lab_budget_caps_and_pause_first(engine: Engine) -> None:
    ai = _ai(engine)
    with Session(engine) as s:
        lab_ai.check_budget(s, ai, "lab_hypothesis", NOW)
        s.add(
            ResearchRun(
                kind="lab_hypothesis",
                role="lab_hypothesis",
                preset="lab",
                provider="a",
                model="m",
                status="ok",
                cost_inr=25.0,
                created_at=NOW,
            )
        )
        s.commit()
        with pytest.raises(BudgetExceededError) as e:  # lab daily cap Rs 30
            lab_ai.check_budget(s, ai, "lab_hypothesis", NOW)
        assert e.value.role == "strategy lab"
        lab_ai.check_budget(s, ai, "lab_summary", NOW)  # Rs 0.2 still fits
        s.add(AiUsage(ts=NOW, model="m", cost_inr=115.0))
        s.commit()
        with pytest.raises(BudgetExceededError) as e:  # 150 - 15 reserve - 20 headroom
            lab_ai.check_budget(s, ai, "lab_summary", NOW)
        assert "paused first" in e.value.role


def test_refinement_grid_then_ai(engine: Engine, cutoff, monkeypatch) -> None:
    patch_cfg(monkeypatch, **{"refine.deterministic_grid": [{"option": {"strike": "itm1"}}]})
    with Session(engine) as s:
        h = R.register(s, "ai_idea", "ai_proposal", "why", "what")
        sp = R.add_spec(
            s,
            h,
            sources.StrategySpec.model_validate(
                {
                    "name": "ai_idea",
                    "source": "ai_proposal",
                    "trigger": {"event_types": ["results"]},
                }
            ),
        )
        sp.stage = "rejected"
        s.commit()
    assert refine.grid_step(engine) == 1
    with Session(engine) as s:
        v2 = s.scalars(select(LabSpec).where(LabSpec.version == 2)).one()
        assert v2.parent_spec_id and "itm1" in v2.spec_yaml and v2.change_note.startswith("grid")
        v2.stage = "rejected"
        s.commit()
    assert refine.grid_step(engine) == 0  # grid exhausted
    answer = {
        "change_note": "Next open entry avoids the gap",
        "spec": {
            "name": "ai_idea",
            "source": "ai_proposal",
            "trigger": {"event_types": ["results"]},
            "entry": {"timing": "next_open"},
        },
    }
    assert refine.ai_step(engine, _ai(engine), FakeProvider(answer), NOW) == 1
    with Session(engine) as s:
        v3 = s.scalars(select(LabSpec).where(LabSpec.version == 3)).one()
        assert v3.change_note.startswith("AI:") and "next_open" in v3.spec_yaml


def test_nightly_needs_the_flag(engine: Engine) -> None:
    assert nightly.job(engine) == {"skipped": "feat.strategy_lab is off"}


def test_lab_roles_are_checked_against_allowed_models(monkeypatch) -> None:
    from app.ai import models

    assert models.resolved()["lab_hypothesis"] == "claude-sonnet-5-5"
    assert models.resolved()["lab_summary"] == "claude-haiku-5-5"
    monkeypatch.setenv("LAB_HYPOTHESIS_MODEL", "claude-opus-5-5")
    assert any(p.startswith("lab_hypothesis = claude-opus-5-5") for p in models.problems())


FORBIDDEN = (
    "app.trading.paper",
    "app.trading.engine",
    "app.market",
    "app.notify",
    "app.db.config_store",
    "app.analytics.github",
    "app.signals.service",
    "httpx",
)


def test_lab_cannot_reach_live_config_paper_engine_or_broker() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "lab"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [a.name for a in node.names]
                )
                for n in names:
                    assert not n.startswith(FORBIDDEN), f"{path.name} imports {n}"
            if isinstance(node, ast.Name) and node.id in ("Config", "set_config"):
                raise AssertionError(f"{path.name} touches the live config table")
            if (
                isinstance(node, ast.Attribute)
                and node.attr in ("write_text", "safe_dump")
                and path.name != "spec.py"
            ):
                raise AssertionError(f"{path.name}:{node.lineno} writes files / YAML")
    assert set(lab_ai.TOOLS) == {
        "get_event_stats",
        "get_hypotheses",
        "get_trials",
        "get_vocabulary",
    }


def test_nightly_end_to_end(engine: Engine, monkeypatch) -> None:
    from contextlib import contextmanager

    from app.db.config_store import set_config
    from app.db.session import session_scope

    con, cal = F.build()
    F.add_training_reactions(con, cal)
    F.add_event(con, "e1", cal[300])
    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", cal[320].isoformat())
    get_settings.cache_clear()
    patch_cfg(monkeypatch, **{"luck.placebo.runs": 2, "schedule.max_screens_per_night": 3})

    @contextmanager
    def fake_connect(*a: Any, **k: Any):
        yield con

    monkeypatch.setattr(nightly, "connect", fake_connect)
    monkeypatch.setattr(nightly, "history_ready", lambda c: (True, ""))
    with session_scope(engine) as s:
        set_config(s, "feat.strategy_lab", True)
    out = nightly.job(engine, _ai(engine), FakeProvider(PROPOSAL), NOW)
    assert out["seeded"] == 7 and out["proposed"] == 1 and out["screened"] == 3
    with Session(engine) as s:
        stages = [sp.stage for sp in s.scalars(select(LabSpec))]
        assert stages.count("rejected") == 3  # three screened tonight, the rest stay queued
        assert stages.count("incubating") == 1  # the news_core comparison book
    assert out["books_opened"] == 1 and out["eod_books"] == 1
    get_settings.cache_clear()
