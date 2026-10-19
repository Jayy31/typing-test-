"""Live analogue lookup (DESIGN_V2 section 1): base rates from the event study for a new signal.

Most specific first: this stock -> its sector -> the event type overall, within the current
market regime (then all regimes). Statistics are direction-adjusted abnormal returns in %
(positive = moved our way). Examples are recent concrete events.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import duckdb

from app.config import get_settings
from app.history.store import connect
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalogueResult:
    level: str  # stock / sector / event_type
    regime: str
    window: int
    n: int
    n_eff: float  # weighted count (timing / confounding weights)
    mean: float
    median: float
    hit_rate: float
    std: float
    p25: float
    p75: float
    examples: list[dict[str, Any]] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.n >= get_settings().analogue_min_n

    @property
    def weight(self) -> float:
        """Share of the analogue distribution in the prediction: n / (n + ANALOGUE_PRIOR_N)."""
        if not self.usable:
            return 0.0
        return self.n_eff / (self.n_eff + float(get_settings().analogue_prior_n))

    def line(self) -> str:
        return (
            f"Analogues: n={self.n} (effective {self.n_eff:.1f}), weight {self.weight:.0%}, "
            f"avg {self.mean:+.1f}%, hit {self.hit_rate:.0%} ({self.level}, {self.regime}, {self.window}d)"
        )


def current_regime(today: date) -> str:
    try:
        with connect() as con:
            row = con.execute(
                "SELECT close FROM hist_index WHERE lower(index_name) = 'india vix' AND date <= ? ORDER BY date DESC LIMIT 1",
                [today],
            ).fetchone()
    except Exception:  # noqa: BLE001
        return "all"
    if row is None:
        return "all"
    cfg = load_yaml("history.yaml")["event_study"]["regimes"]
    v = float(row[0])
    return "low_vol" if v < cfg["vix_low"] else "high_vol" if v > cfg["vix_high"] else "normal"


def lookup(
    event_types: list[str],
    sector: str | None,
    symbol: str,
    direction: str,
    today: date,
    window: int | None = None,
    regime_label: str | None = None,
) -> AnalogueResult | None:
    """`regime_label` (feat.regime): mix all-regime stats toward that market regime's stats at
    n/(n+k) instead of the VIX-regime lookup."""
    cfg = load_yaml("history.yaml")
    w = int(window if window is not None else cfg["blend"]["analogue_window"])
    regime = current_regime(today)
    try:
        con_cm = connect()
        con = con_cm.__enter__()
    except Exception as exc:  # noqa: BLE001 - no history yet / store busy
        log.info("analogues unavailable: %s", exc)
        return None
    try:
        tries = [("stock", sector, symbol), ("sector", sector, None), ("event_type", None, None)]
        best: AnalogueResult | None = None
        if regime_label is not None:
            return _regime_lookup(con, event_types, tries, w, regime_label)
        for etype in event_types:
            for level, sec, sym in tries:
                for reg in dict.fromkeys([regime, "all"]):
                    row = con.execute(
                        """
                        SELECT n, n_eff, mean_abn_ret, median, hit_rate, std, p25, p75 FROM analogue_stats
                        WHERE event_type = ? AND sector IS NOT DISTINCT FROM ? AND symbol IS NOT DISTINCT FROM ?
                          AND regime = ? AND "window" = ?""",
                        [etype, sec, sym, reg, w],
                    ).fetchone()
                    if row is None:
                        continue
                    v = [float(x) for x in row[1:]]
                    res = AnalogueResult(
                        level, reg, w, int(row[0]), v[0], v[1], v[2], v[3], v[4], v[5], v[6]
                    )
                    if res.usable:
                        return _with_examples(con, res, etype, sec, sym, w)
                    best = best or res
        return _with_examples(con, best, event_types[0], sector, None, w) if best else None
    except duckdb.Error as exc:  # store not upgraded yet / partially loaded
        log.info("analogues unavailable: %s", exc)
        return None
    finally:
        con_cm.__exit__(None, None, None)


def _row(con: Any, etype: str, sec: str | None, sym: str | None, reg: str, w: int) -> Any:
    return con.execute(
        """
        SELECT n, n_eff, mean_abn_ret, median, hit_rate, std, p25, p75 FROM analogue_stats
        WHERE event_type = ? AND sector IS NOT DISTINCT FROM ? AND symbol IS NOT DISTINCT FROM ?
          AND regime = ? AND "window" = ?""",
        [etype, sec, sym, reg, w],
    ).fetchone()


def _regime_lookup(
    con: Any, event_types: list[str], tries: list[Any], w: int, label: str
) -> AnalogueResult | None:
    from app.regime.analogues import blend, key

    rk = key(label)
    best: tuple[AnalogueResult, str, Any, Any] | None = None
    for etype in event_types:
        for level, sec, sym in tries:
            row = _row(con, etype, sec, sym, "all", w)
            if row is None:
                continue
            v = [float(x) for x in row[1:]]
            res = AnalogueResult(level, "all", w, int(row[0]), *v)  # type: ignore[arg-type]
            rrow = _row(con, etype, sec, sym, rk, w) if rk else None
            reg = None
            if rrow is not None:
                rv = [float(x) for x in rrow[1:]]
                reg = AnalogueResult(level, rk or "", w, int(rrow[0]), *rv)  # type: ignore[arg-type]
            res = blend(res, reg, label)
            if res.usable:
                return _with_examples(con, res, etype, sec, sym, w)
            best = best or (res, etype, sec, sym)
    return _with_examples(con, best[0], best[1], best[2], None, w) if best else None


def _with_examples(
    con: Any, res: AnalogueResult, etype: str, sector: str | None, symbol: str | None, w: int
) -> AnalogueResult:
    n = int(load_yaml("history.yaml")["event_study"]["examples"])
    rows = con.execute(
        """
        SELECT e.date, r.symbol, e.direction, r.abnormal_ret, e.title FROM event_reactions r
        JOIN hist_events e USING (event_id)
        WHERE e.event_type = ? AND (? IS NULL OR e.sector = ?) AND (? IS NULL OR r.symbol = ?) AND r."window" = ?
          AND e.direction IN ('bullish', 'bearish') AND r.weight > 0
        ORDER BY e.date DESC LIMIT ?""",
        [etype, sector, sector, symbol, symbol, w, n],
    ).fetchall()
    examples = [
        {
            "date": str(d),
            "symbol": s,
            "direction": dr,
            "abnormal_pct": round(a, 2),
            "title": (t or "")[:120],
        }
        for d, s, dr, a, t in rows
    ]
    return AnalogueResult(
        res.level,
        res.regime,
        res.window,
        res.n,
        res.n_eff,
        res.mean,
        res.median,
        res.hit_rate,
        res.std,
        res.p25,
        res.p75,
        examples,
    )
