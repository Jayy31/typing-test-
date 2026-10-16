from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select

from app.ai.analyst import validate_output
from app.ai.client import AiClient, AiOutputError
from app.config import IST, Settings
from app.db.config_store import set_config
from app.db.models import Alert, Analysis, EventCalendar, Signal, SignalVersion
from app.db.session import session_scope
from app.signals.candidates import build_candidates, weighted_sentiment
from app.signals.filters import check_eligibility
from app.signals.lifecycle import apply_analysis, expire_stale
from app.signals.scoring import compute_score
from app.signals.service import run_signal_cycle, sector_watch
from tests.helpers import FakeMessages, analyst_output, seed_instruments, seed_news

NOW = datetime(2026, 10, 15, 11, 0, tzinfo=IST)


def make_ai(engine: Engine, *outputs: Any) -> AiClient:
    settings = Settings(_env_file=None, ai_model_analyst="analyst-model")  # type: ignore[call-arg]
    return AiClient(settings, engine, FakeMessages(*outputs))


# ---- candidates ------------------------------------------------------------------------
def test_weighted_sentiment() -> None:
    assert weighted_sentiment([(1.0, 1.0), (1.0, -1.0)]) == 0.0
    assert weighted_sentiment([(3.0, 1.0), (1.0, -1.0)]) == 0.5
    assert weighted_sentiment([]) == 0.0


def test_candidate_needs_strength_and_corroboration(engine: Engine) -> None:
    seed_instruments(engine)
    seed_news(engine, "BHARTIARTL", 0.8, "nse_announcements")  # official -> qualifies
    seed_news(engine, "TCS", 0.8, "economic_times_markets")  # one strong source
    seed_news(engine, "SBIN", 0.3, "livemint_markets", title="SBI small news")  # weak -> dropped
    with session_scope(engine) as s:
        cands = build_candidates(s, datetime.now(IST))
    assert {c.symbol for c in cands} == {"BHARTIARTL", "TCS"}
    assert all(c.symbol != "SBIN" for c in cands)


def test_single_weak_unofficial_source_is_dropped(engine: Engine) -> None:
    seed_instruments(engine)
    seed_news(
        engine, "TCS", 0.4, "livemint_markets"
    )  # strength .36: above min but single, not strong
    with session_scope(engine) as s:
        assert build_candidates(s, datetime.now(IST)) == []


# ---- scoring ---------------------------------------------------------------------------
def test_score_is_weighted_sum_plus_rules(engine: Engine) -> None:
    seed_instruments(engine)
    seed_news(engine)
    with session_scope(engine) as s:
        (cand,) = build_candidates(s, datetime.now(IST))
    sc = compute_score(analyst_output(), cand, [])
    # .25*80 + .20*70 + .15*80 + .15*75 + .10*100 + .10*70 + .05*50
    assert sc.ai_total == 76.75 and sc.rules_adjustment == 0 and sc.final == 76.75
    assert sc.components["source_quality"] == 100.0


# ---- eligibility -----------------------------------------------------------------------
def test_eligibility_gates(engine: Engine) -> None:
    seed_instruments(engine)
    today, expiry = date(2026, 10, 5), date(2026, 10, 27)
    with session_scope(engine) as s:
        ok = check_eligibility(s, "TCS", 3, expiry, today, set())
        assert all(c.passed for c in ok)
        assert not all(
            c.passed for c in check_eligibility(s, "INFY", 3, expiry, today, set())
        )  # not F&O
        set_config(s, "fno_ban", {"date": "2026-10-05", "symbols": ["TCS"]})
        s.add(EventCalendar(symbol="SBIN", event_type="split", event_date=date(2026, 10, 20)))
        s.add(EventCalendar(symbol="TCS", event_type="dividend", event_date=date(2026, 10, 20)))
    with session_scope(engine) as s:
        banned = {c.name: c for c in check_eligibility(s, "TCS", 3, expiry, today, set())}
        assert not banned["not_in_ban"].passed and banned["no_contract_adjusting_action"].passed
        split = {c.name: c for c in check_eligibility(s, "SBIN", 3, expiry, today, set())}
        assert not split["no_contract_adjusting_action"].passed
        late = {c.name: c for c in check_eligibility(s, "SBIN", 3, date(2026, 10, 9), today, set())}
        assert not late["enough_days_to_expiry"].passed


