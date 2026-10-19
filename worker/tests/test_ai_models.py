import pytest

from app.ai import models
from app.ai.client import MODEL_ROLE
from app.config import Settings

pytestmark = pytest.mark.real_ai_defaults


def _st(**kw: str) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_defaults_haiku_and_sonnet_only() -> None:
    st = _st()
    res = models.resolved(st)
    assert res["AI_MODEL_ANALYST"] == "claude-sonnet-5-5"
    assert res["AI_MODEL_JUDGE"] == "claude-sonnet-5-5"
    for r in ("TAGGER", "RESEARCHER", "NOVELTY", "BULL", "BEAR", "POSTMORTEM", "RESULTS_FALLBACK"):
        assert res[f"AI_MODEL_{r}"] == "claude-haiku-5-5"
    assert st.ai_model_knowledge_cutoff == "2026-06-30"
    assert models.problems(st) == []


def test_postmortem_and_results_fallback_have_own_models() -> None:
    assert MODEL_ROLE["extractor_fallback"] == "results_fallback"
    assert "postmortem" not in MODEL_ROLE


def test_opus_role_is_flagged() -> None:
    found = models.problems(_st(ai_model_judge="claude-opus-5-5"))
    assert found == ["AI_MODEL_JUDGE = claude-opus-5-5 is not in AI_ALLOWED_MODELS"]
    allowed = "claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5"
    assert models.problems(_st(ai_model_judge="claude-opus-5-5", ai_allowed_models=allowed)) == []


def test_opus_preset_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.research import config as rcfg

    monkeypatch.setattr(rcfg, "get_settings", lambda: _st(research_agent_preset="full"))
    found = models.problems(_st())
    assert found and all("claude-opus-5-5" in f for f in found)
