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


def test_blank_env_values_use_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_DAILY_BUDGET_INR", "")
    monkeypatch.setenv("NEWS_POLL_MIN", "")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.ai_daily_budget_inr == 150
    assert s.ai_monthly_budget_inr == 2500
    assert s.news_poll_min == 5


def test_env_example_has_keys_only() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            assert line.split("=", 1)[1] == "", f"value present: {line}"
