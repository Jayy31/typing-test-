"""Target premium levels for the two target modes (v2.1 A2)."""

from app.pricing.bs import price
from app.trading.exits import ExitRules


def predicted_move_targets(
    entry: float,
    rules: ExitRules,
    spot: float,
    strike: float,
    option_type: str,
    iv: float,
    days_to_expiry: float,
    predicted_move_pct: float,
    window_days: float,
    r: float,
) -> tuple[float, ...]:
    """Premium when the stock has moved f x predicted move (f = 0.5/1.0/1.5), repriced with
    Black-Scholes at entry IV after f x window days. Levels are forced strictly increasing and
    at least `min_step_pct` apart; a non-positive prediction falls back to the fixed targets."""
    if predicted_move_pct <= 0:
        return rules.fixed_targets(entry)
    sign = 1.0 if option_type == "CE" else -1.0
    levels: list[float] = []
    floor = entry
    for f in rules.predicted_fractions:
        s = spot * (1 + sign * f * predicted_move_pct / 100)
        t = max(days_to_expiry - f * window_days, 0.0) / 365
        level = float(price(s, strike, t, r, iv, option_type))
        level = max(level, floor * (1 + rules.min_step_pct / 100))
        levels.append(level)
        floor = level
    return tuple(levels)


def targets_for(entry: float, rules: ExitRules, **kw: float | str) -> tuple[float, ...]:
    if rules.targets_mode == "predicted_move":
        return predicted_move_targets(entry, rules, **kw)  # type: ignore[arg-type]
    return rules.fixed_targets(entry)
