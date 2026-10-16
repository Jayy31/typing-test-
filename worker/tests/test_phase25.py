"""Phase 2.5: taxonomy, batch tagging, flags, collectors, options, store, V2 fields."""

import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, select

from app import flags, taxonomy
from app.ai.client import AiClient, compute_cost_inr, estimate_cost
from app.ai.schemas import Scenario, TaggerOutput
from app.ai.tagger import (
    TagInput,
    clean_output,
    collect_batch,
    load_context,
    submit_batch,
    system_prompt,
)
from app.ai.tagger import estimate as tag_estimate
from app.collectors.base import Collector, source_health
from app.config import IST, Settings
from app.db.config_store import set_config
from app.db.models import AiUsage, Signal
from app.db.models import Scenario as ScenarioRow
from app.db.session import session_scope
from app.history.store import StoreBusyError, connect
from app.market.options import OptionChain, OptionQuoteRow, StaticOptionData
from app.signals.prediction import from_scenarios, scenario_expected_move
from app.signals.service import run_signal_cycle
from tests.helpers import FakeMessages, analyst_output, seed_instruments, seed_news


# ---- taxonomy ------------------------------------------------------------------------
def test_taxonomy_drives_schema_and_prompt() -> None:
    enum = TaggerOutput.model_json_schema()["properties"]["event_type"]["enum"]
    assert enum == taxonomy.event_types() and "business_update" in enum
    assert taxonomy.version() in system_prompt() and "order_win" in system_prompt()
    assert "{taxonomy}" not in system_prompt()


def test_unknown_event_type_and_theme_are_normalised() -> None:
    out = TaggerOutput(
        impacts=[],
        themes=["crude", "made_up"],
        event_type="weird",
        novelty=0.5,
        is_actionable=False,
        reason="r",
    )
    assert out.event_type == "other"
    assert clean_output(out, set(), set()).themes == ["crude"]


# ---- batch tagging -------------------------------------------------------------------
class FakeBatches:
    def __init__(self, results: list[Any]) -> None:
        self.created: list[dict[str, Any]] = []
        self._results = results

    def create(self, **kwargs: Any) -> Any:
        self.created.append(kwargs)
        return SimpleNamespace(id="batch_1")

    def retrieve(self, batch_id: str) -> Any:
        return SimpleNamespace(processing_status="ended")

    def results(self, batch_id: str) -> Any:
        return iter(self._results)


def _ok(custom_id: str, payload: dict[str, Any]) -> Any:
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=200,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    msg = SimpleNamespace(
        usage=usage, content=[SimpleNamespace(type="text", text=json.dumps(payload))]
    )
    return SimpleNamespace(
        custom_id=custom_id, result=SimpleNamespace(type="succeeded", message=msg)
    )


def test_batch_mode_same_schema_half_price(engine: Engine) -> None:
    seed_instruments(engine)
    good = {
        "impacts": [{"symbol": "TCS", "sector": None, "direction": "bullish", "sentiment": 0.6}],
        "themes": ["rates"],
        "event_type": "results",
        "novelty": 0.8,
        "is_actionable": True,
        "reason": "beat",
    }
    errored = SimpleNamespace(custom_id="e3", result=SimpleNamespace(type="errored"))
    bad = _ok("e2", {"nonsense": True})
    fb = FakeBatches([_ok("e1", good), bad, errored])
    ai = AiClient(
        Settings(_env_file=None, ai_model_tagger="cheap"),
        engine,  # type: ignore[call-arg]
        FakeMessages(None),
        fb,
    )
    with session_scope(engine) as s:
        ctx = load_context(s)
    inputs = [TagInput("e1", "TCS Q2 beats", None, "hist"), TagInput("e2", "x", None, "hist")]
    assert submit_batch(ai, inputs, ctx) == "batch_1"
    req = fb.created[0]["requests"][0]
    assert req["custom_id"] == "e1"
    assert req["params"]["output_config"]["format"]["type"] == "json_schema"
    assert req["params"]["system"][0]["cache_control"] == {"type": "ephemeral"}
    out = collect_batch(ai, "batch_1", ctx)
    assert out["e1"] is not None and out["e1"].impacts[0].symbol == "TCS"
    assert out["e2"] is None and out["e3"] is None
    with session_scope(engine) as s:
        costs = [u.cost_inr for u in s.scalars(select(AiUsage))]
    full = compute_cost_inr("tagger", 1000, 200, 0, 0)
    assert costs and all(c == pytest.approx(full / 2) for c in costs)


