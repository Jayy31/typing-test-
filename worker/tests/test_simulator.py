from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from app.ai.schemas import Scenario
from app.pricing.bs import price
from app.sim.exits_vec import run_paths
from app.sim.simulator import CandidateInput, evaluate, seed_for, simulate_stock
from app.trading.exits import Bar, ExitRules, PositionState, step
from app.trading.targets import predicted_move_targets

BASE = ExitRules.from_config()


def scen(*rows: tuple[float, float, int]) -> list[Scenario]:
    return [
        Scenario(
            name=f"s{i}",
            probability=p,
            stock_move_pct=m,
            timing_days=t,
            triggers=[],
            invalidation="x",
        )
        for i, (p, m, t) in enumerate(rows)
    ]


@pytest.mark.parametrize(
    "rules",
    [
        replace(BASE, stop_mode="premium", window_stop=False),
        replace(BASE, stop_mode="underlying+premium", window_stop=True),
    ],
)
def test_vectorised_exits_match_pure_module(rules: ExitRules) -> None:
    rng = np.random.default_rng(1)
    n, days, entry, spot0 = 400, 15, 50.0, 1000.0
    prem = entry * np.exp(np.cumsum(rng.normal(0, 0.12, (n, days)), axis=1))
    spot = spot0 * np.exp(np.cumsum(rng.normal(0, 0.012, (n, days)), axis=1))
    dte = [25 - d for d in range(days)]
    targets = rules.fixed_targets(entry)
    state0 = PositionState.open(entry, rules, targets, 1, spot0, 990.0, 4.0, 3)
    vec = run_paths(prem, spot, entry, targets, rules, 1, spot0, state0.spot_stop, 4.0, 3, dte, 2)
    for i in range(n):
        st = state0
        for d in range(days):
            p = float(prem[i, d])
            bar = Bar(
                p,
                p,
                p,
                p,
                dte[d],
                sessions_since_event=(d - 2 if d >= 2 else None),
                spot=float(spot[i, d]),
                sessions_since_entry=d + 1,
            )
            st = step(st, bar, rules)
        if not st.closed:  # the vectorised version closes leftovers at the horizon
            st = replace(st, fills=(*st.fills, *([] if st.remaining == 0 else [])))
            horizon = st.remaining * (float(prem[i, -1]) / entry - 1) * 100
            assert vec.realised_pct[i] == pytest.approx(st.realised_pct() + horizon, abs=1e-6)
        else:
            assert vec.realised_pct[i] == pytest.approx(st.realised_pct(), abs=1e-6)
        assert vec.targets_hit[i] == st.targets_hit
        assert vec.first_event[i] == (st.fills[0].event if st.fills else "HORIZON")


def cand(opt: str = "CE", strike: float = 1000.0) -> CandidateInput:
    """Priced consistently: mid = Black-Scholes value, spread 1."""
    mid = float(price(1000.0, strike, 35 / 365, 0.065, 0.30, opt))
    return CandidateInput(date(2026, 11, 24), strike, opt, mid - 0.5, mid + 0.5, 30.0, 35, 24, 500)


def test_ev_sign_follows_the_prediction() -> None:
    bull = scen((0.7, 6.0, 3), (0.3, 0.0, 3))
    paths = simulate_stock(1000.0, bull, 0.3 / np.sqrt(252), 15, 4000, seed_for("TCS"))
    call = evaluate(cand("CE"), paths, BASE, 1, 4.2, 3, 0.065, None)
    put = evaluate(cand("PE"), paths, BASE, -1, -4.2, 3, 0.065, None)
    assert call.ev_pct > 0 > put.ev_pct
    assert 0 <= call.p_t1_before_sl <= 1 and call.p_t3 <= call.p_t2 <= 1
    c = cand("CE")
    assert call.ev_inr == pytest.approx(
        call.ev_pct / 100 * c.ask * 500 * BASE.lots, abs=0.5
    )  # whole position
    assert call.theta_per_day_inr < 0 and call.iv_sensitivity_pct > 0
    assert call.breakeven_move_pct is not None and call.breakeven_move_pct > 0
    assert sum(call.exit_mix.values()) == pytest.approx(1.0, abs=1e-3)


def test_iv_crush_lowers_ev_and_seed_is_reproducible() -> None:
    sc = scen((0.5, 3.0, 2), (0.5, -1.0, 2))
    a = simulate_stock(1000.0, sc, 0.02, 10, 3000, 42)
    b = simulate_stock(1000.0, sc, 0.02, 10, 3000, 42)
    assert np.array_equal(a.spot, b.spot)
    no_event = evaluate(cand(), a, BASE, 1, 1.0, 2, 0.065, None)
    crush = evaluate(cand(), a, BASE, 1, 1.0, 2, 0.065, event_day=1)
    assert crush.ev_pct < no_event.ev_pct


def test_predicted_move_targets_increase_and_respect_floor() -> None:
    rules = replace(BASE, targets_mode="predicted_move")
    t = predicted_move_targets(30.0, rules, 1000.0, 1000.0, "CE", 0.3, 35, 4.0, 3, 0.065)
    assert len(t) == 3 and t[0] > 30.0 and t[0] < t[1] < t[2]
    assert all(b >= a * 1.05 - 1e-9 for a, b in zip(t, t[1:], strict=False))
    # a negative prediction falls back to fixed targets
    assert predicted_move_targets(
        30.0, rules, 1000.0, 1000.0, "CE", 0.3, 35, -1.0, 3, 0.065
    ) == rules.fixed_targets(30.0)
