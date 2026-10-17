"""Phase 10: randomized property tests (seeded) for the exit rules and whole-lot booking."""

import numpy as np
import pytest

from app.trading.exits import Bar, ExitRules, PositionState, booking_plan, lots_of, step

RNG_SEEDS = range(200)


@pytest.mark.parametrize("lots", range(1, 11))
@pytest.mark.parametrize("mode", ["per_lot", "single_exit"])
def test_booking_is_whole_lots_and_complete(lots: int, mode: str) -> None:
    for n in range(1, 5):
        plan, fracs = booking_plan(lots, n, mode, 2)
        assert sum(plan) == lots and all(isinstance(p, int) and p >= 0 for p in plan)
        assert abs(sum(fracs) - 1) < 1e-9
        assert all(abs(f * lots - round(f * lots)) < 1e-9 for f in fracs)


def _random_path(rng: np.random.Generator, entry: float, n: int) -> list[Bar]:
    bars, p = [], entry
    for i in range(n):
        o = max(0.05, p * (1 + rng.normal(0, 0.08)))  # gaps between bars
        c = max(0.05, o * (1 + rng.normal(0, 0.12)))
        h, lo = (
            max(o, c) * (1 + abs(rng.normal(0, 0.05))),
            min(o, c) * (1 - abs(rng.normal(0, 0.05))),
        )
        bars.append(Bar(o, h, max(lo, 0.05), c, trading_days_to_expiry=max(1, 20 - i)))
        p = c
    return bars


@pytest.mark.parametrize("mode", ["per_lot", "single_exit"])
def test_exit_invariants_on_random_paths(mode: str) -> None:
    rules = ExitRules.from_config({"booking_mode": mode})
    lots = 3
    for seed in RNG_SEEDS:
        rng = np.random.default_rng(seed)
        entry = float(rng.uniform(5, 300))
        st = PositionState.open(entry, rules)
        stops = [st.stop]
        for b in _random_path(rng, entry, 25):
            st = step(st, b, rules)
            stops.append(st.stop)
            if st.closed:
                break
        # never more than the position is sold, always in whole lots
        sold = sum(f.fraction for f in st.fills)
        assert sold <= 1 + 1e-9 and abs(st.remaining - (1 - sold)) < 1e-9
        assert all(
            lots_of(f, lots) >= 1 and abs(f.fraction * lots - lots_of(f, lots)) < 1e-9
            for f in st.fills
        )
        # the stop only ever moves up (breakeven after T1, trail at T1 after T2)
        assert all(b >= a - 1e-9 for a, b in zip(stops, stops[1:], strict=False))
        # targets in order, each at most once
        hits = [f.event for f in st.fills if f.event in ("T1", "T2", "T3")]
        assert hits == sorted(hits) and len(hits) == len(set(hits))
        # a stop/target fill is never better than its trigger on a gap (fills at the bar's price)
        for f in st.fills:
            if f.gap:
                assert f.trigger is not None and f.price < f.trigger
        # bounded loss: the whole position can never lose more than the premium
        assert st.realised_pct() >= -100 - 1e-9
        if st.closed:
            assert abs(st.remaining) < 1e-9