def test_cost_estimate_before_one_off_jobs() -> None:
    est = tag_estimate(100_000)
    assert est.batch and est.items == 100_000 and est.inr > 0
    assert "Batch API" in est.summary() and "Rs" in est.summary()
    live = estimate_cost("tagger", 100_000, 1400, 250, batch=False)
    assert live.inr == pytest.approx(est.inr * 2)


def test_roles_map_to_env_models() -> None:
    s = Settings(_env_file=None, ai_model_judge="strong-model")  # type: ignore[call-arg]
    ai = AiClient(s, None, FakeMessages(None))
    assert ai.model_for("judge") == "strong-model"
    with pytest.raises(RuntimeError, match="AI_MODEL_BULL"):
        ai.model_for("bull")
    with pytest.raises(ValueError):
        ai.model_for("reviewer")


# ---- collectors ----------------------------------------------------------------------
class _Fake(Collector[int]):
    name = "fake_source"

    def __init__(self, fail: bool) -> None:
        self.fail = fail

    def fetch(self) -> Any:
        if self.fail:
            raise TimeoutError("slow")
        return [1, 2, 3]

    def parse(self, raw: Any) -> list[int]:
        return list(raw)

    def store(self, items: list[int], engine: Engine | None) -> int:
        return len(items)


def test_collector_base_records_source_health(engine: Engine) -> None:
    assert _Fake(False).run(engine) == 3
    for _ in range(2):
        with pytest.raises(TimeoutError):
            _Fake(True).run(engine)
    h = source_health(engine)["fake_source"]
    assert h["consecutive_failures"] == 2 and "TimeoutError" in h["last_error"] and h["last_ok"]
    _Fake(False).run(engine)
    assert source_health(engine)["fake_source"]["consecutive_failures"] == 0


# ---- option data interface -----------------------------------------------------------
def _chain(day: date, spot: float) -> OptionChain:
    ts = datetime(day.year, day.month, day.day, 15, 0, tzinfo=IST)
    rows = tuple(
        OptionQuoteRow("TCS", date(2026, 10, 27), k, t, 9.0, 10.0, 9.5, 30.0, 1000, 500, spot, ts)
        for k in (3900.0, 4000.0, 4100.0)
        for t in ("CE", "PE")
    )
    return OptionChain("TCS", date(2026, 10, 27), spot, ts, rows)


def test_static_option_data_has_no_look_ahead() -> None:
    src = StaticOptionData(
        [
            (date(2026, 10, 5), _chain(date(2026, 10, 5), 3990)),
            (date(2026, 10, 6), _chain(date(2026, 10, 6), 4080)),
        ]
    )
    assert src.spot("TCS", as_of=date(2026, 10, 5)) == 3990
    assert src.spot("TCS", as_of=date(2026, 10, 6)) == 4080
    assert src.spot("TCS", as_of=date(2026, 10, 4)) is None
    chain = src.chain("TCS", date(2026, 10, 27), as_of=date(2026, 10, 5))
    assert chain is not None and chain.atm_strike() == 4000.0
    row = chain.get(4000.0, "CE")
    assert row is not None and row.mid == 9.5 and round(row.spread_pct or 0, 2) == 10.53


