"""Phase 5: multi-agent panel, routing caps, budget reserve, calibration, selection gates,
post-mortems, weekly digest."""

from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pytest
from sqlalchemy import Engine, select

from app.ai.client import AiClient, BudgetExceededError, compute_cost_inr
from app.ai.schemas import CaseOutput, PostMortemOutput
from app.calibration import fit as F
from app.calibration import service as calibration
from app.config import IST, Settings, get_settings
from app.db.models import (
    AgentRun,
    AiUsage,
    Alert,
    Analysis,
    CalibrationRecord,
    Calibrator,
    Digest,
    PaperTrade,
    PostMortem,
    RejectedCandidate,
    Signal,
)
from app.db.session import session_scope
from app.notify.digest import build, weekly_digest
from app.signals.options_stage import selection_rejection
from app.signals.service import run_signal_cycle
from app.sim.simulator import SimResult, blend_results
from tests.helpers import (
    FakeByType,
    FakeMessages,
    analyst_output,
    panel_outputs,
    seed_instruments,
    seed_news,
)

PANEL = dict(
    ai_model_analyst="mid-m",
    ai_model_postmortem="mid-m",
    ai_model_researcher="cheap-m",
    ai_model_novelty="cheap-m",
    ai_model_bull="mid-m",
    ai_model_bear="mid-m",
    ai_model_judge="strong-m",
)


def ai(engine: Engine, messages: Any, **kw: Any) -> AiClient:
    return AiClient(Settings(_env_file=None, **{**PANEL, **kw}), engine, messages)  # type: ignore[call-arg]


@pytest.fixture
def cutoff(monkeypatch):
    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", "2025-06-30")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---- prices and budget ------------------------------------------------------------------
def test_prices_come_from_config_per_model() -> None:
    # Sonnet 5.5 cache reads are $0.10/MTok (corrected); Opus 5.5 $4 / $20
    assert compute_cost_inr("bull", 0, 0, 1_000_000, 0, model="claude-sonnet-5-5") == pytest.approx(
        0.10 * 88
    )
    assert compute_cost_inr(
        "judge", 1_000_000, 1_000_000, 0, 0, model="claude-opus-5-5"
    ) == pytest.approx(24 * 88)
    # Haiku 5.5 long-context prices above 100k prompt tokens
    small = compute_cost_inr("tagger", 50_000, 0, 0, 0, model="claude-haiku-5-5")
    big = compute_cost_inr("tagger", 150_000, 0, 0, 0, model="claude-haiku-5-5")
    assert small == pytest.approx(0.05e-0 * 88 * 0.1) and big == pytest.approx(0.15 * 0.5 * 88)
    # unknown model id -> the role's tier
    assert compute_cost_inr("judge", 1_000_000, 0, 0, 0, model="some-new-model") == pytest.approx(
        2 * 88
    )


def _spend(engine: Engine, inr: float) -> None:
    with session_scope(engine) as s:
        s.add(
            AiUsage(
                ts=datetime.now(IST),
                model="m",
                input_tokens=1,
                output_tokens=1,
                cached_tokens=0,
                cost_inr=inr,
            )
        )


def test_budget_reserve_keeps_the_tagger_running(engine: Engine) -> None:
    client = ai(
        engine, FakeMessages(analyst_output()), ai_daily_budget_inr=100, ai_tagger_reserve_inr=15
    )
    _spend(engine, 86)
    with pytest.raises(BudgetExceededError) as exc:
        client.check_budget("judge")
    assert not exc.value.tagger_paused
    assert "Paused: multi-agent panel, single analyst and post-mortems" in exc.value.alert_text()
    assert "live news tagging (until the reserve is used)" in exc.value.alert_text()
    client.check_budget("tagger")  # Rs 86 < Rs 100: tagger still allowed
    _spend(engine, 14)
    with pytest.raises(BudgetExceededError) as exc:
        client.check_budget("tagger")
    assert exc.value.tagger_paused and "Paused: live news tagging too" in exc.value.alert_text()


