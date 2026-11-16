"""Live market data: Kotak Neo daily login (base URL only), scrip master sync, option-chain
polling and storage, IV history."""

import logging
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.collectors.base import Collector
from app.config import IST, get_settings
from app.db.config_store import get_config, set_config
from app.db.models import Instrument, OptionQuote, Signal
from app.db.session import session_scope
from app.market.kotak import (
    FnoInstrument,
    KotakClient,
    Quote,
    chain_lot,
    parse_cash_master,
    parse_fo_master,
    parse_futures,
    parse_option_chain,
    parse_quotes,
)
from app.market.options import OptionChain, OptionQuoteRow
from app.pricing.bs import implied_vol
from app.signals.lifecycle import LIVE
from app.timeutil import as_ist
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
EXPIRIES_KEY = "fno_expiries"
SESSION_KEY = "kotak.session"  # {base_url, feed_url, day, ok, error}: no tokens are ever stored


def market_open(now: datetime) -> bool:
    hours = load_yaml("options.yaml")["market_hours"]
    t = now.strftime("%H:%M")
    return now.weekday() < 5 and hours["open"] <= t <= hours["close"]


def kotak_cfg() -> dict[str, Any]:
    return dict(load_yaml("options.yaml")["kotak"])


def stored_base_url(engine: Engine | None) -> str | None:
    with session_scope(engine) as s:
        return (get_config(s, SESSION_KEY, {}) or {}).get("base_url")


def login_job(engine: Engine | None = None, client: KotakClient | None = None) -> int:
    """Daily 08:30 (and on demand): TOTP + MPIN login to learn the data base URL. A failure keeps
    yesterday's base URL (Kotak: 'stable for the day and rarely changes') and raises a health
    alert through the job runner."""
    client = client or KotakClient()
    if not client.login_configured:
        return 0
    now = datetime.now(IST)
    try:
        info = client.login()
    except Exception as exc:
        with session_scope(engine) as s:
            prev = get_config(s, SESSION_KEY, {}) or {}
            set_config(
                s,
                SESSION_KEY,
                {**prev, "ok": False, "error": str(exc)[:200], "at": now.isoformat()},
            )
        raise
    with session_scope(engine) as s:
        set_config(
            s,
            SESSION_KEY,
            {
                **info,
                "day": now.date().isoformat(),
                "ok": True,
                "error": None,
                "at": now.isoformat(),
            },
        )
    return 1


def client_for(engine: Engine | None) -> KotakClient:
    c = KotakClient(base_url=stored_base_url(engine))
    if c.configured and not c.base_url and c.login_configured:
        login_job(engine, c)
    return c


class KotakInstrumentsCollector(Collector[Any]):
    """Daily: map our symbols to Kotak tokens (spot quotes), real lot sizes and monthly expiries
    from the scrip master CSVs (nse_cm, nse_fo)."""

    name = "kotak_instruments"
    timeout_s = 180.0

    def __init__(self, client: KotakClient | None = None) -> None:
        self.client = client

    def fetch(self) -> Any:
        c = self.client or client_for(None)
        files = c.scrip_files()
        cm = next((u for u in files if "/nse_cm" in u), None)
        fo = next((u for u in files if u.endswith("/nse_fo.csv")), None)
        if not cm or not fo:
            raise RuntimeError("scrip master file list has no nse_cm / nse_fo file")
        return {"cm": c.download(cm), "fo": c.download(fo)}

    def parse(self, raw: Any) -> list[Any]:
        return [raw]

    def store(self, items: list[Any], engine: Engine | None) -> int:
        with session_scope(engine) as s:
            insts = {i.symbol: i for i in s.scalars(select(Instrument))}
            equities = parse_cash_master(items[0]["cm"], set(insts))
            fno = parse_fo_master(items[0]["fo"], set(insts))
            if not equities:
                raise RuntimeError("scrip master had no matching NSE equities; check format")
            for sym, inst in insts.items():
                if sym in equities:
                    inst.instrument_key = equities[sym]
                f = fno.get(sym)
                inst.is_fno = bool(f and f.expiries)
                if f and f.lot_size:
                    inst.lot_size = f.lot_size
            set_config(
                s, EXPIRIES_KEY, {k: [d.isoformat() for d in v.expiries] for k, v in fno.items()}
            )
            return len(equities)


def years_to(expiry: date, now: datetime) -> float:
    end = datetime.combine(expiry, time(15, 30), IST)
    return max((end - now).total_seconds(), 0.0) / (365.0 * 86400)


def with_iv(row: OptionQuoteRow, spot: float, now: datetime) -> OptionQuoteRow:
    """Kotak sends no IV: solve it (in %) from the mid, else the last price."""
    if row.bid and row.ask:
        px: float | None = (row.bid + row.ask) / 2
    elif row.volume:
        px = row.ltp
    else:
        return row  # last price of a contract not traded today is stale: no IV from it
    t = years_to(row.expiry, now)
    if not px or not spot or t <= 0:
        return row
    iv = implied_vol(px, spot, row.strike, t, float(get_settings().risk_free_rate), row.option_type)
    return replace(row, iv=round(iv * 100, 3) if iv else None)


