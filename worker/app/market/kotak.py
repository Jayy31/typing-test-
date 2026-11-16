"""Kotak Neo market-data client (READ-ONLY). See docs/notes/kotak-api.md.

Market data (scrip master, quotes, expiries, option chain) needs only the access token from the
Neo app (Trade API dashboard) in the `Authorization` header. The daily TOTP + MPIN login is used
for one thing: learning `baseUrl`, the host every data call goes to. The session token and sid it
returns are discarded at once and never sent anywhere, so the order / portfolio APIs (which need
them, plus a whitelisted static IP since 1 April 2026) are unreachable from this code.

Every request passes `_check_url`: only the two login endpoints, the four market-data paths and
the scrip-master CSV downloads are allowed; anything else raises before a byte is sent. There is
deliberately no method that could place, modify or cancel an order (tests/test_kotak.py).

Field names come from Kotak's REST docs (github.com/Kotak-Neo/Kotak-Neo/docs, read 2026-11-16)
and are UNVERIFIED against live responses: the first raw response of each endpoint per day is
saved for checking (see `save_sample`). Parsers are pure functions so they can be fixed quickly.
"""

import base64
import csv
import hashlib
import hmac
import io
import json
import logging
import struct
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import IST, get_settings
from app.market.options import OptionChain, OptionQuoteRow

log = logging.getLogger(__name__)
LOGIN_HOST = "https://mis.kotaksecurities.com"
LOGIN_PATHS = ("/login/1.0/tradeApiLogin", "/login/1.0/tradeApiValidate")
DATA_PATHS = (
    "/script-details/1.0/masterscrip/file-paths",
    "/script-details/1.0/quotes/neosymbol/",
    "/market-data/1.0/watchlist/expiries",
    "/market-data/1.0/watchlist/option-chain",
)
SCRIP_HOST = "lapi.kotaksecurities.com"
NEO_FIN_KEY = "neotradeapi"  # static value from the docs (login APIs only)
NSE_FO_EPOCH_OFFSET = 315511200  # nse_fo lExpiryDate: add this, then read as a Unix time (docs)
QUOTE_BATCH = 50


class KotakError(RuntimeError):
    """Kotak call failed. Messages never contain the access token, MPIN or TOTP secret."""


class KotakBlocked(KotakError):
    """A request outside the read-only allowlist (never sent)."""


@dataclass(frozen=True)
class Quote:
    instrument_key: str
    last_price: float
    prev_close: float | None
    bid: float | None
    ask: float | None
    ts: datetime
    bid_qty: int | None = None  # best level
    ask_qty: int | None = None
    bid_qty_depth: int | None = None  # all depth levels shown (up to 5)
    ask_qty_depth: int | None = None


@dataclass(frozen=True)
class FnoInstrument:
    symbol: str
    lot_size: int
    expiries: tuple[date, ...]  # monthly stock-option expiries, ascending


# ---- TOTP (RFC 6238, 30 s, 6 digits, SHA-1: what authenticator apps use) --------------------
def totp(secret: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret.replace(" ", "").upper() + "=" * (-len(secret) % 8))
    counter = int((time.time() if at is None else at) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off : off + 4])[0] & 0x7FFFFFFF) % 10**digits
    return str(code).zfill(digits)


# ---- pure parsers ---------------------------------------------------------------------
def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # drop NaN


def _int(x: Any) -> int | None:
    v = _f(x)
    return int(v) if v is not None else None


def _pos(x: Any) -> float | None:
    v = _f(x)
    return v if v and v > 0 else None


def fo_expiry(raw: Any) -> date | None:
    """nse_fo lExpiryDate -> IST date (docs: add 315511200 to the epoch value)."""
    v = _f(raw)
    if v is None or v <= 0:
        return None
    return datetime.fromtimestamp(v + NSE_FO_EPOCH_OFFSET, UTC).astimezone(IST).date()