# ---- panel and routing ------------------------------------------------------------------
def test_panel_runs_all_agents_and_stores_them(engine: Engine) -> None:
    seed_instruments(engine)
    (nid,) = seed_news(engine)
    fake = FakeByType(panel_outputs(nid))
    run_signal_cycle(engine, ai(engine, fake))
    with session_scope(engine) as s:
        an = s.scalars(select(Analysis)).one()
        assert (
            an.prediction_source == "panel"
            and an.prompt_ver == "panel_v1"
            and an.model == "strong-m"
        )
        runs = s.scalars(select(AgentRun).where(AgentRun.analysis_id == an.id)).all()
        assert [r.agent for r in runs] == ["researcher", "novelty", "bull", "bear", "judge"]
        assert all(r.cost_inr > 0 and r.input_tokens == 3000 for r in runs)
        assert an.scores_json["panel"]["winning_side"] == "bull"
        sig = s.scalars(select(Signal)).one()
        assert (
            sig.prediction_source == "panel"
            and sig.flags_json["feat.multi_agent"]["active"] is True
        )
        rec = s.scalars(select(CalibrationRecord)).one()
        assert rec.target == "p_move" and rec.source == "panel" and rec.cluster_key
    models = [c["model"] for c in fake.calls]
    assert models == ["cheap-m", "cheap-m", "mid-m", "mid-m", "strong-m"]
    # later agents see earlier outputs
    assert "BULL CASE" in fake.calls[-1]["messages"][0]["content"]


def test_panel_rejects_invented_citations(engine: Engine) -> None:
    seed_instruments(engine)
    (nid,) = seed_news(engine)
    outs = panel_outputs(nid)
    bad = outs[CaseOutput][0].model_copy(update={"cited_news_ids": [999]})
    outs[CaseOutput] = [bad, bad, bad]
    run_signal_cycle(engine, ai(engine, FakeByType(outs)))
    with session_scope(engine) as s:
        an = s.scalars(select(Analysis)).one()
        assert an.scores_json["failed"] and "unknown news ids" in an.thesis
        assert s.scalars(select(Signal)).first() is None
        assert {r.agent for r in s.scalars(select(AgentRun))} == {"researcher", "novelty"}


def test_daily_caps_route_panel_then_single_then_skip(engine: Engine) -> None:
    import re

    seed_instruments(engine, {"BHARTIARTL": 475, "TCS": 175, "SBIN": 750})
    for sym in ("BHARTIARTL", "TCS", "SBIN"):
        seed_news(engine, sym, 0.8, "nse_announcements", title=f"{sym} big news")

    def cited(kwargs: dict[str, Any]) -> int:
        return int(re.search(r"\[id=(\d+)\]", kwargs["messages"][0]["content"]).group(1))

    def per_call(schema: type, idx: int | None = None) -> Any:
        def make(kwargs: dict[str, Any]) -> Any:
            outs = panel_outputs(cited(kwargs))
            o = outs[schema]
            return o[idx] if idx is not None else o

        return make

    from app.ai.schemas import AnalystOutput, JudgeOutput, NoveltyOutput, ResearchOutput

    fake = FakeByType(
        {
            ResearchOutput: per_call(ResearchOutput),
            NoveltyOutput: per_call(NoveltyOutput),
            CaseOutput: lambda kw: panel_outputs(cited(kw))[CaseOutput][
                0 if "BULL advocate" in kw["system"][0]["text"] else 1
            ],
            JudgeOutput: per_call(JudgeOutput),
            AnalystOutput: lambda kw: analyst_output(cited_news_ids=[cited(kw)]),
        }
    )
    run_signal_cycle(
        engine, ai(engine, fake, ai_multi_agent_max_per_day=1, ai_single_analyst_max_per_day=1)
    )
    with session_scope(engine) as s:
        sources = sorted(a.prediction_source for a in s.scalars(select(Analysis)))
        assert sources == ["panel", "single"]
        rej = s.scalars(select(RejectedCandidate).where(RejectedCandidate.stage == "ai_cap")).one()
        assert "daily AI caps reached (panel 1/1, single 1/1)" in rej.reason