# ---- history store -------------------------------------------------------------------
def test_history_store_schema_and_single_writer(tmp_path: Path) -> None:
    db = tmp_path / "h.duckdb"
    with connect(db, write=True) as con:
        con.execute("INSERT INTO hist_prices VALUES ('2026-10-05','TCS',1,2,0.5,1.5,100,40)")
        with pytest.raises(StoreBusyError):
            with connect(db, write=True):
                pass
    with connect(db) as con:
        tables = {
            r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        }
        assert {
            "hist_prices",
            "hist_options",
            "hist_events",
            "event_reactions",
            "analogue_stats",
        } <= tables
        assert con.execute("SELECT count(*) FROM hist_prices").fetchone() == (1,)


# ---- flags + V2 signal fields --------------------------------------------------------
def test_flags_default_on_but_inactive_until_built(engine: Engine) -> None:
    with session_scope(engine) as s:
        snap = flags.snapshot(s)
        assert snap["feat.multi_agent"] == {"enabled": True, "built": False, "active": False}
        assert snap["feat.simulator"] == {"enabled": True, "built": True, "active": True}
        set_config(s, "feat.multi_agent", False)
        assert flags.enabled(s, "feat.multi_agent") is False


def test_scenarios_must_sum_to_one() -> None:
    sc = [
        Scenario(
            name="a",
            probability=0.5,
            stock_move_pct=3,
            timing_days=2,
            triggers=[],
            invalidation="x",
        ),
        Scenario(
            name="b",
            probability=0.3,
            stock_move_pct=-1,
            timing_days=2,
            triggers=[],
            invalidation="y",
        ),
    ]
    with pytest.raises(ValidationError, match="sum"):
        analyst_output(scenarios=sc)


def test_prediction_from_scenarios() -> None:
    sc = analyst_output().scenarios  # 0.6*5 + 0.3*0.5 + 0.1*-3 = 2.85
    assert scenario_expected_move(sc) == pytest.approx(2.85)
    bull = from_scenarios(sc, "bullish")
    bear = from_scenarios(sc, "bearish")
    assert (
        bull is not None and bull.move_pct == pytest.approx(2.85) and bull.source == "scenario_tree"
    )
    assert bull.p_move == pytest.approx(0.9)
    assert (
        bear is not None
        and bear.move_pct == pytest.approx(-2.85)
        and bear.p_move == pytest.approx(0.1)
    )
    assert from_scenarios(sc, "neutral") is None


def test_signal_gets_v2_stamp_scenarios_and_honest_labels(engine: Engine) -> None:
    seed_instruments(engine)
    seed_news(engine)
    ai = AiClient(
        Settings(_env_file=None, ai_model_analyst="m"),
        engine,  # type: ignore[call-arg]
        FakeMessages(analyst_output()),
    )
    run_signal_cycle(engine, ai)
    with session_scope(engine) as s:
        sig = s.scalars(select(Signal)).one()
        assert sig.engine_version == flags.ENGINE_VERSION
        assert sig.flags_json and sig.flags_json["feat.multi_agent"]["active"] is False
        assert sig.pricing_status == "pending"  # no broker token in tests -> graceful degradation
        assert (
            sig.predicted_move_pct == pytest.approx(2.85)
            and sig.predicted_move_source == "scenario_tree"
        )
        assert sig.impact_order == 1 and sig.calibrator_id is None
        assert len(s.scalars(select(ScenarioRow)).all()) == 3
        from app.db.models import Alert

        text = s.scalars(select(Alert)).one().text
    assert "Predicted move: +2.9% (source: scenario tree)" in text
    assert "not yet calibrated" in text
    from tests.test_notify import Recorder, bot

    why = bot(engine, Recorder()).reply_for("/why S-1")
    for section in (
        "PREDICTION",
        "NOT YET CALIBRATED",
        "SCENARIO TREE",
        "60%  hike sticks",
        "ENGINE",
    ):
        assert section in why, section
