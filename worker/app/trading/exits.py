"""Pure exit rules. No I/O: the paper engine, simulator and backtester all call `step`.

Prices are option premiums; `spot` fields are the underlying stock. The engine feeds one
observation at a time:
  - live: each poll is a bar with open=high=low=close=bid and spot=last
  - daily (backtest): one bar per day with premium and spot open/high/low/close
Conservative fills: if a bar touches a stop and a target, the stop is assumed first.
Gaps: if a bar opens beyond a premium level, the fill is the open, not the level.
Underlying stop fills at the bar's premium low (worst case for a daily bar).

Event codes: T1/T2/T3 targets, SL premium stop, TRAIL stop after T1 (entry or T1 level),
USTOP underlying stop (v2.1 A1), WINDOW scenario-window stop (v2.1 A3), THESIS, TIME
(near-expiry backstop), NOMOVE (no move after the event).
"""

from dataclasses import dataclass, field, replace
from typing import Any

from app.yaml_config import load_yaml


@dataclass(frozen=True)
class ExitRules:
    stop_loss_pct: float = -50
    stop_mode: str = "premium"  # premium | underlying+premium
    underlying_adverse_pct: float = 3.0
    use_pre_news_level: bool = True
    targets_mode: str = "fixed"  # fixed | predicted_move
    targets_pct: tuple[float, ...] = (25, 50, 100)
    predicted_fractions: tuple[float, ...] = (0.5, 1.0, 1.5)
    min_step_pct: float = 5
    book_fractions: tuple[float, ...] = (1 / 3, 1 / 3, 1 / 3)
    move_sl_to_entry_after: int = 1
    trail_after: int = 2
    window_stop: bool = False
    window_buffer_days: int = 1
    started_fraction: float = 0.25
    no_move_sessions: int = 2
    no_move_threshold_pct: float = 10
    time_stop_trading_days: int = 5

    @classmethod
    def from_config(cls, overrides: dict[str, Any] | None = None) -> "ExitRules":
        """Rules from config/exits.yaml; `overrides` lets the backtester try variants."""
        c: dict[str, Any] = load_yaml("exits.yaml")
        rules = cls(
            stop_loss_pct=float(c["stop"]["premium_backstop_pct"]),
            stop_mode=str(c["stop"]["mode"]),
            underlying_adverse_pct=float(c["stop"]["underlying_adverse_pct"]),
            use_pre_news_level=bool(c["stop"]["use_pre_news_level"]),
            targets_mode=str(c["targets"]["mode"]),
            targets_pct=tuple(float(x) for x in c["targets"]["fixed_pct"]),
            predicted_fractions=tuple(float(x) for x in c["targets"]["predicted_fractions"]),
            min_step_pct=float(c["targets"]["min_step_pct"]),
            book_fractions=tuple(float(x) for x in c["book_fractions"]),
            move_sl_to_entry_after=int(c["move_sl_to_entry_after"]),
            trail_after=int(c["trail_after"]),
            window_stop=bool(c["window_stop"]["enabled"]),
            window_buffer_days=int(c["window_stop"]["buffer_days"]),
            started_fraction=float(c["window_stop"]["started_fraction"]),
            no_move_sessions=int(c["no_move_sessions"]),
            no_move_threshold_pct=float(c["no_move_threshold_pct"]),
            time_stop_trading_days=int(c["time_stop_trading_days"]),
        )
        return replace(rules, **(overrides or {}))

    def fixed_targets(self, entry: float) -> tuple[float, ...]:
        return tuple(entry * (1 + p / 100) for p in self.targets_pct)


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float
    trading_days_to_expiry: int
    sessions_since_event: int | None = None  # None = event not yet passed / not event-driven
    thesis_broken: bool = False
    spot: float | None = None  # underlying close/last
    spot_high: float | None = None
    spot_low: float | None = None
    sessions_since_entry: int | None = None


@dataclass(frozen=True)
class Fill:
    event: str
    fraction: float  # of the original position
    price: float