def _rows(csv_text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    # header names are used as given, minus stray spaces and trailing ';' ("dStrikePrice;")
    return [
        {(k or "").strip().rstrip(";"): (v or "").strip() for k, v in r.items()} for r in reader
    ]


def parse_cash_master(csv_text: str, symbols: set[str]) -> dict[str, str]:
    """nse_cm scrip master -> {symbol: 'nse_cm|<pSymbol>'} for the regular EQ series."""
    out: dict[str, str] = {}
    fallback: dict[str, str] = {}
    for r in _rows(csv_text):
        name, token = r.get("pSymbolName", ""), r.get("pSymbol", "")
        if name not in symbols or not token:
            continue
        if r.get("pTrdSymbol", "") == f"{name}-EQ":
            out[name] = f"nse_cm|{token}"
        else:
            fallback.setdefault(name, f"nse_cm|{token}")
    return {**fallback, **out}


def parse_fo_master(csv_text: str, symbols: set[str]) -> dict[str, FnoInstrument]:
    """nse_fo scrip master -> monthly stock-option expiries and lot size per symbol. Only the
    LAST expiry of each month is kept: we trade monthly stock options only."""
    exp: dict[str, set[date]] = {}
    lots: dict[str, int] = {}
    for r in _rows(csv_text):
        sym = r.get("pSymbolName", "")
        if sym not in symbols or r.get("pOptionType") not in ("CE", "PE"):
            continue
        d = fo_expiry(r.get("lExpiryDate"))
        if d is None:
            continue
        exp.setdefault(sym, set()).add(d)
        lot = _int(r.get("lLotSize"))
        if lot:
            lots[sym] = lot
    out: dict[str, FnoInstrument] = {}
    for sym, dates in exp.items():
        monthly: dict[tuple[int, int], date] = {}
        for d in dates:
            monthly[(d.year, d.month)] = max(monthly.get((d.year, d.month), d), d)
        out[sym] = FnoInstrument(sym, lots.get(sym, 0), tuple(sorted(monthly.values())))
    return out


def parse_quotes(raw: Any, ts: datetime) -> dict[str, Quote]:
    """Quotes API (a JSON list) -> {'<exchange>|<exchange_token>': Quote}. ohlc.close is the
    previous close (docs); depth holds the top 5 levels each side."""
    out: dict[str, Quote] = {}
    for e in raw if isinstance(raw, list) else (raw or {}).get("data") or []:
        if not isinstance(e, dict):
            continue
        key = f"{e.get('exchange')}|{e.get('exchange_token')}"
        last = _pos(e.get("ltp"))
        if last is None:
            continue
        depth = e.get("depth") or {}
        buy = [x for x in depth.get("buy") or [] if _pos(x.get("price"))]
        sell = [x for x in depth.get("sell") or [] if _pos(x.get("price"))]
        out[key] = Quote(
            key,
            last,
            _pos((e.get("ohlc") or {}).get("close")),
            _pos(buy[0]["price"]) if buy else None,
            _pos(sell[0]["price"]) if sell else None,
            ts,
            bid_qty=_int(buy[0].get("quantity")) if buy else None,
            ask_qty=_int(sell[0].get("quantity")) if sell else None,
            bid_qty_depth=sum(_int(x.get("quantity")) or 0 for x in buy) or None,
            ask_qty_depth=sum(_int(x.get("quantity")) or 0 for x in sell) or None,
        )
    return out


def parse_option_chain(
    raw: dict[str, Any], symbol: str, expiry: date, spot: float, ts: datetime
) -> OptionChain:
    """Option chain API -> OptionChain. Kotak gives LTP, OHLC, volume and open interest per strike
    but no bid/ask or IV: bid/ask come from a depth quote, IV is computed (app/market/live.py)."""
    data = raw.get("data") or {}
    rows: list[OptionQuoteRow] = []
    for side, opt in (("call", "CE"), ("put", "PE")):
        for leg in data.get(side) or []:
            inst, q, oi = (
                leg.get("instrument") or {},
                leg.get("quote") or {},
                leg.get("openInterest") or {},
            )
            strike = _f(inst.get("strikePrice"))
            if strike is None or (inst.get("optionType") or opt) != opt:
                continue
            rows.append(
                OptionQuoteRow(
                    symbol=symbol,
                    expiry=expiry,
                    strike=strike,
                    option_type=opt,
                    bid=None,
                    ask=None,
                    ltp=_pos(q.get("ltp")),
                    iv=None,
                    oi=_int(oi.get("current")),
                    volume=_int(q.get("volume")),
                    spot=spot,
                    ts=ts,
                    instrument_key=inst.get("neoSymbol"),
                )
            )
    return OptionChain(symbol, expiry, spot, ts, tuple(rows))


def chain_lot(raw: dict[str, Any]) -> int | None:
    return _int(((raw.get("data") or {}).get("common_data") or {}).get("mktLot"))


def parse_futures(raw: dict[str, Any]) -> dict[date, dict[str, float]]:
    """Futures chain (instrument_type=fut) -> {expiry: {last, prev_close, oi, chg_oi}}."""
    out: dict[date, dict[str, float]] = {}
    for e in (raw.get("data") or {}).get("fut") or []:
        inst, q, oi = e.get("inst") or {}, e.get("quote") or {}, e.get("oi") or {}
        try:
            d = datetime.strptime(str(inst.get("expiryDt")), "%d-%b-%Y").date()
        except ValueError:
            continue
        vals = {
            "last": _pos(q.get("ltp")),
            "prev_close": _pos(q.get("pc")),
            "oi": _f(oi.get("cur")),
            "chg_oi": _f(oi.get("chg")),
        }
        out[d] = {k: v for k, v in vals.items() if v is not None}
    return out


# ---- client ---------------------------------------------------------------------------
def _check_url(method: str, url: str, base_url: str | None) -> None:
    """The read-only allowlist. Raises KotakBlocked for anything else (nothing is sent)."""
    u = urlsplit(url)
    if u.scheme != "https":
        raise KotakBlocked(f"{method} blocked: not https")
    if method == "POST":
        if f"{u.scheme}://{u.netloc}" == LOGIN_HOST and u.path in LOGIN_PATHS:
            return
        raise KotakBlocked(f"POST blocked: {u.netloc}{u.path} is not a login endpoint")
    if method != "GET":
        raise KotakBlocked(f"{method} blocked")
    if u.netloc == SCRIP_HOST and u.path.endswith(".csv"):
        return
    if base_url:
        b = urlsplit(base_url.rstrip("/"))
        if u.netloc == b.netloc and u.path.startswith(b.path):
            rest = u.path[len(b.path) :]
            if any(rest == p or (p.endswith("/") and rest.startswith(p)) for p in DATA_PATHS):
                return
    raise KotakBlocked(f"GET blocked: {u.netloc}{u.path} is not a market-data endpoint")


class KotakClient:
    def __init__(
        self,
        base_url: str | None = None,
        http: httpx.Client | None = None,
        min_interval_s: float = 0.2,
        settings: Any = None,
    ) -> None:
        self._st = settings or get_settings()
        self.base_url = (base_url or self._st.kotak_base_url or "").rstrip("/") or None
        self._http = http or httpx.Client(timeout=20.0)
        self._min_interval = min_interval_s  # 5 req/s; Kotak allows 10 across all APIs
        self._last = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._st.kotak_access_token)

    @property
    def login_configured(self) -> bool:
        st = self._st
        return all(
            (
                st.kotak_access_token,
                st.kotak_mobile,
                st.kotak_ucc,
                st.kotak_mpin,
                st.kotak_totp_secret,
            )
        )

    def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _send(self, method: str, url: str, headers: dict[str, str], body: Any = None) -> Any:
        _check_url(method, url, self.base_url)
        self._throttle()
        path = urlsplit(url).path
        try:
            if method == "POST":
                resp = self._http.post(url, json=body, headers=headers)
            else:
                resp = self._http.get(url, headers=headers)
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise KotakError(f"{method} {path} failed: {type(exc).__name__}") from None
        bad = isinstance(data, dict) and (
            data.get("stat") == "Not_Ok" or data.get("status") == "error"
        )
        if resp.status_code != 200 or bad:
            msg = data.get("message") or data.get("emsg") if isinstance(data, dict) else None
            raise KotakError(f"{method} {path} failed: HTTP {resp.status_code} {str(msg)[:160]}")
        return data

    # -- login: only to learn baseUrl ------------------------------------------------------
    def login(self) -> dict[str, str]:
        """TOTP + MPIN login. Returns {'base_url', 'feed_url'}; the session token and sid are
        dropped here and never stored or sent."""
        st = self._st
        if not self.login_configured:
            raise KotakError("Kotak login keys are not all set (KOTAK_*)")
        mobile = st.kotak_mobile.strip()
        mobile = mobile if mobile.startswith("+") else f"+91{mobile[-10:]}"
        head = {
            "Authorization": st.kotak_access_token,
            "neo-fin-key": NEO_FIN_KEY,
            "Content-Type": "application/json",
        }
        step1 = None
        for attempt in range(2):  # a code at the very end of its 30 s window can expire in flight
            try:
                step1 = self._send(
                    "POST",
                    LOGIN_HOST + LOGIN_PATHS[0],
                    head,
                    {
                        "mobileNumber": mobile,
                        "ucc": st.kotak_ucc,
                        "totp": totp(st.kotak_totp_secret),
                    },
                )
                break
            except KotakError:
                if attempt:
                    raise
                time.sleep(31 - time.time() % 30)
        view = (step1 or {}).get("data") or {}
        if not view.get("token") or not view.get("sid"):
            raise KotakError("TOTP login returned no view token")
        step2 = self._send(
            "POST",
            LOGIN_HOST + LOGIN_PATHS[1],
            {**head, "sid": str(view["sid"]), "Auth": str(view["token"])},
            {"mpin": st.kotak_mpin},
        )
        data = step2.get("data") or {}
        base = str(data.get("baseUrl") or "").rstrip("/")
        if not base.startswith("https://"):
            raise KotakError("MPIN login returned no baseUrl")
        self.base_url = base
        return {"base_url": base, "feed_url": str(data.get("feedUrl") or "")}

    # -- market data (access token only) ---------------------------------------------------
    def get(self, path: str, query: str = "", sample: str | None = None) -> Any:
        if not self.configured:
            raise KotakError("KOTAK_ACCESS_TOKEN is not set")
        if not self.base_url:
            raise KotakError("no Kotak baseUrl yet (daily login has not run)")
        headers = {"Authorization": self._st.kotak_access_token, "Content-Type": "application/json"}
        body = self._send("GET", f"{self.base_url}{path}{query}", headers)
        save_sample(sample or path, body)
        return body

    def scrip_files(self) -> list[str]:
        body = self.get(DATA_PATHS[0])
        return [str(x) for x in ((body.get("data") or {}).get("filesPaths") or [])]

    def download(self, url: str) -> str:
        _check_url("GET", url, self.base_url)
        try:
            resp = self._http.get(url, timeout=120.0)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise KotakError(f"scrip master download failed: {type(exc).__name__}") from None
        return resp.text

    def quotes(self, keys: list[str]) -> list[Any]:
        out: list[Any] = []
        for i in range(0, len(keys), QUOTE_BATCH):
            body = self.get(DATA_PATHS[1], ",".join(keys[i : i + QUOTE_BATCH]) + "/all")
            out += body if isinstance(body, list) else (body.get("data") or [])
        return out

    def option_chain(self, underlying: str, expiry: date | None, kind: str, count: int) -> Any:
        q = f"?exchange=nse_fo&underlying={underlying}&instrument_type={kind}"
        if expiry:
            q += f"&expiry={expiry.isoformat()}"
        if kind == "option":
            q += f"&count={count}"
        return self.get(DATA_PATHS[3], q, sample=f"{DATA_PATHS[3]}_{kind}")


def save_sample(path: str, body: Any) -> None:
    """Keep the first raw response per endpoint per day, to verify field names after deploy."""
    try:
        folder = Path(get_settings().history_data_dir) / "raw_samples" / "kotak"
        name = f"{date.today().isoformat()}_{path.strip('/').replace('/', '_')}.json"
        target = folder / name
        if not target.exists():
            folder.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(body)[:2_000_000])
    except OSError:
        pass  # samples are a convenience; never fail a data call because of them