def test_without_panel_models_the_single_analyst_runs(engine: Engine) -> None:
    seed_instruments(engine)
    seed_news(engine)
    client = AiClient(
        Settings(_env_file=None, ai_model_analyst="m"), engine, FakeMessages(analyst_output())
    )  # type: ignore[call-arg]
    run_signal_cycle(engine, client)
    with session_scope(engine) as s:
        an = s.scalars(select(Analysis)).one()
        assert an.prediction_source == "single"
        assert [r.agent for r in s.scalars(select(AgentRun))] == ["analyst"]
        assert s.scalars(select(Signal)).one().flags_json["feat.multi_agent"]["active"] is False


# ---- calibration maths ------------------------------------------------------------------
def test_platt_recovers_an_overconfident_model() -> None:
    rng = np.random.default_rng(0)
    p_true = rng.uniform(0.2, 0.8, 4000)
    y = rng.random(4000) < p_true
    raw = 1 / (1 + np.exp(-2.0 * np.log(p_true / (1 - p_true))))  # model is overconfident
    a, b = F.platt_fit(raw, y.astype(float), np.ones(4000))
    assert a == pytest.approx(0.5, abs=0.06) and abs(b) < 0.08
    assert F.brier(F.platt_apply(raw, a, b), y.astype(float), np.ones(4000)) < F.brier(
        raw, y.astype(float), np.ones(4000)
    )


def test_isotonic_is_monotone_and_weighted() -> None:
    p = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    y = np.array([0, 1, 0, 0, 1, 1], dtype=float)
    kx, ky = F.isotonic_fit(p, y, np.ones(6))
    assert all(b >= a for a, b in zip(ky, ky[1:], strict=False))
    assert F.isotonic_apply(0.6, kx, ky) == pytest.approx(1 - F.EPS)


def test_cluster_weights_count_one_event_once() -> None:
    w = F.cluster_weights(["rbi"] * 10 + ["a", "b"])
    assert w.sum() == pytest.approx(3.0) and w[0] == pytest.approx(0.1)
    # ten correlated wins + two losses: weighted hit rate 1/3, not 10/12
    y = np.array([1.0] * 10 + [0.0, 0.0])
    assert np.average(y, weights=w) == pytest.approx(1 / 3)


def test_method_switches_on_independent_events() -> None:
    p = list(np.linspace(0.3, 0.8, 60))
    y = [i % 2 == 0 for i in range(60)]
    assert F.fit(p, y, [f"e{i}" for i in range(60)], isotonic_min=50).method == "isotonic"
    assert (
        F.fit(p, y, [f"e{i // 2}" for i in range(60)], isotonic_min=50).method == "platt"
    )  # 30 events


def test_weight_and_blend_are_linear() -> None:
    assert calibration.weight_for(0.7, 0.6, 0.5, 0.5) == pytest.approx(0.5)
    assert calibration.weight_for(0.7, 0.8, 0.5, 0.5) == 1.0  # clipped
    assert calibration.weight_for(None, None, 0.5, 0.42) == 0.42
    r = SimResult(
        20, 2000, 0.7, 0.4, 0.2, -5000, 3, -100, 2.0, 1.0, (1, 2, 3), {"T1": 0.7, "SL": 0.3}
    )
    m = SimResult(
        -10, -1000, 0.4, 0.2, 0.1, -6000, 4, -120, 2.0, 1.2, (1, 2, 3), {"T1": 0.4, "SL": 0.6}
    )
    b = blend_results(r, m, 0.25)
    assert b.ev_pct == pytest.approx(-2.5) and b.p_t1_before_sl == pytest.approx(0.475)
    assert b.max_loss_inr == -6000 and b.exit_mix["SL"] == pytest.approx(0.525)