# ---- lifecycle -------------------------------------------------------------------------
def step(
    s: Any, option: str | None, score: float, eligible: bool = True, t: datetime = NOW
) -> list[str]:
    ev = apply_analysis(
        s,
        symbol="TCS",
        sector="IT",
        option=option,
        score=score,
        eligible=eligible,
        threshold=65,
        expiry=date(2026, 10, 27),
        analysis_id=1,
        reason="why",
        now=t,
    )
    return [e.kind for e in ev]


def test_lifecycle_sequence(engine: Engine) -> None:
    with session_scope(engine) as s:
        assert step(s, "CALL", 60) == []  # below threshold: no signal
        assert step(s, "CALL", 70) == ["new"]
        assert step(s, "CALL", 72) == []  # small change: NEW -> ACTIVE silently
        assert s.scalars(select(Signal)).one().status == "ACTIVE"
        assert step(s, "CALL", 80) == ["upgraded"]
        assert step(s, "CALL", 70) == ["downgraded"]
        assert step(s, "CALL", 60) == ["downgraded"]  # below 65 but above 55: stays live
        assert s.scalars(select(Signal)).one().status == "DOWNGRADED"
        assert step(s, "CALL", 50) == ["cancelled"]  # below cancel line
        assert step(s, "CALL", 70) == ["new"]  # a fresh signal can follow
        versions = s.scalars(select(SignalVersion)).all()
        assert len(versions) >= 6


def test_direction_flip_cancels_and_opens_new(engine: Engine) -> None:
    with session_scope(engine) as s:
        step(s, "CALL", 75)
        assert step(s, "PUT", 78) == ["cancelled", "new"]
        live = s.scalars(select(Signal).where(Signal.status == "NEW")).all()
        assert [g.direction for g in live] == ["PUT"]


def test_ineligible_or_neutral_cancels(engine: Engine) -> None:
    with session_scope(engine) as s:
        step(s, "CALL", 75)
        assert step(s, "CALL", 75, eligible=False) == ["cancelled"]
        step(s, "CALL", 75)
        assert step(s, None, 40) == ["cancelled"]


def test_ineligible_never_creates(engine: Engine) -> None:
    with session_scope(engine) as s:
        assert step(s, "CALL", 90, eligible=False) == []
        assert s.scalars(select(Signal)).all() == []


def test_expire_stale_is_quiet(engine: Engine) -> None:
    with session_scope(engine) as s:
        step(s, "CALL", 75, t=NOW - timedelta(days=10))
        assert expire_stale(s, NOW) == 1
        assert s.scalars(select(Signal)).one().status == "CANCELLED"


# ---- analyst validation ----------------------------------------------------------------
def test_validate_output_rejects_unsupported_claims() -> None:
    validate_output(analyst_output(cited_news_ids=[1]), {1, 2})
    with pytest.raises(AiOutputError, match="unknown"):
        validate_output(analyst_output(cited_news_ids=[1, 99]), {1, 2})
    with pytest.raises(AiOutputError, match="no news"):
        validate_output(analyst_output(cited_news_ids=[]), {1})


