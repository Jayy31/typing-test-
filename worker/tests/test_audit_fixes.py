"""Accuracy audit 2026-11 (docs/AUDIT_FIXES.md), group A: live trading and trade selection."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pytest
from sqlalchemy import Engine, select

from app.config import IST, get_settings
from app.db.models import PaperTrade, Signal
from app.db.session import session_scope
from app.history.analogues import AnalogueResult
from app.market.options import OptionChain, OptionQuoteRow
from app.signals.candidates import Candidate, EvidenceItem
from app.signals.deferred import next_open
from app.signals.expiry import trading_days_between
from app.signals.prediction import combine
from app.sim.simulator import simulate_stock
from app.trading.paper_engine import _quote_moved, trading_cycle
from app.yaml_config import load_yaml
from tests.helpers import analyst_output
from tests.test_options_stage import FakeSource, run
from tests.test_paper_trading import _paths, make_signal, trade, weekday_at  # noqa: F401


class NoDepth(FakeSource):
    """Like Kotak outside the depth window: chain rows carry no bid/ask; with_depth supplies them."""

    def chain(self, symbol: str, expiry: date, as_of: date | None = None) -> OptionChain:
        ch = super().chain(symbol, expiry, as_of)
        rows = tuple(replace(r, bid=None, ask=None, bid_qty=None, ask_qty=None) for r in ch.rows)
        return OptionChain(ch.symbol, ch.expiry, ch.spot, ch.ts, rows)

    def with_depth(self, row: OptionQuoteRow) -> OptionQuoteRow:
        assert row.ltp
        return replace(
            row, bid=max(row.ltp - 0.5, 0.05), ask=row.ltp + 0.5, bid_qty=9000, ask_qty=9000
        )


def test_a1_trade_outside_depth_window_is_still_monitored(engine: Engine) -> None:
    src = FakeSource()
    now = weekday_at(11, 0)
    make_signal(engine, src, now)
    assert trading_cycle(engine, src, now) == 1
    far = NoDepth(spot=3700.0)  # stock collapses; the chain has no bid for our strike any more
    trading_cycle(engine, far, now + timedelta(minutes=10))
    tr = trade(engine)
    assert tr.status == "CLOSED" and tr.exit_reason in ("SL", "SPOT_SL", "STOP")


class Expired:
    available = True

    def __init__(self, spot: float) -> None:
        self._spot = spot

    def expiries(self, symbol: str, as_of: date | None = None) -> list[date]:
        return []

    def chain(self, symbol: str, expiry: date, as_of: date | None = None) -> None:
        return None

    def quote(self, symbol: str) -> None:
        return None

    def spot(self, symbol: str, as_of: date | None = None) -> float:
        return self._spot


def test_a1_expired_trade_settles_at_intrinsic(engine: Engine) -> None:
    src = FakeSource()
    now = weekday_at(11, 0)
    make_signal(engine, src, now)
    trading_cycle(engine, src, now)
    tr = trade(engine)
    after = datetime.combine(tr.expiry + timedelta(days=1), now.timetz())  # type: ignore[operator]
    while after.weekday() >= 5:
        after += timedelta(days=1)
    trading_cycle(engine, Expired(tr.strike + 37.0), after)  # type: ignore[arg-type, operator]
    tr = trade(engine)
    assert tr.status == "CLOSED" and tr.exit_reason == "EXPIRY"
    last = (tr.state_json or {})["fills"][-1]
    assert last["price"] == pytest.approx(37.0 if tr.option_type == "CE" else 0.0)


class NoIv(FakeSource):
    def chain(self, symbol: str, expiry: date, as_of: date | None = None) -> OptionChain:
        ch = super().chain(symbol, expiry, as_of)
        rows = tuple(replace(r, iv=None) for r in ch.rows)
        return OptionChain(ch.symbol, ch.expiry, ch.spot, ch.ts, rows)

    def with_depth(self, row: OptionQuoteRow) -> OptionQuoteRow:
        return row


def test_a2_quote_without_iv_is_never_priced(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.signals import options_stage

    monkeypatch.setattr(options_stage, "atm_iv", lambda chain: 8.0)  # ATM IV known, rows not
    run(engine, NoIv())
    with session_scope(engine) as s:
        assert s.scalars(select(Signal).where(Signal.pricing_status == "priced")).first() is None


def test_a3_market_paths_drift_at_the_risk_free_rate() -> None:
    get_settings.cache_clear()
    scen = analyst_output().scenarios
    days, vol = 20, 0.30 / 252**0.5
    p = simulate_stock(1000.0, scen, vol, days, 40000, 7, ai_weight=0.0)
    r = float(get_settings().risk_free_rate)
    assert float(np.mean(p.spot[:, -1])) == pytest.approx(1000 * np.exp(r * days / 252), rel=0.004)


def _an(median: float, hit: float) -> AnalogueResult:
    return AnalogueResult("event_type", "all", 3, 80, 80.0, median, median, hit, 2.0, -1.5, 1.8)


def test_a4_p_move_uses_the_measured_hit_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANALOGUE_MIN_N", "5")
    monkeypatch.setenv("ANALOGUE_PRIOR_N", "20")
    get_settings.cache_clear()
    scen = analyst_output().scenarios
    up, _ = combine(scen, "bullish", _an(0.1, 0.52), True)
    dn, _ = combine(scen, "bullish", _an(-0.1, 0.48), True)
    assert up and dn and up.p_move is not None and dn.p_move is not None
    assert abs(up.p_move - dn.p_move) < 0.05  # was a ~0.3 jump on the sign of the median
    against, _ = combine(scen, "bullish", _an(0.1, 0.52), False)
    assert against and against.p_move == pytest.approx(up.p_move - 0.8 * 0.04, abs=1e-3)
    get_settings.cache_clear()


def test_a5_flipped_graph_stats() -> None:
    f = replace(_an(-1.2, 0.35), p25=-3.0, p75=0.5).flipped()
    assert (f.mean, f.median, f.p25, f.p75) == (1.2, 1.2, -0.5, 3.0)
    assert f.hit_rate == pytest.approx(0.65)


def _item(source: str, etype: str, sentiment: float, weight: float) -> EvidenceItem:
    return EvidenceItem(
        1, 1, source, "news", "t", None, datetime.now(IST), sentiment, 0.8, etype, (), weight
    )


def test_a6_a7_event_type_order_and_support_rows() -> None:
    c = Candidate(
        "TCS",
        [
            _item("moneycontrol", "results", 0.8, 1.0),
            _item("moneycontrol", "block_deal", 0.2, 0.3),
            _item("kg:1:customer_of:base_rate:support", "kg:customer_of:results", 0.9, 1.0),
        ],
    )
    types = c.ranked_event_types()
    assert types[0] == "results" and types[-1].startswith("kg:")
    assert c.sources == {"moneycontrol"}  # the support row is not a second source


def test_a8_current_month_is_traded_when_valid(engine: Engine) -> None:
    src = FakeSource()
    run(engine, src)
    min_td = int(load_yaml("signals.yaml")["expiry"]["min_trading_days"])
    today = datetime.now(IST).date()
    want = next(e for e in src.exp if trading_days_between(today, e, set()) >= min_td)
    with session_scope(engine) as s:
        sig = s.scalars(select(Signal)).one()
        assert sig.pricing_status == "priced" and sig.expiry == want


def test_a10_entry_recheck() -> None:
    row = OptionQuoteRow(
        "X", date(2026, 11, 24), 100.0, "CE", 10.0, 10.4, 10.2, 20.0, 1, 1, 100.0, datetime.now(IST)
    )
    cfg: dict[str, Any] = {"recheck": {"enabled": True, "max_premium_drift_pct": 15}}
    assert not _quote_moved(row, {"best": {"ask": 10.0}}, cfg)
    assert _quote_moved(row, {"best": {"ask": 8.0}}, cfg)  # ask ran up 30% since pricing
    assert _quote_moved(replace(row, bid=6.0), {"best": {"ask": 10.0}}, cfg)  # spread blew out
    assert not _quote_moved(replace(row, bid=6.0), {}, {"recheck": {"enabled": False}})


def test_a11a_next_open_skips_the_weekend() -> None:
    friday = datetime(2026, 11, 20, 23, 59, tzinfo=IST)
    assert next_open(friday, set()).date() == date(2026, 11, 23)


def test_a11f_day_rules_wait_for_the_late_session(engine: Engine) -> None:
    src = FakeSource()
    now = weekday_at(11, 0)
    make_signal(engine, src, now)
    trading_cycle(engine, src, now)
    with session_scope(engine) as s:
        tr = s.scalars(select(PaperTrade)).one()
        rules = dict(tr.rules_json or {})
        rules["time_stop_trading_days"] = trading_days_between(now.date(), tr.expiry, set())  # type: ignore[arg-type]
        tr.rules_json = rules
    trading_cycle(engine, src, now + timedelta(minutes=5))
    assert trade(engine).status == "OPEN"  # 11:05: today is not a completed session yet
    trading_cycle(engine, src, now.replace(hour=15, minute=5))
    tr2 = trade(engine)
    assert tr2.status == "CLOSED" and tr2.exit_reason == "TIME"


# ---- group B: learning system ----------------------------------------------------------------
def _analysis(s: Any, symbol: str = "SBIN") -> Any:
    from app.db.models import Analysis

    a = Analysis(
        symbol=symbol, thesis="t", direction="bullish", created_at=datetime.now(IST), window_days=3
    )
    s.add(a)
    s.flush()
    return a


def test_b1_rejection_never_replayed_is_not_a_miss(engine: Engine) -> None:
    from app.calibration import service as calibration
    from app.db.models import CalibrationRecord, RejectedCandidate

    now = datetime.now(IST)
    with session_scope(engine) as s:
        a = _analysis(s)
        s.add(
            RejectedCandidate(
                dedupe_key="b1",
                ts=now,
                symbol="SBIN",
                stage="ev",
                reason="r",
                analysis_id=a.id,
                ref_strike=500,
                ref_option_type="PE",
                outcome_status="resolved",
                details_json={"cf": {"seen": now.isoformat()}},  # option never quoted: no state
            )
        )
        s.add(
            CalibrationRecord(
                analysis_id=a.id, target="p_t1_before_sl", predicted=0.6, recorded_at=now
            )
        )
    calibration.resolve(engine)
    with session_scope(engine) as s:
        r = s.scalars(select(CalibrationRecord)).one()
        assert r.outcome is None and r.resolved_at is None


def test_b2_tracker_serves_least_recently_seen_rows(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db.models import RejectedCandidate
    from app.tracking import counterfactual as cf

    cfg = {
        "counterfactual": {**load_yaml("tracking.yaml")["counterfactual"], "max_rows_per_run": 1}
    }
    monkeypatch.setattr(
        cf, "load_yaml", lambda name: cfg if name == "tracking.yaml" else load_yaml(name)
    )
    src = FakeSource()
    now = weekday_at(12, 40)
    with session_scope(engine) as s:
        s.add(
            RejectedCandidate(dedupe_key="c0", ts=now, symbol="INFY", stage="candidate", reason="r")
        )
        for i, sym in enumerate(("TCS", "SBIN")):
            s.add(
                RejectedCandidate(
                    dedupe_key=f"o{i}",
                    ts=now,
                    symbol=sym,
                    stage="edge",
                    reason="r",
                    ref_expiry=src.exp[0],
                    ref_strike=4000.0,
                    ref_option_type="CE",
                    ref_premium=50.0,
                )
            )
    cf.track_rejections(engine, src, now)  # type: ignore[arg-type]
    cf.track_rejections(engine, src, now + timedelta(minutes=5))  # type: ignore[arg-type]
    with session_scope(engine) as s:
        seen = {
            r.symbol: bool(((r.details_json or {}).get("cf") or {}).get("state"))
            for r in s.scalars(select(RejectedCandidate))
        }
    assert seen["TCS"] and seen["SBIN"]  # one chain per run, yet both replayed after two runs


def test_b3_b10c_score_sets_scored_at_and_judges_the_window_day() -> None:
    from app.db.models import PredictionScore
    from app.tracking.scorecard import score

    made = datetime(2026, 11, 9, 11, 0, tzinfo=IST)  # Monday
    ps = PredictionScore(
        analysis_id=1,
        symbol="TCS",
        direction="bullish",
        predicted_move_pct=2,
        predicted_move_raw_pct=4,
        window_days=2,
        spot_start=100.0,
        created_at=made,
        path_json=[],
    )
    closes = [
        ("2026-11-09", 100.5),
        ("2026-11-10", 101.0),
        ("2026-11-11", 102.0),
        ("2026-11-12", 99.0),
    ]
    score(ps, closes, set())
    assert ps.status == "scored" and ps.scored_at is not None
    assert (
        ps.direction_hit is True
    )  # up at the end of the 2-day window; the buffer-day fall is not judged
    assert ps.realised_move_pct == pytest.approx(-1.0)


def _opp(ret: float, edge: float, taken: bool, cluster: str) -> Any:
    from app.analytics.learning import Opp

    vals: dict[str, float | None] = {
        "score": 80,
        "edge_ratio": edge,
        "p_t1_before_sl": 0.7,
        "ev_pct": 30,
    }
    return Opp(datetime.now(IST), ret, vals, taken, cluster)


def test_b6_learning_counts_one_news_event_once() -> None:
    from app.analytics import learning

    cur = learning.current_values()
    opps = [_opp(10.0, 1.1, False, "c1") for _ in range(40)]  # one event analysed 40 times
    d = learning.evaluate(opps, "edge_ratio_min", 1.0, cur)
    assert d is not None and len(d) == 1 and d[0] == pytest.approx(400.0)


def test_b5_only_soft_gate_rejections_are_opportunities(engine: Engine) -> None:
    from app.analytics import learning
    from app.db.models import RejectedCandidate

    now = datetime.now(IST)
    with session_scope(engine) as s:
        for i, stage in enumerate(("edge", "entry", "liquidity", "options_probability")):
            a = _analysis(s)
            s.add(
                RejectedCandidate(
                    dedupe_key=f"s{i}",
                    ts=now,
                    symbol="SBIN",
                    stage=stage,
                    reason="r",
                    analysis_id=a.id,
                    outcome_status="resolved",
                    option_return_pct=20.0,
                    score=80,
                    details_json={"edge_ratio": 1.1, "p_t1_before_sl": 0.6, "ev_pct": 20},
                )
            )
    with session_scope(engine) as s:
        opps = learning.opportunities(s, now - timedelta(days=1), now + timedelta(days=1))
    assert len(opps) == 2  # edge and options_probability; pre-move and liquidity are hard rules
    assert opps[0].ret == pytest.approx(20.0 - 1.5)  # same round-trip cost as real trades


def test_b8_time_exit_is_not_a_t1_hit() -> None:
    from app.analytics.live import perf

    t = PaperTrade(
        pnl_pct=-5.0, pnl=-100.0, exit_reason="TIME", state_json={"targets_hit": 0}, slippage_inr=0
    )
    w = PaperTrade(
        pnl_pct=30.0, pnl=600.0, exit_reason="TRAIL", state_json={"targets_hit": 2}, slippage_inr=0
    )
    assert perf([t, w], lambda x: str(id(x)))["t1_hit_rate"] == 0.5


def test_b9_calibrator_brier_is_out_of_sample() -> None:
    from app.calibration import fit as F

    rng = np.random.default_rng(3)
    p = rng.uniform(0.3, 0.9, 400)
    y = rng.random(400) < 0.5  # outcomes unrelated to the predictions
    keys = [f"k{i}" for i in range(400)]
    res = F.fit(list(p), [bool(v) for v in y], keys, isotonic_min=200)
    w = F.cluster_weights(keys)
    in_sample = F.brier(np.array([res.apply(v) for v in p]), y.astype(float), w)
    assert res.brier > in_sample  # the stored figure no longer flatters the calibrator


def test_b10d_learning_lookback_is_three_months(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.analytics import learning

    seen: list[datetime] = []
    monkeypatch.setattr(learning, "opportunities", lambda s, a, b: seen.append(a) or [])
    monkeypatch.setattr(learning, "review_applied", lambda e, n: [])
    learning.run_monthly(engine, datetime(2026, 11, 1, 6, 0, tzinfo=IST))
    assert seen and seen[0].date() == date(2026, 8, 1)


def test_b7_review_without_affected_trades_does_not_revert(engine: Engine) -> None:
    from app.analytics import learning
    from app.db.models import Suggestion
    from tests.test_phase10 import NOW, _trade

    applied_at = datetime(2026, 8, 25, tzinfo=IST)
    with session_scope(engine) as s:
        s.add(
            Suggestion(
                id=7,
                month=date(2026, 8, 1),
                kind="change",
                param="edge_ratio_min",
                file="worker/config/options.yaml",
                yaml_path="edge_ratio_min",
                current_value=1.2,
                proposed_value=1.3,
                expected_effect=6.0,
                evidence_json={},
                status="applied",
                applied_at=applied_at,
            )
        )
        for i in range(1, 61):  # every trade far above both values: the change affected nothing
            _trade(s, i, 5.0, applied_at + timedelta(hours=20 * i), edge=1.6)
    assert learning.review_applied(engine, NOW) == []
    with session_scope(engine) as s:
        g = s.get(Suggestion, 7)
        assert g is not None and g.review_json["status"] == "too few affected opportunities"


def test_b4_after_hours_prediction_is_scored_when_priced(engine: Engine) -> None:
    from types import SimpleNamespace

    from app.db.models import CalibrationRecord, PredictionScore
    from app.signals.deferred import _score_deferred

    now = datetime.now(IST)
    with session_scope(engine) as s:
        a = _analysis(s, "TCS")
        sig = Signal(
            symbol="TCS", direction="CALL", status="NEW", analysis_id=a.id, created_at=now, score=80
        )
        s.add(sig)
        s.flush()
        res = SimpleNamespace(p_t1_before_sl=0.6)
        plan = SimpleNamespace(
            spot=4000.0,
            best_reference=SimpleNamespace(raw=SimpleNamespace(p_t1_before_sl=0.7), result=res),
        )
        policy = SimpleNamespace(move_weight=0.5, t1_cal=None)
        d = {"move_pct": 4.0, "p_move": 0.7, "direction": "bullish", "source": "single"}
        _score_deferred(s, a, sig, d, plan, policy, now)
    with session_scope(engine) as s:
        ps = s.scalars(select(PredictionScore)).one()
        assert (ps.spot_start, ps.predicted_move_pct, ps.predicted_move_raw_pct) == (
            4000.0,
            2.0,
            4.0,
        )
        assert s.scalars(select(CalibrationRecord)).one().target == "p_t1_before_sl"


# ---- group C: history, backtests, Strategy Lab -------------------------------------------------
def test_c1_hypothesis_is_closed_after_a_failed_holdout(engine: Engine) -> None:
    from sqlalchemy.orm import Session

    from app.db.models import LabSpec, LabVerdict
    from app.lab import refine
    from tests.test_lab_validate import _register

    sid = _register(engine)
    with Session(engine) as s:
        sp = s.get(LabSpec, sid)
        assert sp is not None
        sp.stage = "rejected"
        s.commit()
        assert len(refine.candidates(s)) == 1  # rejected at Stage 0: variants allowed
        s.add(
            LabVerdict(
                spec_id=sid,
                stage="holdout",
                passed=False,
                checks_json={},
                summary="failed",
                created_at=datetime.now(IST),
            )
        )
        s.commit()
        assert refine.candidates(s) == []  # holdout used: no variant may look at it again


def test_c4_scheduled_events_have_no_base_rate_direction() -> None:
    from app.lab.backtest import _direction
    from tests.test_lab_validate import spec

    sp = spec(trigger={"event_types": ["results"], "direction": "base_rate", "min_analogue_n": 5})
    an = replace(_an(1.0, 0.6), mean=2.0)
    assert _direction(sp, None, an) is None  # "moved the news' way" is not "up"
    assert _direction(sp, "bearish", an) == "bearish"
    assert _direction(sp, "bearish", replace(an, mean=-2.0)) == "bullish"


def test_c7_c8f_reactions_in_date_order_and_regime_known_before_the_event() -> None:
    from app.history.event_study import grouped_values, reaction_rows
    from tests import bt_fixture as F

    con, cal = F.build(event_days=(300,))
    F.add_training_reactions(con, cal)
    rows = reaction_rows(con)
    days = [r[5] for r in rows]
    assert days == sorted(days) and len(days) > 2
    d = days[-1]
    prev = cal[cal.index(d) - 1]
    groups = grouped_values(
        [r for r in rows if r[5] == d], {prev: 10.0, d: 40.0}
    )  # VIX spikes ON the day
    regimes = {k[3] for k in groups}
    assert "low_vol" in regimes and "high_vol" not in regimes


def test_c8c_stage0_entries_stop_before_the_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.lab import validate as V

    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", "2026-06-30")
    get_settings.cache_clear()
    assert V.screening_entry_end(date(2026, 12, 1)) == date(2026, 7, 1) - timedelta(days=45)
    assert V.screening_entry_end(date(2025, 1, 1)) == date(2025, 1, 1)
    get_settings.cache_clear()


def test_c6_positioning_base_rates_skip_corporate_action_windows() -> None:
    import duckdb

    from app.history.store import init_schema
    from app.positioning.history import stat_rows

    con = duckdb.connect()
    init_schema(con)
    days = [date(2026, 3, 2) + timedelta(days=i) for i in range(5)]  # Mon..Fri
    for i, d in enumerate(days):
        con.execute("INSERT INTO hist_index VALUES (?, 'Nifty 50', 100)", [d])
        for sym, px in (("AAA", 100.0 if i < 2 else 20.0), ("BBB", 100.0 + i)):  # AAA: 1:5 split
            con.execute(
                "INSERT INTO hist_prices VALUES (?, ?, ?, ?, ?, ?, 1000, 50)",
                [d, sym, px, px, px, px],
            )
        if i == 0:
            for sym in ("AAA", "BBB"):
                con.execute(
                    "INSERT INTO hist_positioning VALUES (?, ?, 'pcr', 'high', 1.5)", [d, sym]
                )
    con.execute(
        "INSERT INTO hist_corp_actions VALUES ('AAA', ?, 'split', 'Split 10 to 2', 0.2, NULL, 't')",
        [days[2]],
    )
    vals = [v for vs in stat_rows(con).values() for v in vs]
    assert len(vals) == 1 and vals[0] == pytest.approx(
        3.0
    )  # only BBB; no -80% "return" from the split


# ---- follow-up: B4 rest, C3 rest, C8a, C8g ----------------------------------------------------
def test_b4_after_hours_prediction_without_signal_is_scored_at_the_open(engine: Engine) -> None:
    from types import SimpleNamespace

    from app.db.models import Analysis, PredictionScore
    from app.signals.deferred import PENDING_KEY, queue_score, score_pending

    night = datetime(2026, 11, 17, 21, 0, tzinfo=IST)  # Tuesday evening
    with session_scope(engine) as s:
        a = _analysis(s, "TCS")
        a.created_at = night
        pred = SimpleNamespace(move_pct=4.0, p_move=0.7, source="scenario_tree")
        used = SimpleNamespace(move_pct=2.0, p_move=0.6)
        queue_score(s, a, "bullish", used, pred, None, "single", night)
        aid = a.id

    class Spot:
        def spot(self, symbol: str) -> float:
            return 4100.0

    assert score_pending(engine, Spot(), night + timedelta(hours=1)) == 0  # not due yet
    open_ = datetime(2026, 11, 18, 9, 20, tzinfo=IST)
    assert score_pending(engine, Spot(), open_) == 1
    assert score_pending(engine, Spot(), open_) == 0  # once
    with session_scope(engine) as s:
        ps = s.scalars(select(PredictionScore)).one()
        assert (ps.spot_start, ps.predicted_move_pct, ps.predicted_move_raw_pct) == (
            4100.0,
            2.0,
            4.0,
        )
        an = s.get(Analysis, aid)
        assert an is not None and PENDING_KEY not in (an.scores_json or {})


def test_c8a_post_entry_removes_the_day0_jump() -> None:
    from app.history.event_study import post_entry

    d = date(2025, 3, 3)
    rows = [
        ("results", "IT", "TCS", 0, "bullish", d, 1.0, 5.0),
        ("results", "IT", "TCS", 3, "bullish", d, 0.5, 6.0),
        ("results", "IT", "TCS", 5, "bullish", d, 1.0, 4.0),
        ("results", "IT", "INFY", 3, "bullish", d, 1.0, 9.0),  # no day-0 row: dropped
    ]
    out = post_entry(rows)
    assert out == [
        ("results", "IT", "TCS", 3, "bullish", d, 0.5, 1.0),
        ("results", "IT", "TCS", 5, "bullish", d, 1.0, -1.0),
    ]


def test_c8g_skew_needs_strikes_near_the_target_and_live_pcr_keeps_change() -> None:
    from app.positioning.features import DayInput, OptRow, iv_skew, pcr_state

    near = [OptRow(95, "PE", iv=30.0), OptRow(105, "CE", iv=20.0)]
    assert iv_skew(DayInput(spot=100.0, near=near))[0] == "put_skew"
    far = [OptRow(85, "PE", iv=30.0), OptRow(115, "CE", iv=20.0)]  # only far strikes have an IV
    assert iv_skew(DayInput(spot=100.0, near=far)) is None
    assert pcr_state(1.5, 1.2)[0] == "high_rising"
    assert pcr_state(1.5, None)[0] == "high"


def test_c3_at_open_entry_uses_previous_day_liquidity() -> None:
    import inspect

    from app.backtest import engine as bt
    from app.lab import backtest as lab

    # both entry paths read volume / OI for the tier and fill class from the previous session
    assert "liq_day = _prev_day(data, sig.day) if sig.entry_at_open" in inspect.getsource(bt)
    assert "liq_day = _shift_day(data, entry_day, -1) if at_open" in inspect.getsource(lab)


def test_b10a_neutral_ai_rows_resolve_from_closes_and_events_count_once(engine: Engine) -> None:
    import duckdb

    from app.analytics import ai_vs_rules
    from app.db.models import AiVsRules, NewsCluster

    con = duckdb.connect()
    con.execute("CREATE TABLE hist_prices (symbol VARCHAR, date DATE, close DOUBLE)")
    for i, px in enumerate([100.0, 101.0, 103.0, 104.0, 106.0]):
        con.execute("INSERT INTO hist_prices VALUES ('TCS', ?, ?)", [date(2026, 11, 2 + i), px])
    with session_scope(engine) as s:
        t0 = datetime(2026, 11, 3, 10, 0, tzinfo=IST)
        cl = NewsCluster(headline="one story", first_seen=t0, last_seen=t0)
        s.add(cl)
        s.flush()
        ids = []
        for _ in range(2):  # two articles about the same event, AI neutral, rules bullish
            a = _analysis(s, "TCS")
            a.direction, a.cluster_id, a.window_days = "neutral", cl.id, 3
            a.created_at = datetime(2026, 11, 3, 11, 0, tzinfo=IST)  # in market hours
            s.add(
                AiVsRules(
                    analysis_id=a.id,
                    symbol="TCS",
                    created_at=a.created_at,
                    news_direction="bullish",
                    ai_direction=None,
                    ai_signal=False,
                    rules_direction="bullish",
                )
            )
            ids.append(a.id)
    assert ai_vs_rules.resolve(engine, con=con) == 2
    with session_scope(engine) as s:
        rows = list(s.scalars(select(AiVsRules)))
        # ref = close before the analysis day (11-02: 100), 3 trading days later (11-05: 104)
        assert [r.realised_move_pct for r in rows] == [4.0, 4.0]
        out = ai_vs_rules.summarize(rows, ai_vs_rules.cluster_keys(s, rows))
        assert out["rules_only"] == {"n": 1, "hit_rate": 1.0, "avg_move_pct": 4.0}
        assert ai_vs_rules.summarize(rows)["rules_only"]["n"] == 2  # unclustered for comparison
