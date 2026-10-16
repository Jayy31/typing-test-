"""Live market data: Upstox instrument sync, option-chain polling and storage, IV history."""

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.collectors.base import Collector
from app.config import IST, get_settings
from app.db.config_store import get_config, set_config
from app.db.models import Instrument, OptionQuote, Signal
from app.db.session import session_scope
from app.market.options import OptionChain, OptionQuoteRow
from app.market.upstox import (
    FnoInstrument,
    Quote,
    UpstoxClient,
    parse_instruments,
    parse_option_chain,
    parse_quotes,
)
from app.signals.lifecycle import LIVE
from app.timeutil import as_ist
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
EXPIRIES_KEY = "fno_expiries"


def market_open(now: datetime) -> bool:
    hours = load_yaml("options.yaml")["market_hours"]
    t = now.strftime("%H:%M")
    return now.weekday() < 5 and hours["open"] <= t <= hours["close"]


class UpstoxInstrumentsCollector(Collector[Any]):
    """Daily: map our symbols to Upstox keys/ISINs, real lot sizes and monthly expiries."""

    name = "upstox_instruments"
    timeout_s = 60.0

    def __init__(self, client: UpstoxClient | None = None) -> None:
        self.client = client or UpstoxClient()

    def fetch(self) -> Any:
        return self.client.instruments()

    def parse(self, raw: Any) -> list[Any]:
        return [raw]

    def store(self, items: list[Any], engine: Engine | None) -> int:
        with session_scope(engine) as s:
            insts = {i.symbol: i for i in s.scalars(select(Instrument))}
            equities, fno = parse_instruments(items[0], set(insts))
            if not equities:
                raise RuntimeError("instrument master had no matching NSE equities; check format")
            for sym, inst in insts.items():
                if sym in equities:
                    inst.instrument_key, inst.isin = equities[sym]
                f = fno.get(sym)
                inst.is_fno = bool(f and f.expiries)
                if f and f.lot_size:
                    inst.lot_size = f.lot_size
            set_config(
                s, EXPIRIES_KEY, {k: [d.isoformat() for d in v.expiries] for k, v in fno.items()}
            )
            return len(equities)


class UpstoxOptionData:
    """OptionDataSource backed by Upstox (live). `as_of` is ignored: live data only."""

    def __init__(self, engine: Engine | None = None, client: UpstoxClient | None = None) -> None:
        self.engine = engine
        self.client = client or UpstoxClient()

    @property
    def available(self) -> bool:
        return self.client.configured

    def _key(self, symbol: str) -> str | None:
        with session_scope(self.engine) as s:
            inst = s.get(Instrument, symbol)
            return inst.instrument_key if inst else None

    def expiries(self, symbol: str, as_of: date | None = None) -> list[date]:
        today = as_of or datetime.now(IST).date()
        with session_scope(self.engine) as s:
            raw = (get_config(s, EXPIRIES_KEY, {}) or {}).get(symbol, [])
        return [d for d in (date.fromisoformat(x) for x in raw) if d >= today]

    def chain(self, symbol: str, expiry: date, as_of: date | None = None) -> OptionChain | None:
        key = self._key(symbol)
        if not key:
            return None
        raw = self.client.option_chain(key, expiry)
        chain = parse_option_chain(raw, symbol, expiry, datetime.now(IST))
        return chain if chain.rows and chain.spot > 0 else None

    def quote(self, symbol: str) -> Quote | None:
        key = self._key(symbol)
        if not key:
            return None
        return parse_quotes(self.client.quotes([key]), datetime.now(IST)).get(key)

    def with_depth(self, row: OptionQuoteRow) -> OptionQuoteRow:
        """Fallback when the chain carries no bid/ask quantities: read the option's own
        market-depth quote (best level) for the fill check. Used only at entry and exit."""
        if (row.bid_qty is not None and row.ask_qty is not None) or not row.instrument_key:
            return row
        try:
            q = parse_quotes(self.client.quotes([row.instrument_key]), datetime.now(IST)).get(
                row.instrument_key
            )
        except Exception as exc:  # noqa: BLE001 - fill check degrades to "unknown"
            log.warning("depth fallback failed for %s: %s", row.instrument_key, exc)
            return row
        if q is None:
            return row
        return replace(
            row,
            bid_qty=row.bid_qty if row.bid_qty is not None else q.bid_qty,
            ask_qty=row.ask_qty if row.ask_qty is not None else q.ask_qty,
        )

    def spot(self, symbol: str, as_of: date | None = None) -> float | None:
        q = self.quote(symbol)
        return q.last_price if q else None


