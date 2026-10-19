"""Watchlist paper book (feat.watchlist_book): near-misses traded separately from the main book.

A near-miss passed every hard check (eligibility, liquidity, core rules, entry rules) and failed
exactly ONE soft gate (score, edge ratio, P(T1 before SL), minimum EV % of premium) within the
margin in config/watchlist.yaml. It gets a Signal with status WATCHLIST and book "watchlist", which
the paper engine trades with the same rules as the main book. It reuses the existing prediction
(no AI call), sends no Telegram alert, and is kept out of main P&L, stats, portfolio and reports.
"""

from dataclasses import dataclass, replace
from typing import Any

from app.config import get_settings
from app.signals.options_stage import OptionPlan
from app.trading import core_rules
from app.yaml_config import load_yaml

BOOK = "watchlist"
MAIN = "main"
STATUS = "WATCHLIST"
SOFT_STAGES = {"edge", "ev", "probability"}


def cfg() -> dict[str, Any]:
    return load_yaml("watchlist.yaml")


@dataclass(frozen=True)
class GateCheck:
    gate: str
    value: float
    required: float
    floor: float  # the margin: lowest value a near-miss may have

    @property
    def passed(self) -> bool:
        return self.value >= self.required

    @property
    def near(self) -> bool:
        return not self.passed and self.value >= self.floor


def soft_gates(score: float, plan: OptionPlan, gates: dict[str, float]) -> list[GateCheck] | None:
    """The four soft gates for the plan's best candidate (None when it was never priced)."""
    best = plan.best_reference
    if best is None or best.result is None or plan.edge_ratio is None:
        return None
    m = cfg()["margins"]
    thr = float(get_settings().signal_score_threshold)
    r = best.result
    return [
        GateCheck("score", score, thr, thr - float(m["score_points"])),
        GateCheck(
            "edge_ratio", plan.edge_ratio, gates["edge_ratio_min"], float(m["edge_ratio_min"])
        ),
        GateCheck(
            "p_t1_before_sl",
            r.p_t1_before_sl,
            gates["min_p_t1_before_sl"],
            float(m["min_p_t1_before_sl"]),
        ),
        GateCheck(
            "ev_pct", r.ev_pct, gates["min_ev_pct_of_premium"], float(m["min_ev_pct_of_premium"])
        ),
    ]


def near_miss(
    score: float,
    eligible: bool,
    plan: OptionPlan | None,
    gates: dict[str, float],
    today: Any,
    direction: str,
) -> GateCheck | None:
    """The single soft gate a near-miss failed, or None when it is not a near-miss."""
    if not eligible or plan is None or plan.status == "pending":
        return None
    if plan.rejection is not None and plan.rejection[0] not in SOFT_STAGES:
        return None  # a hard check failed (liquidity, entry rules, core rules, ...)
    checks = soft_gates(score, plan, gates)
    if checks is None:
        return None
    failed = [c for c in checks if not c.passed]
    if len(failed) != 1 or not failed[0].near:
        return None
    best = plan.best_reference
    assert best is not None
    opt = "CE" if direction == "bullish" else "PE"
    if core_rules.violation(best.row.symbol, direction, opt, best.row.expiry, today):
        return None
    if best.row.option_type != opt:
        return None
    return failed[0]


def plan_for_book(plan: OptionPlan) -> OptionPlan:
    """The near-miss plan as if its best candidate had been chosen (same contract the gate saw)."""
    return replace(plan, chosen=plan.best_reference, rejection=None, status="priced")


def is_main(book: str | None) -> bool:
    return (book or MAIN) == MAIN
