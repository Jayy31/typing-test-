"""Phase 14 PR 1: trial registry, pre-registration, validation ladder, luck tests."""

import copy
from datetime import date, timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.backtest.data import HistoryData
from app.config import get_settings
from app.db.models import LabHypothesis, LabSpec, LabTrial, LabVerdict
from app.lab import registry as R
from app.lab import validate as V
from app.lab.backtest import run_spec
from app.lab.spec import StrategySpec, cfg
from tests import bt_fixture as F


def spec(name: str = "post_results", **kw) -> StrategySpec:
    d = {"name": name, "source": "known_pattern", "trigger": {"event_types": ["results"]}}
    d.update(kw)
    return StrategySpec.model_validate(d)


@pytest.fixture
def cutoff_mid(monkeypatch):
    con, cal = F.build(event_days=(290, 300, 330, 340, 350))
    F.add_training_reactions(con, cal)
    for i in (290, 300, 330, 340, 350):
        F.add_event(con, f"e{i}", cal[i])
    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", cal[320].isoformat())
    get_settings.cache_clear()
    yield con, cal
    get_settings.cache_clear()


def small_cfg(monkeypatch, **over):
    c = copy.deepcopy(cfg())
    c["luck"]["placebo"]["runs"] = 3
    for path, v in over.items():
        node = c
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = v
    for mod in (V, R):
        monkeypatch.setattr(mod, "cfg", lambda c=c: c)
    import app.lab.spec as S

    monkeypatch.setattr(S, "cfg", lambda c=c: c)
    return c


def test_deflated_sharpe_falls_with_more_trials() -> None:
    one = V.deflated_sharpe(0.3, 100, 1, 0.02)
    many = V.deflated_sharpe(0.3, 100, 200, 0.02)
    assert one > 0.99 and many < one
    assert V.deflated_sharpe(0.0, 100, 1, 0.02) == pytest.approx(0.5)


def test_placebo_shift_is_deterministic_and_never_near_zero() -> None:
    f, g = V.placebo_shift(1), V.placebo_shift(1)
    shifts = [f(f"k{i}") for i in range(200)]
    assert shifts == [g(f"k{i}") for i in range(200)]
    assert all(3 <= abs(x) <= 20 for x in shifts) and len(set(shifts)) > 10


def test_neighbours_cover_strike_days_and_thresholds() -> None:
    s = spec(
        entry={"timing": "before_event", "days_before": 5},
        trigger={"event_types": ["results"], "direction": "bullish"},
        exits={"stop_loss_pct": -40, "targets_pct": [30, 60]},
        filters={"min_adv_cr": 50},
    )
    labels = [lbl for lbl, _ in V.neighbours(s)]
    assert {"strike itm1", "strike otm1", "days_before -1", "days_before +2"} <= set(labels)
    assert any(lbl.startswith("min_adv_cr") for lbl in labels)
    assert any(lbl.startswith("targets") for lbl in labels)


def test_concentration_and_screening_window(cutoff_mid) -> None:
    con, cal = cutoff_mid
    data = HistoryData(con)
    end = V.screening_end(cal[-1])
    assert end == cal[320] + timedelta(days=1)
    res = run_spec(con, spec(), F.START, end, data)
    assert res.trades and all(t.entry_day <= cal[320] for t in res.trades)
    conc = V.concentration(res.trades)
    assert conc["stock"] == 1.0  # one stock supplies all profit -> fails the 25% rule


def _register(engine: Engine, s_: StrategySpec | None = None) -> int:
    with Session(engine) as s:
        h = R.register(
            s,
            "post_results_drift",
            "known_pattern",
            "Drift after results",
            "+3% of premium per trade",
        )
        sp = R.add_spec(s, h, s_ or spec())
        s.commit()
        return sp.id


def test_pre_registration_and_variant_limits(engine: Engine) -> None:
    with Session(engine) as s:
        with pytest.raises(R.RegistryError):
            R.register(s, "x", "known_pattern", "", "something")
        h = R.register(s, "h1", "known_pattern", "why", "what")
        R.add_spec(s, h, spec())
        with pytest.raises(R.RegistryError):
            R.add_spec(s, h, spec(description="same rules"))  # identical spec
        with pytest.raises(R.RegistryError):
            R.add_spec(s, h, spec(source="ai_proposal"))
        for i in range(9):
            R.add_spec(s, h, spec(exits={"max_hold_days": i + 1}))
        with pytest.raises(R.RegistryError):
            R.add_spec(s, h, spec(exits={"max_hold_days": 15}))
        assert h.status == "testing" and not h.ai_memory_risk


def test_screen_stores_every_trial_and_rejects_thin_results(
    engine, cutoff_mid, monkeypatch
) -> None:
    con, cal = cutoff_mid
    small_cfg(monkeypatch)
    sid = _register(engine)
    v = R.screen(con, sid, engine)
    assert not v.passed and "min_trades" in v.summary and "concentration" in v.summary
    with Session(engine) as s:
        kinds = [t.kind for t in s.scalars(select(LabTrial))]
        assert kinds.count("stage0") == 1 and "cost" in kinds and "plateau" in kinds
        assert s.get(LabSpec, sid).stage == "rejected"
        assert s.scalars(select(LabHypothesis)).one().status == "rejected"
        st = s.scalars(select(LabTrial).where(LabTrial.kind == "stage0")).one()
        assert st.period_end <= cal[320] + timedelta(days=1)


def test_screen_passes_when_bars_are_met(engine, cutoff_mid, monkeypatch) -> None:
    con, cal = cutoff_mid
    small_cfg(
        monkeypatch,
        **{
            "stage0.min_trades": 1,
            "stage0.min_events": 1,
            "stage0.require_ci_lower_above": -1e9,
            "stage0.deflated_sharpe_min": 0.0,
            "luck.max_profit_share_per_stock": 1.0,
            "luck.max_profit_share_per_month": 1.0,
            "luck.regime.min_trades_per_regime": 1,
            "luck.plateau.min_share_profitable": 0.0,
            "luck.placebo.real_must_beat_percentile": 0,
        },
    )
    sid = _register(engine)
    v = R.screen(con, sid, engine)
    failed = [k for k, c in v.checks_json["checks"].items() if not c["passed"]]
    # one trade (one per stock while open): too few for a bootstrap CI, everything else passes
    assert failed == ["ci_excludes_zero"], v.checks_json


def test_holdout_pending_then_used_exactly_once(engine, cutoff_mid, monkeypatch) -> None:
    con, cal = cutoff_mid
    small_cfg(monkeypatch)
    sid = _register(engine)
    with Session(engine) as s:
        s.get(LabSpec, sid).stage = "holdout_pending"
        s.commit()
    out = R.holdout(con, sid, engine)
    assert isinstance(out, dict) and out["pending"] and out["n"] >= 1
    with Session(engine) as s:
        t = s.scalars(select(LabTrial).where(LabTrial.kind == "holdout_count")).one()
        assert t.expectancy_pct is None and t.n_trades >= 1  # counts only, no results
    small_cfg(monkeypatch, **{"holdout.min_trades": 1, "holdout.min_events": 1})
    v = R.holdout(con, sid, engine)
    assert isinstance(v, LabVerdict) and v.stage == "holdout"
    with pytest.raises(R.RegistryError):
        R.holdout(con, sid, engine)
    with Session(engine) as s, pytest.raises(IntegrityError):
        s.add(LabVerdict(spec_id=sid, stage="holdout", passed=True, checks_json={}))
        s.commit()
    _ = date
