"""Vectorised copy of app.trading.exits.step for the Monte Carlo simulator.

It must behave exactly like the pure module for bars where open=high=low=close (one price
per simulated day). tests/test_simulator.py replays random paths through BOTH and requires
identical results, so the rules have one meaning everywhere.
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from app.trading.exits import ExitRules

F = NDArray[np.float64]
B = NDArray[np.bool_]


@dataclass(frozen=True)
class VecResult:
    realised_pct: F  # per path, % of entry premium
    first_event: NDArray[np.str_]  # first fill event per path ("" if none)
    targets_hit: NDArray[np.int64]
    hold_days: NDArray[np.int64]
    exit_event: NDArray[np.str_]


def run_paths(
    prem: F,  # (paths, days) exit (bid) premium per day
    spot: F,  # (paths, days)
    entry: float,
    targets: tuple[float, ...],
    rules: ExitRules,
    direction: int,
    spot_entry: float | None,
    spot_stop: float | None,
    predicted_move_pct: float | None,
    window_days: int | None,
    dte: list[int],  # trading days to expiry at each simulated day
    event_day: int | None,  # day index when the event passes (for NOMOVE), None if none
) -> VecResult:
    n, days = prem.shape
    open_ = np.ones(n, dtype=bool)
    remaining = np.ones(n)
    stop = np.full(n, entry * (1 + rules.stop_loss_pct / 100))
    hit = np.zeros(n, dtype=np.int64)
    started = np.zeros(n, dtype=bool)
    realised = np.zeros(n)
    first = np.full(n, "", dtype="<U8")
    last_ev = np.full(n, "", dtype="<U8")
    hold = np.zeros(n, dtype=np.int64)
    nt = len(targets)

    def fill(mask: B, event: str, frac: F, price: F) -> None:
        realised[mask] += frac[mask] * (price[mask] / entry - 1) * 100
        first[mask & (first == "")] = event
        last_ev[mask] = event

    for d in range(days):
        p, s = prem[:, d], spot[:, d]
        if spot_entry and predicted_move_pct:
            moved = direction * (s / spot_entry - 1) * 100
            started |= open_ & (moved >= rules.started_fraction * abs(predicted_move_pct))
        hold[open_] = d + 1
        stop_ev = np.where(hit >= rules.move_sl_to_entry_after, "TRAIL", "SL")
        # premium gap/stop (open == low == price here)
        m = open_ & (p <= stop)
        for ev in ("SL", "TRAIL"):
            mm = m & (stop_ev == ev)
            fill(mm, ev, remaining, p)
        remaining[m] = 0
        open_ &= ~m
        # underlying stop
        if spot_stop is not None:
            m = open_ & (direction * (s - spot_stop) <= 0)
            fill(m, "USTOP", remaining, np.maximum(p, 0.0))
            remaining[m] = 0
            open_ &= ~m
        # targets
        for k in range(nt):
            m = open_ & (hit == k) & (p >= targets[k])
            if not m.any():
                continue
            last = k == nt - 1
            frac = remaining.copy() if last else np.minimum(rules.book_fractions[k], remaining)
            fill(m, f"T{k + 1}", frac, np.maximum(targets[k], p))
            remaining[m] = (
                np.round((remaining[m] - frac[m]) * rules.lots) / rules.lots
                if rules.lots
                else np.round(remaining[m] - frac[m], 9)
            )  # exact multiples of 1/lots, like the pure module
            hit[m] = k + 1
            if k + 1 >= rules.trail_after:
                stop[m] = np.maximum(stop[m], targets[rules.trail_after - 2])
            elif k + 1 >= rules.move_sl_to_entry_after:
                stop[m] = np.maximum(stop[m], entry)
            closed = m & ((remaining <= 1e-9) | last)
            remaining[closed] = 0
            open_ &= ~closed
        # rule exits at close
        if dte[d] <= rules.time_stop_trading_days:
            fill(open_, "TIME", remaining, p)
            remaining[open_] = 0
            open_[:] = False
        if (
            rules.window_stop
            and window_days is not None
            and d + 1 > window_days + rules.window_buffer_days
        ):
            m = open_ & ~started & (hit == 0)
            fill(m, "WINDOW", remaining, p)
            remaining[m] = 0
            open_ &= ~m
        if event_day is not None and d - event_day >= rules.no_move_sessions:
            m = open_ & (hit == 0) & (np.abs(p / entry - 1) * 100 < rules.no_move_threshold_pct)
            fill(m, "NOMOVE", remaining, p)
            remaining[m] = 0
            open_ &= ~m
    # anything still open at the horizon is closed at the last price
    fill(open_, "HORIZON", remaining, prem[:, -1])
    return VecResult(realised, first, hit, hold, last_ev)