def store_chain(s: Session, chain: OptionChain) -> int:
    """Persist the strikes around ATM (config strikes_each_side) to option_quotes."""
    n = int(load_yaml("options.yaml")["polling"]["strikes_each_side"])
    strikes = chain.strikes()
    atm = strikes.index(chain.atm_strike())
    keep = set(strikes[max(0, atm - n) : atm + n + 1])
    added = 0
    for r in chain.rows:
        if r.strike in keep:
            s.add(
                OptionQuote(
                    ts=r.ts,
                    symbol=r.symbol,
                    expiry=r.expiry,
                    strike=r.strike,
                    type=r.option_type,
                    bid=r.bid,
                    ask=r.ask,
                    ltp=r.ltp,
                    iv=r.iv,
                    oi=r.oi,
                    volume=r.volume,
                    spot=chain.spot,
                    bid_qty=r.bid_qty,
                    ask_qty=r.ask_qty,
                )
            )
            added += 1
    return added


def atm_iv(chain: OptionChain) -> float | None:
    """Average of ATM call and put IV (in %), when available."""
    k = chain.atm_strike()
    ivs = [r.iv for r in (chain.get(k, "CE"), chain.get(k, "PE")) if r and r.iv]
    return sum(ivs) / len(ivs) if ivs else None


def iv_percentile(s: Session, symbol: str, current_iv: float, now: datetime) -> float | None:
    """Percentile of the current ATM IV within this stock's own daily ATM IV history."""
    cfg = load_yaml("options.yaml")["iv"]
    since = now - timedelta(days=int(cfg["lookback_days"]))
    rows = s.execute(
        select(OptionQuote.ts, OptionQuote.strike, OptionQuote.spot, OptionQuote.iv).where(
            OptionQuote.symbol == symbol, OptionQuote.ts >= since, OptionQuote.iv.is_not(None)
        )
    ).all()
    daily: dict[date, tuple[float, float]] = {}  # day -> (distance to spot, iv): nearest-ATM sample
    for ts, strike, spot, iv in rows:
        if not spot or iv is None:
            continue
        d, dist = as_ist(ts).date(), abs(strike - spot)
        if d not in daily or dist < daily[d][0]:
            daily[d] = (dist, float(iv))
    history = [iv for _, iv in daily.values()]
    if len(history) < int(cfg["min_samples"]):
        return None
    return 100.0 * sum(1 for h in history if h <= current_iv) / len(history)


def _poll(
    engine: Engine | None, symbols: list[str], source: UpstoxOptionData, n_expiries: int
) -> int:
    stored = 0
    for sym in symbols:
        for expiry in source.expiries(sym)[:n_expiries]:
            try:
                chain = source.chain(sym, expiry)
            except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the poll
                log.warning("chain %s %s failed: %s", sym, expiry, exc)
                continue
            if chain:
                with session_scope(engine) as s:
                    stored += store_chain(s, chain)
    return stored


def poll_active_options(
    engine: Engine | None = None, source: UpstoxOptionData | None = None
) -> int:
    """Every OPTION_POLL_ACTIVE_MIN in market hours: stocks with live signals (open trades: 3b)."""
    source = source or UpstoxOptionData(engine)
    if not source.available or not market_open(datetime.now(IST)):
        return 0
    with session_scope(engine) as s:
        symbols = sorted(set(s.scalars(select(Signal.symbol).where(Signal.status.in_(LIVE)))))
    return _poll(engine, symbols, source, 2)


def poll_universe_options(
    engine: Engine | None = None, source: UpstoxOptionData | None = None
) -> int:
    """Twice a day: nearest monthly chain for every F&O stock (feeds IV history)."""
    source = source or UpstoxOptionData(engine)
    if not source.available:
        return 0
    with session_scope(engine) as s:
        symbols = sorted(s.scalars(select(Instrument.symbol).where(Instrument.is_fno)))
    return _poll(engine, symbols, source, 1)


def configured_poll_minutes() -> int:
    return max(1, int(get_settings().option_poll_active_min))


def sync_instruments_job(engine: Engine | None = None) -> int:
    if not UpstoxClient().configured:
        return 0
    return UpstoxInstrumentsCollector().run(engine)


__all__ = ["FnoInstrument", "Quote"]
