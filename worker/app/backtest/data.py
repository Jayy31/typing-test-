"""Point-in-time access to the history store for the backtester (read-only DuckDB).

Everything a decision on day d may use is available "as of d": the trading calendar, raw prices
up to d, that day's option chain, the F&O universe and lot size on d, corporate actions, India
VIX. Option prices of later days are read only by the trade simulator, day by day.
"""

import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache
from typing import Any

import duckdb

from app.history import universe
from app.history.corp_actions import Action, load_actions
from app.history.event_study import reaction_day
from app.history.nse_files import fo_kind
from app.yaml_config import load_yaml


@dataclass(frozen=True)
class OptRow:
    day: date
    expiry: date
    strike: float
    type: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    settle: float | None
    oi: int | None
    volume: int | None


@dataclass(frozen=True)
class PriceBar:
    open: float
    high: float
    low: float
    close: float
    volume: int | None


@dataclass(frozen=True)
class Event:
    key: str  # one per (symbol, event type, direction, reaction day): duplicate articles count once
    event_ids: tuple[str, ...]
    symbol: str
    sector: str | None
    event_type: str | None
    direction: str
    day: date  # reaction day (entry at its close)
    timing: str
    title: str | None


class HistoryData:
    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con
        cfg = load_yaml("history.yaml")["event_study"]
        idx = con.execute(
            "SELECT date, close FROM hist_index WHERE lower(index_name) = lower(?) ORDER BY date",
            [cfg["market_index"]],
        ).fetchall()
        self.calendar: list[date] = [r[0] for r in idx]
        self.nifty: dict[date, float] = dict(idx)
        self.vix: dict[date, float] = dict(
            con.execute(
                "SELECT date, close FROM hist_index WHERE lower(index_name) = 'india vix'"
            ).fetchall()
        )
        self.prices: dict[str, dict[date, PriceBar]] = defaultdict(dict)
        for sym, d, o, h, lo, c, v in con.execute(
            "SELECT symbol, date, coalesce(open, close), coalesce(high, close), coalesce(low, close), close, volume FROM hist_prices"
        ).fetchall():
            self.prices[sym][d] = PriceBar(o, h, lo, c, v)
        self.actions: dict[str, list[Action]] = load_actions(con)
        self.members = universe.members(con)
        self._opt_cache: dict[str, dict[date, list[OptRow]]] = {}

    # ---- calendar ------------------------------------------------------------------------
    def idx(self, d: date) -> int:
        return bisect.bisect_left(self.calendar, d)

    def next_day(self, d: date, n: int = 1) -> date | None:
        i = (
            self.idx(d) + n
            if (self.idx(d) < len(self.calendar) and self.calendar[self.idx(d)] == d)
            else self.idx(d) + n - 1
        )
        return self.calendar[i] if 0 <= i < len(self.calendar) else None

    def trading_days_between(self, a: date, b: date) -> int:
        """Trading days after `a` up to and including `b`."""
        return max(0, bisect.bisect_right(self.calendar, b) - bisect.bisect_right(self.calendar, a))

    def prev_close(self, symbol: str, d: date) -> float | None:
        p = self.prices.get(symbol, {})
        i = self.idx(d) - 1
        while i >= 0:
            bar = p.get(self.calendar[i])
            if bar:
                return bar.close
            i -= 1
        return None

    # ---- options ---------------------------------------------------------------------------
    def _options(self, symbol: str) -> dict[date, list[OptRow]]:
        if symbol not in self._opt_cache:
            by_day: dict[date, list[OptRow]] = defaultdict(list)
            for r in self.con.execute(
                "SELECT date, expiry, strike, type, open, high, low, close, settle, oi, volume FROM hist_options WHERE symbol = ? ORDER BY date",
                [symbol],
            ).fetchall():
                by_day[r[0]].append(OptRow(*r))
            if len(self._opt_cache) > 20:  # bounded memory: options of the last ~20 stocks
                self._opt_cache.clear()
            self._opt_cache[symbol] = by_day
        return self._opt_cache[symbol]

    def chain(self, symbol: str, d: date) -> list[OptRow]:
        return self._options(symbol).get(d, [])

    def contract(
        self, symbol: str, d: date, expiry: date, strike: float, typ: str
    ) -> OptRow | None:
        for r in self.chain(symbol, d):
            if (
                r.expiry == expiry
                and r.type == typ
                and abs(r.strike - strike) < 0.01 * max(strike, 1)
            ):
                return r
        return None

    @lru_cache(maxsize=4096)  # noqa: B019 - one instance per backtest run
    def iv(self, symbol: str, d: date, expiry: date, strike: float, typ: str) -> float | None:
        row = self.con.execute(
            "SELECT iv FROM hist_iv WHERE symbol = ? AND date = ? AND expiry = ? AND strike = ? AND type = ?",
            [symbol, d, expiry, strike, typ],
        ).fetchone()
        return float(row[0]) if row and row[0] else None

    def to_lots(self, qty: int | None, d: date, lot: int | None, field: str) -> float | None:
        if qty is None or not lot:
            return None
        unit = load_yaml("backtest.yaml")["units"][fo_kind(d)][field]
        return float(qty) if unit == "contracts" else float(qty) / lot

    @lru_cache(maxsize=4096)  # noqa: B019
    def large_caps(self, d: date) -> frozenset[str]:
        n = int(load_yaml("backtest.yaml")["spread"]["large_cap_top_n"])
        rows = self.con.execute(
            "SELECT symbol FROM hist_prices WHERE date = ? ORDER BY close * coalesce(volume, 0) DESC LIMIT ?",
            [d, n],
        ).fetchall()
        return frozenset(r[0] for r in rows)

    def lot_on(self, symbol: str, d: date) -> tuple[int | None, str | None]:
        return universe.lot_on(self.con, symbol, d)

    def in_fno(self, symbol: str, d: date) -> bool:
        return not self.members or (d, symbol) in self.members

    # ---- events ----------------------------------------------------------------------------
    def events(self, start: date, end: date) -> list[Event]:
        """Labelled events whose reaction day falls in [start, end), deduplicated."""
        grouped: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
        for eid, d, t, sym, sector, etype, direction, title in self.con.execute(
            """SELECT event_id, date, time, symbol, sector, event_type, direction, title FROM hist_events
               WHERE symbol IS NOT NULL AND direction IN ('bullish', 'bearish') ORDER BY date, time NULLS LAST, event_id"""
        ).fetchall():
            i, timing = reaction_day(self.calendar, d, t)
            if i is None:
                continue
            day = self.calendar[i]
            if not (start <= day < end):
                continue
            grouped[f"{sym}:{etype}:{direction}:{day}"].append(
                (eid, sym, sector, etype, direction, day, timing, title)
            )
        out = []
        for key, rows in grouped.items():
            eid, sym, sector, etype, direction, day, timing, title = rows[0]
            out.append(
                Event(
                    key,
                    tuple(r[0] for r in rows),
                    sym,
                    sector,
                    etype,
                    direction,
                    day,
                    timing,
                    title,
                )
            )
        return sorted(out, key=lambda e: (e.day, e.symbol))


def at_close(d: date) -> datetime:
    return datetime.combine(d, time(15, 30))
