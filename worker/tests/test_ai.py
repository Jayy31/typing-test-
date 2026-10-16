from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine, select

from app.ai.client import AiClient, AiOutputError, BudgetExceededError, compute_cost_inr
from app.ai.schemas import Impact, TaggerOutput
from app.ai.tagger import clean_output, tag_items
from app.collectors.fno_meta import SYMBOL_MASTER, parse_symbol_master, sync_instruments
from app.config import IST, Settings
from app.db.models import AiUsage, NewsItem, NewsTag
from app.db.session import session_scope


class FakeMessages:
    def __init__(self, output: TaggerOutput | None, stop_reason: str = "end_turn") -> None:
        self.output, self.stop_reason, self.calls = output, stop_reason, []  # type: ignore[var-annotated]

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        usage = SimpleNamespace(
            input_tokens=1000,
            output_tokens=100,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        return SimpleNamespace(parsed_output=self.output, stop_reason=self.stop_reason, usage=usage)


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, ai_model_tagger="tagger-model", **kw)  # type: ignore[call-arg]


OUT = TaggerOutput(
    impacts=[Impact(symbol="BHARTIARTL", sector=None, direction="bullish", sentiment=0.7)],
    themes=["tariffs"],
    event_type="policy",
    novelty=0.9,
    is_actionable=True,
    reason="tariff hike",
)


def test_cost_uses_tier_prices() -> None:
    # tagger: $0.10/M in, $0.50/M out, 88 INR/USD
    cost = compute_cost_inr("tagger", 1_000_000, 1_000_000, 0, 0)
    assert cost == pytest.approx((0.10 + 0.50) * 88.0)


def test_parse_records_usage_and_caches_system_prompt(engine: Engine) -> None:
    fake = FakeMessages(OUT)
    ai = AiClient(settings(), engine, fake)
    out = ai.parse("tagger", "SYS", "USER", TaggerOutput, 700, thinking={"type": "disabled"})
    assert out.event_type == "policy"
    call = fake.calls[0]
    assert call["model"] == "tagger-model"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["thinking"] == {"type": "disabled"}
    with session_scope(engine) as s:
        (row,) = s.scalars(select(AiUsage))
        assert row.model == "tagger-model" and row.cost_inr > 0


def test_budget_blocks_calls(engine: Engine) -> None:
    ai = AiClient(settings(ai_daily_budget_inr=0.0001), engine, FakeMessages(OUT))
    ai.parse("tagger", "S", "U", TaggerOutput, 100)  # first call allowed (spend is 0)
    with pytest.raises(BudgetExceededError):
        ai.parse("tagger", "S", "U", TaggerOutput, 100)


def test_monthly_budget_counts_earlier_days(engine: Engine) -> None:
    with session_scope(engine) as s:
        s.add(
            AiUsage(ts=datetime.now(IST).replace(day=1, hour=0, minute=1), model="m", cost_inr=50)
        )
    ai = AiClient(
        settings(ai_monthly_budget_inr=40, ai_daily_budget_inr=1000), engine, FakeMessages(OUT)
    )
    with pytest.raises(BudgetExceededError, match="monthly"):
        ai.check_budget(datetime.now(IST).replace(day=15))


def test_refusal_and_missing_output_raise(engine: Engine) -> None:
    with pytest.raises(AiOutputError):
        AiClient(settings(), engine, FakeMessages(None, "refusal")).parse(
            "tagger", "S", "U", TaggerOutput, 9
        )
    with pytest.raises(AiOutputError):
        AiClient(settings(), engine, FakeMessages(None)).parse("tagger", "S", "U", TaggerOutput, 9)


def test_missing_model_env_is_clear_error(engine: Engine) -> None:
    ai = AiClient(Settings(_env_file=None), engine, FakeMessages(OUT))  # type: ignore[call-arg]
    with pytest.raises(RuntimeError, match="AI_MODEL_TAGGER"):
        ai.parse("tagger", "S", "U", TaggerOutput, 9)


def test_clean_output_drops_invented_symbols() -> None:
    bad = OUT.model_copy(
        update={
            "impacts": [
                Impact(symbol="FAKECO", sector=None, direction="bullish", sentiment=0.5),
                Impact(symbol="BHARTIARTL", sector=None, direction="bullish", sentiment=0.5),
                Impact(symbol=None, sector="Telecom", direction="bullish", sentiment=0.4),
                Impact(symbol=None, sector="Made Up", direction="bullish", sentiment=0.4),
            ]
        }
    )
    kept = clean_output(bad, {"BHARTIARTL"}, {"Telecom"})
    assert [(i.symbol, i.sector) for i in kept.impacts] == [("BHARTIARTL", None), (None, "Telecom")]


def test_tag_items_stores_tags_and_statuses(engine: Engine) -> None:
    rows = parse_symbol_master(SYMBOL_MASTER.read_text(encoding="utf-8"))
    sync_instruments(rows, {"BHARTIARTL": 475}, engine)
    with session_scope(engine) as s:
        s.add(
            NewsItem(
                url_hash="h1", source="x", title="Airtel hikes tariffs", url="u1", status="relevant"
            )
        )
        s.add(NewsItem(url_hash="h2", source="x", title="Other", url="u2", status="dropped"))
    ai = AiClient(settings(), engine, FakeMessages(OUT))
    assert tag_items(engine, ai) == 1
    with session_scope(engine) as s:
        tag = s.scalars(select(NewsTag)).one()
        assert (tag.symbol, tag.direction, tag.prompt_ver) == ("BHARTIARTL", "bullish", "tagger_v1")
        assert s.scalars(select(NewsItem).where(NewsItem.url_hash == "h1")).one().status == "tagged"
    # not actionable -> no tag rows
    with session_scope(engine) as s:
        s.add(
            NewsItem(url_hash="h3", source="x", title="Airtel opinion", url="u3", status="relevant")
        )
    na = OUT.model_copy(update={"is_actionable": False})
    assert tag_items(engine, AiClient(settings(), engine, FakeMessages(na))) == 1
    with session_scope(engine) as s:
        assert (
            s.scalars(select(NewsItem).where(NewsItem.url_hash == "h3")).one().status
            == "not_actionable"
        )
        assert len(s.scalars(select(NewsTag)).all()) == 1


def test_long_theme_list_fits_column(engine: Engine) -> None:
    rows = parse_symbol_master(SYMBOL_MASTER.read_text(encoding="utf-8"))
    sync_instruments(rows, {"BHARTIARTL": 475}, engine)
    with session_scope(engine) as s:
        s.add(NewsItem(url_hash="h9", source="x", title="Airtel", url="u9", status="relevant"))
    long = OUT.model_copy(update={"themes": [f"theme_number_{i}_long_name" for i in range(10)]})
    tag_items(engine, AiClient(settings(), engine, FakeMessages(long)))
    with session_scope(engine) as s:
        assert len(s.scalars(select(NewsTag)).one().theme or "") <= 64
