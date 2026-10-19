"""Positioning as evidence (feat.positioning): never a signal on its own.

Each state is support-only (shown in /why, no effect) until the event study has ANALOGUE_MIN_N
history days in that state; then its learned base rate (forward abnormal return) tilts the
prediction as a pseudo-scenario, weighted n / (n + ANALOGUE_PRIOR_N), all states together capped
at `evidence.max_total_weight`. Shared by the live signal path and the backtester.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from app.ai.schemas import Scenario
from app.config import get_settings
from app.positioning.features import cfg

StatsFn = Callable[[str, str, int], tuple[int, float, float, float] | None]


@dataclass(frozen=True)
class Evidence:
    feature: str
    state: str
    value: float
    basis: str  # support (too few history days) / base_rate
    lean: str | None  # configured lean (display) or the learned one
    n: int = 0
    mean_pct: float | None = None  # forward abnormal stock return after this state, %
    hit_up: float | None = None
    weight: float = 0.0  # share of the prediction

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def line(self) -> str:
        if self.basis == "base_rate":
            return (
                f"{self.feature}: {self.state} ({self.value:g}) - base rate n={self.n}, "
                f"{self.mean_pct:+.2f}% over {cfg()['evidence']['window']}d, up {self.hit_up:.0%}, "
                f"weight {self.weight:.0%}"
            )
        lean = f", leans {self.lean}" if self.lean else ""
        return f"{self.feature}: {self.state} ({self.value:g}) - support only (n={self.n}{lean})"


def assess(states: dict[str, tuple[str, float]], stats: StatsFn) -> list[Evidence]:
    st = get_settings()
    c = cfg()
    w = int(c["evidence"]["window"])
    out: list[Evidence] = []
    for feat, (state, value) in sorted(states.items()):
        row = stats(feat, state, w)
        n = row[0] if row else 0
        lean = c["lean"].get(state)
        if row is None or n < st.analogue_min_n:
            out.append(Evidence(feat, state, value, "support", lean, n))
            continue
        _, mean, up, _ = row
        weight = n / (n + float(st.analogue_prior_n))
        learned = "bullish" if mean > 0 else "bearish" if mean < 0 else None
        out.append(Evidence(feat, state, value, "base_rate", learned, n, mean, up, weight))
    # strongest usable states only, total weight capped
    usable = sorted((e for e in out if e.basis == "base_rate"), key=lambda e: -abs(e.mean_pct or 0))
    keep = {id(e) for e in usable[: int(c["evidence"]["max_states"])]}
    total = sum(e.weight for e in usable if id(e) in keep)
    cap = float(c["evidence"]["max_total_weight"])
    scale = min(1.0, cap / total) if total else 0.0
    return [
        e
        if e.basis != "base_rate"
        else Evidence(
            **{**asdict(e), "weight": round(e.weight * scale, 4) if id(e) in keep else 0.0}
        )
        for e in out
    ]


def tilt(scenarios: list[Scenario], evidence: list[Evidence], window_days: int) -> list[Scenario]:
    """Mix base-rate states into the scenario set as pseudo-scenarios (stock move = learned mean)."""
    used = [e for e in evidence if e.basis == "base_rate" and e.weight > 0]
    w = sum(e.weight for e in used)
    if not used or not scenarios:
        return scenarios
    mixed = [s.model_copy(update={"probability": s.probability * (1 - w)}) for s in scenarios]
    for e in used:
        mixed.append(
            Scenario(
                name=f"positioning {e.feature}={e.state}",
                probability=e.weight,
                stock_move_pct=max(-50.0, min(50.0, e.mean_pct or 0.0)),
                timing_days=max(1, window_days),
                triggers=[f"positioning base rate (n={e.n})"],
                invalidation="n/a",
            )
        )
    return mixed