@dataclass(frozen=True)
class PositionState:
    entry: float
    targets: tuple[float, ...] = ()  # absolute premium levels T1..Tn
    targets_hit: int = 0
    remaining: float = 1.0
    stop: float = 0.0
    closed: bool = False
    fills: tuple[Fill, ...] = field(default_factory=tuple)
    # underlying context (v2.1)
    direction: int = 1  # +1 call (bullish), -1 put (bearish)
    spot_entry: float | None = None
    spot_stop: float | None = None
    predicted_move_pct: float | None = None
    window_days: int | None = None
    started: bool = False

    @classmethod
    def open(
        cls,
        entry: float,
        rules: ExitRules,
        targets: tuple[float, ...] | None = None,
        direction: int = 1,
        spot_entry: float | None = None,
        pre_news_spot: float | None = None,
        predicted_move_pct: float | None = None,
        window_days: int | None = None,
    ) -> "PositionState":
        """`targets` = absolute premiums (predicted_move mode); default = fixed % targets."""
        spot_stop = None
        if rules.stop_mode == "underlying+premium" and spot_entry:
            spot_stop = spot_entry * (1 - direction * rules.underlying_adverse_pct / 100)
            if rules.use_pre_news_level and pre_news_spot:
                # the tighter of the two: back beyond the pre-news level, or X% adverse
                spot_stop = (
                    max(spot_stop, pre_news_spot)
                    if direction > 0
                    else min(spot_stop, pre_news_spot)
                )
                # never a stop already breached at entry
                if direction * (spot_entry - spot_stop) <= 0:
                    spot_stop = spot_entry * (1 - direction * rules.underlying_adverse_pct / 100)
        return cls(
            entry=entry,
            targets=targets or rules.fixed_targets(entry),
            stop=entry * (1 + rules.stop_loss_pct / 100),
            direction=direction,
            spot_entry=spot_entry,
            spot_stop=spot_stop,
            predicted_move_pct=predicted_move_pct,
            window_days=window_days,
        )

    def realised_pct(self) -> float:
        """Return on the whole position (closed parts only), % of entry premium."""
        return sum(f.fraction * (f.price / self.entry - 1) * 100 for f in self.fills)


def _close(state: PositionState, event: str, price: float) -> PositionState:
    fill = Fill(event, state.remaining, price)
    return replace(state, remaining=0.0, closed=True, fills=(*state.fills, fill))


def _stop_event(state: PositionState, rules: ExitRules) -> str:
    return "TRAIL" if state.targets_hit >= rules.move_sl_to_entry_after else "SL"


def _underlying_breached(state: PositionState, bar: Bar) -> bool:
    if state.spot_stop is None or bar.spot is None:
        return False
    worst = (bar.spot_low if state.direction > 0 else bar.spot_high) or bar.spot
    return state.direction * (worst - state.spot_stop) <= 0


def _update_started(state: PositionState, bar: Bar, rules: ExitRules) -> PositionState:
    if state.started or not (state.spot_entry and state.predicted_move_pct and bar.spot):
        return state
    best = (bar.spot_high if state.direction > 0 else bar.spot_low) or bar.spot
    moved = state.direction * (best / state.spot_entry - 1) * 100
    if moved >= rules.started_fraction * abs(state.predicted_move_pct):
        return replace(state, started=True)
    return state


def step(state: PositionState, bar: Bar, rules: ExitRules) -> PositionState:
    """Apply one observation. Returns the new state (unchanged if nothing happened)."""
    if state.closed:
        return state
    state = _update_started(state, bar, rules)

    # 1) gap through the premium stop at the open
    if bar.open <= state.stop:
        return _close(state, _stop_event(state, rules), bar.open)
    # 2) underlying stop (thesis invalidated on the stock): conservative fill at the bar low
    if _underlying_breached(state, bar):
        return _close(state, "USTOP", max(bar.low, 0.0))
    # 3) premium stop touched inside the bar: assume it came first
    if bar.low <= state.stop:
        return _close(state, _stop_event(state, rules), state.stop)

    # 4) targets, in order; gap above a target fills at the open
    n = len(state.targets)
    while state.targets_hit < n and not state.closed:
        k = state.targets_hit
        level = state.targets[k]
        if bar.high < level:
            break
        price = max(level, bar.open)
        last = k == n - 1
        frac = state.remaining if last else min(rules.book_fractions[k], state.remaining)
        hit = k + 1
        stop = state.stop
        if hit >= rules.trail_after:
            stop = max(stop, state.targets[rules.trail_after - 2])
        elif hit >= rules.move_sl_to_entry_after:
            stop = max(stop, state.entry)
        state = replace(
            state,
            targets_hit=hit,
            remaining=round(state.remaining - frac, 6),
            stop=stop,
            fills=(*state.fills, Fill(f"T{hit}", frac, price)),
            closed=last or state.remaining - frac <= 1e-9,
        )
    if state.closed:
        return state

    # 5) rule-based exits at the bar's close
    if bar.thesis_broken:
        return _close(state, "THESIS", bar.close)
    if bar.trading_days_to_expiry <= rules.time_stop_trading_days:
        return _close(state, "TIME", bar.close)
    if (
        rules.window_stop
        and state.window_days is not None
        and bar.sessions_since_entry is not None
        and bar.sessions_since_entry > state.window_days + rules.window_buffer_days
        and not state.started
        and state.targets_hit == 0
    ):
        return _close(state, "WINDOW", bar.close)
    if (
        bar.sessions_since_event is not None
        and bar.sessions_since_event >= rules.no_move_sessions
        and state.targets_hit == 0
        and abs(bar.close / state.entry - 1) * 100 < rules.no_move_threshold_pct
    ):
        return _close(state, "NOMOVE", bar.close)
    return state
