"""Pure exit rules. No I/O: the paper engine, simulator and backtester all call `step`.

Prices are option premiums. The engine feeds one observation at a time:
  - live: each poll is a bar with open=high=low=close=bid
  - daily (backtest): one bar per day with open/high/low/close
Conservative fills: if a bar touches both the stop and a target, the stop is assumed first.
Gaps: if a bar opens beyond a level, the fill is the open, not the level.
"""

from dataclasses import dataclass, field, replace
from typing import Any

from app.yaml_config import load_yaml


@dataclass(frozen=True)
class ExitRules:
    stop_loss_pct: float = -50
    targets_pct: tuple[float, ...] = (25, 50, 100)
    book_fractions: tuple[float, ...] = (1 / 3, 1 / 3, 1 / 3)
    move_sl_to_entry_after: int = 1
    trail_after: int = 2
    no_move_sessions: int = 2
    no_move_threshold_pct: float = 10
    time_stop_trading_days: int = 5

    @classmethod
    def from_config(cls) -> "ExitRules":
        c: dict[str, Any] = load_yaml("exits.yaml")
        return cls(
            stop_loss_pct=float(c["stop_loss_pct"]),
            targets_pct=tuple(float(x) for x in c["targets_pct"]),
            book_fractions=tuple(float(x) for x in c["book_fractions"]),
            move_sl_to_entry_after=int(c["move_sl_to_entry_after"]),
            trail_after=int(c["trail_after"]),
            no_move_sessions=int(c["no_move_sessions"]),
            no_move_threshold_pct=float(c["no_move_threshold_pct"]),
            time_stop_trading_days=int(c["time_stop_trading_days"]),
        )


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float
    trading_days_to_expiry: int
    sessions_since_event: int | None = None  # None = event not yet passed / not event-driven
    thesis_broken: bool = False


@dataclass(frozen=True)
class Fill:
    event: str  # T1/T2/T3/SL/TRAIL/THESIS/TIME/NOMOVE
    fraction: float  # of the original position
    price: float


@dataclass(frozen=True)
class PositionState:
    entry: float
    targets_hit: int = 0
    remaining: float = 1.0
    stop: float = 0.0
    closed: bool = False
    fills: tuple[Fill, ...] = field(default_factory=tuple)

    @classmethod
    def open(cls, entry: float, rules: ExitRules) -> "PositionState":
        return cls(entry=entry, stop=entry * (1 + rules.stop_loss_pct / 100))

    def realised_pct(self) -> float:
        """Return on the whole position (closed parts only), % of entry premium."""
        return sum(f.fraction * (f.price / self.entry - 1) * 100 for f in self.fills)


def target_price(state: PositionState, rules: ExitRules, k: int) -> float:
    return state.entry * (1 + rules.targets_pct[k] / 100)


def _close(state: PositionState, event: str, price: float) -> PositionState:
    fill = Fill(event, state.remaining, price)
    return replace(state, remaining=0.0, closed=True, fills=(*state.fills, fill))


def _stop_event(state: PositionState, rules: ExitRules) -> str:
    return "TRAIL" if state.targets_hit >= rules.move_sl_to_entry_after else "SL"


def step(state: PositionState, bar: Bar, rules: ExitRules) -> PositionState:
    """Apply one observation. Returns the new state (unchanged if nothing happened)."""
    if state.closed:
        return state

    # 1) gap through the stop at the open
    if bar.open <= state.stop:
        return _close(state, _stop_event(state, rules), bar.open)

    # 2) stop touched inside the bar: assume it came first (conservative)
    if bar.low <= state.stop:
        return _close(state, _stop_event(state, rules), state.stop)

    # 3) targets, in order; gap above a target fills at the open
    while state.targets_hit < len(rules.targets_pct) and not state.closed:
        k = state.targets_hit
        level = target_price(state, rules, k)
        if bar.high < level:
            break
        price = max(level, bar.open)
        last = k == len(rules.targets_pct) - 1
        frac = state.remaining if last else min(rules.book_fractions[k], state.remaining)
        fills = (*state.fills, Fill(f"T{k + 1}", frac, price))
        hit = k + 1
        stop = state.stop
        if hit >= rules.trail_after:
            stop = max(stop, target_price(state, rules, rules.trail_after - 2))
        elif hit >= rules.move_sl_to_entry_after:
            stop = max(stop, state.entry)
        state = replace(
            state,
            targets_hit=hit,
            remaining=round(state.remaining - frac, 6),
            stop=stop,
            fills=fills,
            closed=last or state.remaining - frac <= 1e-9,
        )
    if state.closed:
        return state

    # 4) rule-based exits at the bar's close
    if bar.thesis_broken:
        return _close(state, "THESIS", bar.close)
    if bar.trading_days_to_expiry <= rules.time_stop_trading_days:
        return _close(state, "TIME", bar.close)
    if (
        bar.sessions_since_event is not None
        and bar.sessions_since_event >= rules.no_move_sessions
        and state.targets_hit == 0
        and abs(bar.close / state.entry - 1) * 100 < rules.no_move_threshold_pct
    ):
        return _close(state, "NOMOVE", bar.close)
    return state