# ---- calibration records, fitting and use ------------------------------------------------
def _records(
    engine: Engine, n: int, source: str, cluster_every: int = 1, when: datetime | None = None
) -> None:
    when = when or datetime(2026, 9, 1, 11, 0, tzinfo=IST)
    rng = np.random.default_rng(1)
    with session_scope(engine) as s:
        an = Analysis(
            symbol="TCS", thesis="t", direction="bullish", created_at=when, prediction_source=source
        )
        s.add(an)
        s.flush()
        for i in range(n):
            p = float(rng.uniform(0.3, 0.9))
            s.add(
                CalibrationRecord(
                    analysis_id=an.id,
                    target="p_t1_before_sl",
                    predicted=p,
                    source=source,
                    cluster_key=f"{source}:{an.id}:{i // cluster_every}",
                    outcome=bool(rng.random() < p * 0.7),
                    recorded_at=when,
                    resolved_at=when,
                )
            )


def test_fit_needs_cutoff_and_enough_independent_events(engine: Engine, monkeypatch) -> None:
    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", "")
    get_settings.cache_clear()
    _records(engine, 120, "panel")
    out = calibration.fit_all(engine)
    assert all("not fitted" in line and " 0 records" in line for line in out)  # R1: no cutoff
    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", "2025-06-30")
    get_settings.cache_clear()
    _records(engine, 120, "single", cluster_every=4)  # 120 records but only 30 independent events
    out = calibration.fit_all(engine)
    assert any(
        "p_t1_before_sl/single: 120 records, 30 events (< 50): not fitted" in line for line in out
    )
    with session_scope(engine) as s:
        cals = {
            (c.target, c.source): c
            for c in s.scalars(select(Calibrator).where(Calibrator.active.is_(True)))
        }
    assert cals[("p_t1_before_sl", "all")].method == "platt"  # 150 events < 200
    assert (
        cals[("p_t1_before_sl", "all")].n_events == 150
        and cals[("p_t1_before_sl", "all")].n_samples == 240
    )
    get_settings.cache_clear()


def test_platt_then_isotonic_and_source_specific_choice(engine: Engine, cutoff) -> None:
    _records(engine, 80, "panel")  # 80 events: platt
    _records(engine, 120, "single", cluster_every=4)  # 30 events: below 50 for its own fit
    calibration.fit_all(engine)
    with session_scope(engine) as s:
        cals = {
            (c.target, c.source): c
            for c in s.scalars(select(Calibrator).where(Calibrator.active.is_(True)))
        }
        assert (
            cals[("p_t1_before_sl", "panel")].method == "platt"
            and cals[("p_t1_before_sl", "panel")].n_events == 80
        )
        assert ("p_t1_before_sl", "single") not in cals
        assert (
            cals[("p_t1_before_sl", "all")].n_samples == 200
            and cals[("p_t1_before_sl", "all")].n_events == 110
        )
        assert (
            calibration.active(s, "p_t1_before_sl", "single").source == "all"
        )  # falls back to combined
        assert calibration.active(s, "p_t1_before_sl", "panel").source == "panel"
        pol = calibration.policy(s, "panel", 0.7)
        assert (
            pol.method == "platt" and pol.t1_cal and pol.move_cal is None and pol.move_weight == 0.5
        )
    _records(engine, 150, "panel")  # 230 panel events -> isotonic
    calibration.fit_all(engine)
    with session_scope(engine) as s:
        c = calibration.active(s, "p_t1_before_sl", "panel")
        assert c.method == "isotonic"
        assert (
            s.scalars(select(Calibrator).where(Calibrator.source == "panel")).all()[0].active
            is False
        )  # old version off


def test_records_before_the_cutoff_never_count(engine: Engine, cutoff) -> None:
    _records(engine, 100, "panel", when=datetime(2025, 5, 1, 11, 0, tzinfo=IST))
    out = calibration.fit_all(engine)
    assert all("not fitted" in line for line in out)