class LiveOptionData:
    """OptionDataSource (and FuturesQuoteSource) backed by Kotak Neo market data. `as_of` is
    ignored: live data only."""

    def __init__(self, engine: Engine | None = None, client: KotakClient | None = None) -> None:
        self.engine = engine
        self._client = client

    @property
    def client(self) -> KotakClient:
        if self._client is None:
            self._client = client_for(self.engine)
        return self._client

    @property
    def available(self) -> bool:
        return bool(self._client.configured if self._client else get_settings().kotak_access_token)

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
        """Chain (LTP, OI, volume per strike) + spot quote + one batched depth quote for the
        strikes around ATM (bid/ask and quantities), then IV solved per row."""
        q = self.quote(symbol)
        if q is None:
            return None
        now = datetime.now(IST)
        raw = self.client.option_chain(symbol, expiry, "option", int(kotak_cfg()["chain_count"]))
        chain = parse_option_chain(raw, symbol, expiry, q.last_price, now)
        if not chain.rows:
            return None
        n = int(load_yaml("options.yaml")["polling"]["strikes_each_side"])
        strikes = chain.strikes()
        atm = strikes.index(chain.atm_strike())
        near = set(strikes[max(0, atm - n) : atm + n + 1])
        keys = [r.instrument_key for r in chain.rows if r.strike in near and r.instrument_key]
        try:
            depth = parse_quotes(self.client.quotes(keys), now) if keys else {}
        except Exception as exc:  # noqa: BLE001 - chain still usable on LTP; fills read "unknown"
            log.warning("depth quotes %s %s failed: %s", symbol, expiry, exc)
            depth = {}
        rows = []
        for r in chain.rows:
            d = depth.get(r.instrument_key or "")
            if d:
                r = replace(
                    r,
                    bid=d.bid,
                    ask=d.ask,
                    bid_qty=d.bid_qty,
                    ask_qty=d.ask_qty,
                    ltp=r.ltp or d.last_price,
                )
            rows.append(with_iv(r, q.last_price, now))
        lot = chain_lot(raw)
        if lot:
            self._note_lot(symbol, lot)
        return OptionChain(symbol, expiry, q.last_price, now, tuple(rows))

    def _note_lot(self, symbol: str, lot: int) -> None:
        with session_scope(self.engine) as s:
            inst = s.get(Instrument, symbol)
            if inst and inst.lot_size != lot:
                inst.lot_size = lot

    def quote(self, symbol: str) -> Quote | None:
        key = self._key(symbol)
        if not key:
            return None
        return parse_quotes(self.client.quotes([key]), datetime.now(IST)).get(key)

    def with_depth(self, row: OptionQuoteRow) -> OptionQuoteRow:
        """When a row has no bid/ask quantities (outside the depth window), read the option's own
        depth quote. Used only at entry and exit (fill check)."""
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
            bid=row.bid if row.bid is not None else q.bid,
            ask=row.ask if row.ask is not None else q.ask,
            bid_qty=row.bid_qty if row.bid_qty is not None else q.bid_qty,
            ask_qty=row.ask_qty if row.ask_qty is not None else q.ask_qty,
        )

    def spot(self, symbol: str, as_of: date | None = None) -> float | None:
        q = self.quote(symbol)
        return q.last_price if q else None

    def future(self, symbol: str, expiry: date) -> dict[str, float] | None:
        """Stock future of the same expiry month: {last, prev_close, oi, chg_oi}."""
        raw = self.client.option_chain(symbol, None, "fut", 0)
        for d, vals in parse_futures(raw).items():
            if (d.year, d.month) == (expiry.year, expiry.month):
                return vals or None
        return None


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
    since = now - timedelta(days=max(int(cfg["lookback_days"]), _hist_lookback()))
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
    merged: dict[date, float] = dict(hist_atm_ivs(symbol, now))  # backfilled history (Phase 4)
    merged.update({d: iv for d, (_, iv) in daily.items()})  # live quotes win on overlapping days
    history = list(merged.values())
    if len(history) < int(cfg["min_samples"]):
        return None
    return 100.0 * sum(1 for h in history if h <= current_iv) / len(history)


def _hist_lookback() -> int:
    return int(load_yaml("history.yaml")["iv_backfill"]["lookback_days"])


def hist_atm_ivs(symbol: str, now: datetime) -> dict[date, float]:
    """Backfilled daily ATM IV (bhavcopy) for the lookback window; empty if unavailable."""
    from app.history.store import connect

    since = (now - timedelta(days=_hist_lookback())).date()
    try:
        with connect() as con:
            rows = con.execute(
                "SELECT date, atm_iv FROM hist_atm_iv WHERE symbol = ? AND date >= ?",
                [symbol, since],
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - history is optional (store busy/missing)
        log.info("IV history unavailable: %s", exc)
        return {}
    return {d: float(v) for d, v in rows}


def _poll(
    engine: Engine | None, symbols: list[str], source: LiveOptionData, n_expiries: int
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


def poll_active_options(engine: Engine | None = None, source: LiveOptionData | None = None) -> int:
    """Every OPTION_POLL_ACTIVE_MIN in market hours: stocks with live signals (open trades: 3b)."""
    source = source or LiveOptionData(engine)
    if not source.available or not market_open(datetime.now(IST)):
        return 0
    with session_scope(engine) as s:
        symbols = sorted(set(s.scalars(select(Signal.symbol).where(Signal.status.in_(LIVE)))))
    return _poll(engine, symbols, source, 2)


def poll_universe_options(
    engine: Engine | None = None, source: LiveOptionData | None = None
) -> int:
    """Twice a day: nearest monthly chain for every F&O stock (feeds IV history)."""
    source = source or LiveOptionData(engine)
    if not source.available:
        return 0
    with session_scope(engine) as s:
        symbols = sorted(s.scalars(select(Instrument.symbol).where(Instrument.is_fno)))
    return _poll(engine, symbols, source, 1)


def configured_poll_minutes() -> int:
    return max(1, int(get_settings().option_poll_active_min))


def sync_instruments_job(engine: Engine | None = None) -> int:
    if not get_settings().kotak_access_token:
        return 0
    return KotakInstrumentsCollector(client_for(engine)).run(engine)


__all__ = ["FnoInstrument", "Quote"]
