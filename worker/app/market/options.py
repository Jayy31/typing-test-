"""One option-data interface, two implementations (DESIGN_V2 section 0):
  - live broker (Upstox, Phase 3) for paper trading
  - historical NSE F&O bhavcopy (Phase 4) for the backtester and simulator calibration
Callers depend only on `OptionDataSource`. `StaticOptionData` serves tests and replays.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True)
class OptionQuoteRow:
    symbol: str
    expiry: date
    strike: float
    option_type: str  # CE / PE
    bid: float | None
    ask: float | None
    ltp: float | None
    iv: float | None  # annualised, in % (e.g. 32.5)
    oi: int | None
    volume: int | None
    spot: float | None
    ts: datetime

    @property
    def mid(self) -> float | None:
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2
        return self.ltp

    @property
    def spread_pct(self) -> float | None:
        m = self.mid
        if not (self.bid and self.ask and m):
            return None
        return (self.ask - self.bid) / m * 100


@dataclass(frozen=True)
class OptionChain:
    symbol: str
    expiry: date
    spot: float
    ts: datetime
    rows: tuple[OptionQuoteRow, ...]

    def strikes(self) -> list[float]:
        return sorted({r.strike for r in self.rows})

    def get(self, strike: float, option_type: str) -> OptionQuoteRow | None:
        return next(
            (r for r in self.rows if r.strike == strike and r.option_type == option_type), None
        )

    def atm_strike(self) -> float:
        return min(self.strikes(), key=lambda k: abs(k - self.spot))


class OptionDataSource(Protocol):
    """`as_of` is required for history (no look-ahead) and ignored by the live source."""

    def expiries(self, symbol: str, as_of: date | None = None) -> list[date]: ...

    def chain(self, symbol: str, expiry: date, as_of: date | None = None) -> OptionChain | None: ...

    def spot(self, symbol: str, as_of: date | None = None) -> float | None: ...


class StaticOptionData:
    """In-memory source built from chains, keyed by (symbol, expiry, as_of)."""

    def __init__(self, chains: list[tuple[date | None, OptionChain]]) -> None:
        self._chains = chains

    def _visible(self, symbol: str, as_of: date | None) -> list[OptionChain]:
        return [
            c
            for d, c in self._chains
            if c.symbol == symbol and (as_of is None or d is None or d <= as_of)
        ]

    def expiries(self, symbol: str, as_of: date | None = None) -> list[date]:
        return sorted({c.expiry for c in self._visible(symbol, as_of)})

    def chain(self, symbol: str, expiry: date, as_of: date | None = None) -> OptionChain | None:
        matches = [c for c in self._visible(symbol, as_of) if c.expiry == expiry]
        return max(matches, key=lambda c: c.ts) if matches else None

    def spot(self, symbol: str, as_of: date | None = None) -> float | None:
        visible = self._visible(symbol, as_of)
        return max(visible, key=lambda c: c.ts).spot if visible else None