def test_resolve_t1_from_trade_and_counterfactual(engine: Engine) -> None:
    now = datetime.now(IST)
    with session_scope(engine) as s:
        a1 = Analysis(symbol="TCS", thesis="t", direction="bullish", created_at=now)
        a2 = Analysis(symbol="SBIN", thesis="t", direction="bearish", created_at=now)
        s.add_all([a1, a2])
        s.flush()
        sig = Signal(
            symbol="TCS", direction="bullish", score=80, status="ACTIVE", analysis_id=a1.id
        )
        s.add(sig)
        s.flush()
        s.add(
            PaperTrade(
                signal_id=sig.id,
                entry_ts=now,
                entry_premium=10,
                sl=5,
                t1=12.5,
                t2=15,
                t3=20,
                status="OPEN",
                state_json={"targets_hit": 1, "closed": False},
            )
        )
        s.add(
            RejectedCandidate(
                dedupe_key="x",
                ts=now,
                symbol="SBIN",
                stage="ev",
                reason="r",
                analysis_id=a2.id,
                ref_strike=500,
                ref_option_type="PE",
                outcome_status="resolved",
                details_json={"cf": {"state": {"targets_hit": 0, "closed": True}}},
            )
        )
        s.add(
            CalibrationRecord(
                analysis_id=a1.id,
                signal_id=sig.id,
                target="p_t1_before_sl",
                predicted=0.6,
                recorded_at=now,
            )
        )
        s.add(
            CalibrationRecord(
                analysis_id=a2.id, target="p_t1_before_sl", predicted=0.6, recorded_at=now
            )
        )
    assert calibration.resolve(engine) == 2
    with session_scope(engine) as s:
        outs = {
            r.analysis_id: (r.outcome, r.trade_id, r.rejection_id)
            for r in s.scalars(select(CalibrationRecord))
        }
    assert outs[a1.id][0] is True and outs[a1.id][1] is not None
    assert outs[a2.id][0] is False and outs[a2.id][2] is not None


def test_selection_gates() -> None:
    def r(ev: float, p: float) -> SimResult:
        return SimResult(ev, ev * 100, p, 0.3, 0.1, -5000, 3, -100, 2.0, 1.0, (1, 2, 3), {})

    assert selection_rejection(r(-1, 0.7))[0] == "ev"
    assert "minimum 10% of premium" in selection_rejection(r(8, 0.7))[1]
    assert selection_rejection(r(15, 0.54))[0] == "probability"
    assert selection_rejection(r(15, 0.55)) is None


# ---- post-mortems and digest ---------------------------------------------------------------
def _closed_trade(engine: Engine, source: str = "panel") -> int:
    now = datetime.now(IST)
    with session_scope(engine) as s:
        an = Analysis(
            symbol="TCS",
            thesis="Results beat",
            direction="bullish",
            created_at=now,
            prediction_source=source,
        )
        s.add(an)
        s.flush()
        sig = Signal(
            symbol="TCS",
            direction="bullish",
            score=80,
            status="CLOSED",
            analysis_id=an.id,
            prediction_source=source,
        )
        s.add(sig)
        s.flush()
        tr = PaperTrade(
            signal_id=sig.id,
            entry_ts=now - timedelta(days=2),
            entry_premium=10,
            sl=5,
            t1=12.5,
            t2=15,
            t3=20,
            status="CLOSED",
            exit_ts=now,
            exit_premium=4.6,
            pnl=-1500,
            pnl_pct=-54,
            symbol="TCS",
            exit_reason="SL",
            slippage_inr=-300,
            fillability="fillable",
            lots=3,
            lot_size=175,
        )
        s.add(tr)
        s.flush()
        return tr.id


def test_post_mortem_once_per_closed_trade(engine: Engine) -> None:
    from app.tracking.postmortem import run_post_mortems

    tid = _closed_trade(engine)
    out = PostMortemOutput(
        primary_cause="slippage_gap",
        secondary_causes=["iv_crush"],
        thesis_correct=False,
        summary="Gapped through the stop.",
        what_went_right=[],
        what_went_wrong=["gap"],
        lessons=["x"],
    )
    client = ai(engine, FakeByType({PostMortemOutput: out}))
    assert run_post_mortems(engine, client) == 1 and run_post_mortems(engine, client) == 0
    with session_scope(engine) as s:
        pm = s.scalars(select(PostMortem)).one()
        assert (
            pm.trade_id == tid
            and pm.primary_cause == "slippage_gap"
            and pm.prediction_source == "panel"
        )
        assert pm.model == "mid-m" and pm.cost_inr > 0


