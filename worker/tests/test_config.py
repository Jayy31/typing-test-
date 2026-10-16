import pytest

from app.config import Settings


def test_defaults_match_design() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.signal_score_threshold == 65
    assert s.news_poll_min == 5
    assert s.option_poll_active_min == 2
    assert s.tz == "Asia/Kolkata"


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_DAILY_BUDGET_INR", "50")
    monkeypatch.setenv("AI_MODEL_TAGGER", "some-model-id")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.ai_daily_budget_inr == 50
    assert s.ai_model_tagger == "some-model-id"