# ---- full cycle ------------------------------------------------------------------------
def test_cycle_creates_signal_and_alert_once(engine: Engine) -> None:
    seed_instruments(engine)
    seed_news(engine)
    ai = make_ai(engine, analyst_output(cited_news_ids=[1]))
    assert run_signal_cycle(engine, ai) == 1
    with session_scope(engine) as s:
        sig = s.scalars(select(Signal)).one()
        # 76.75 AI score + 8 (telecom_tariff_hike playbook agrees) + 2.5 (macro alignment 100)
        assert (sig.symbol, sig.direction, sig.status, sig.score) == (
            "BHARTIARTL",
            "CALL",
            "NEW",
            87.25,
        )
        assert sig.expiry is not None and sig.expiry.weekday() <= 4
        alert = s.scalars(select(Alert)).one()
        assert alert.kind == "signal" and "NEW SIGNAL" in alert.text and "BHARTIARTL" in alert.text
        an = s.scalars(select(Analysis)).one()
        assert (
            an.model == "analyst-model"
            and an.scores_json["final"] == 87.25
            and an.scores_json["rules_adjustment"] == 8.0
        )
        assert an.scores_json["eligibility"] and an.scores_json["cited_news_ids"] == [1]
    # nothing new -> no second analysis (no AI spend)
    assert run_signal_cycle(engine, ai) == 0


def test_cycle_reanalyses_on_new_evidence_after_cooldown(engine: Engine) -> None:
    seed_instruments(engine)
    seed_news(engine)
    ai = make_ai(engine, analyst_output(cited_news_ids=[1]))
    run_signal_cycle(engine, ai)
    seed_news(engine, sentiment=0.7, title="Airtel second tariff story")
    assert run_signal_cycle(engine, ai) == 0  # inside the cooldown window
    with session_scope(engine) as s:
        for an in s.scalars(select(Analysis)):
            an.created_at = datetime.now(IST) - timedelta(hours=3)
    ai2 = make_ai(engine, analyst_output(cited_news_ids=[1, 2], impact=95, surprise=95))
    assert run_signal_cycle(engine, ai2) == 1
    with session_scope(engine) as s:
        sig = s.scalars(select(Signal)).one()
        assert sig.status == "UPGRADED" and sig.score > 87.25
        kinds = [
            a.text.split()[0] + " " + a.text.split()[1]
            for a in s.scalars(select(Alert).order_by(Alert.id))
        ]
        assert kinds == ["NEW SIGNAL", "SIGNAL UPGRADED"]


def test_failed_analysis_leaves_marker_and_no_retry_storm(engine: Engine) -> None:
    seed_instruments(engine)
    seed_news(engine)
    bad = analyst_output(cited_news_ids=[999])
    ai = make_ai(engine, bad)
    assert run_signal_cycle(engine, ai) == 1
    with session_scope(engine) as s:
        an = s.scalars(select(Analysis)).one()
        assert an.scores_json["failed"] is True and s.scalars(select(Signal)).all() == []
    assert run_signal_cycle(engine, ai) == 0


def test_budget_exhaustion_queues_one_health_alert(engine: Engine) -> None:
    seed_instruments(engine)
    seed_news(engine)
    settings = Settings(_env_file=None, ai_model_analyst="m", ai_daily_budget_inr=0.000001)  # type: ignore[call-arg]
    ai = AiClient(settings, engine, FakeMessages(analyst_output(cited_news_ids=[999])))
    run_signal_cycle(engine, ai)  # first call spends the budget (and fails validation)
    seed_news(engine, title="Another Airtel announcement", sentiment=0.9)
    with session_scope(engine) as s:
        for an in s.scalars(select(Analysis)):
            an.created_at = datetime.now(IST) - timedelta(hours=3)
    run_signal_cycle(engine, ai)
    run_signal_cycle(engine, ai)
    with session_scope(engine) as s:
        health = s.scalars(select(Alert).where(Alert.kind == "health")).all()
    assert len(health) == 1 and "budget" in health[0].text.lower()


def test_sector_watch_alerts_once_per_day(engine: Engine) -> None:
    seed_instruments(engine)
    seed_news(engine, "TCS", 0.9, "nse_announcements", n=3)
    with session_scope(engine) as s:
        assert sector_watch(s, datetime.now(IST)) == 1
        assert sector_watch(s, datetime.now(IST)) == 0
        alert = s.scalars(select(Alert)).one()
        assert alert.kind == "sector" and "IT" in alert.text and "TCS" in alert.text