def test_post_mortems_pause_first_when_budget_is_near(engine: Engine) -> None:
    from app.tracking.postmortem import run_post_mortems

    _closed_trade(engine)
    _spend(engine, 90)
    client = ai(engine, FakeByType({}), ai_daily_budget_inr=100, ai_tagger_reserve_inr=15)
    assert run_post_mortems(engine, client) == 0
    with session_scope(engine) as s:
        assert "post-mortems" in s.scalars(select(Alert)).one().text


def test_weekly_digest_reports_sources_and_is_sent_once(engine: Engine, cutoff) -> None:
    _closed_trade(engine, "panel")
    _closed_trade(engine, "single")
    _records(engine, 60, "panel", when=datetime.now(IST))
    now = datetime.now(IST)
    with session_scope(engine) as s:
        text, data = build(s, now.date() - timedelta(days=now.weekday()))
    assert "Panel: 1 trades" in text and "Single analyst: 1 trades" in text
    assert "Gap/slippage vs trigger levels: Rs -600" in text
    assert "p_t1_before_sl: all: 60 records / 60.0 events" in text
    assert "NOT YET CALIBRATED" in text and "SUGGESTIONS (not applied" in text
    assert data["trades"]["excluding_unfillable"]["n"] == 2
    assert weekly_digest(engine) == 1 and weekly_digest(engine) == 0
    with session_scope(engine) as s:
        assert s.scalars(select(Digest)).one().text.startswith("WEEKLY REVIEW")


def test_why_shows_calibration_method_and_source() -> None:
    from app.notify.explain import calibration_lines

    sig = Signal(symbol="TCS", direction="bullish", score=80, status="NEW", shrink_weight=0.5)
    assert (
        "NOT YET CALIBRATED: shrunk toward the market (50% AI / 50% market)"
        in calibration_lines(sig, None)[0]
    )
    sig2 = Signal(
        symbol="TCS",
        direction="bullish",
        score=80,
        status="NEW",
        calibrator_id=3,
        calibration_method="isotonic",
        ai_weight=0.42,
    )
    line = calibration_lines(
        sig2, {"method": "isotonic", "t1_calibrator": "isotonic (panel, 230 events)"}
    )[0]
    assert (
        "Calibrated with isotonic: isotonic (panel, 230 events); AI share of simulated paths 42%"
        in line
    )


def test_calibration_command(engine: Engine, cutoff) -> None:
    from app.notify.telegram import _calibration

    _records(engine, 60, "panel", when=datetime.now(IST))
    calibration.fit_all(engine)
    with session_scope(engine) as s:
        text = _calibration(s)
    assert (
        "p_t1_before_sl: all 60 rec / 60.0 events" in text
        and "active p_t1_before_sl/panel: platt v1" in text
    )


__all__ = ["date"]


def test_options_stage_uses_the_calibrated_weight(engine: Engine, no_selection_gates) -> None:
    from app.calibration.service import ActiveCalibrator, Policy
    from app.signals.options_stage import plan_options
    from tests.test_options_stage import FakeSource

    seed_instruments(engine)
    cal = ActiveCalibrator(1, "p_t1_before_sl", "all", "platt", {"a": 0.5, "b": -0.3}, 80, 80, 0.2)
    pol = Policy(0.5, "platt", None, cal, 0.5)
    src = FakeSource()
    now = datetime.now(IST)
    plan = plan_options(
        src,
        "BHARTIARTL",
        "bullish",
        analyst_output().scenarios,
        2.85,
        3,
        now,
        475,
        now,
        engine,
        pol,
    )
    assert plan.calibration_method == "platt" and plan.calibrator_id == 1 and plan.chosen
    priced = [c for c in plan.candidates if c.result]
    for c in priced:
        expected = cal.apply(c.raw.p_t1_before_sl)
        lo, hi = sorted((c.market.p_t1_before_sl, c.raw.p_t1_before_sl))
        if lo <= expected <= hi:  # reachable by mixing: reproduced exactly
            assert c.result.p_t1_before_sl == pytest.approx(expected, abs=1e-9)
        assert 0.0 <= c.ai_weight <= 1.0 and c.p_t1_calibrated == pytest.approx(expected)
    assert plan.raw_result is plan.chosen.raw
