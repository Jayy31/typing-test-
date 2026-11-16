"""Kotak Neo market data (read-only): TOTP, login (base URL only), allowlist, parsers, live source.
Sample payloads follow the official REST docs (github.com/Kotak-Neo/Kotak-Neo/docs)."""

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, select

from app.config import IST
from app.db.config_store import get_config
from app.db.models import Instrument, OptionQuote
from app.db.session import session_scope
from app.market import live
from app.market.implied import edge_ratio, event_iv_premium, n_day_move_pct
from app.market.kotak import (
    KotakBlocked,
    KotakClient,
    KotakError,
    _check_url,
    fo_expiry,
    parse_cash_master,
    parse_fo_master,
    parse_futures,
    parse_option_chain,
    parse_quotes,
    totp,
)
from app.market.live import (
    KotakInstrumentsCollector,
    LiveOptionData,
    iv_percentile,
    login_job,
    store_chain,
)
from tests.helpers import seed_instruments

TS = datetime(2026, 10, 16, 11, 0, tzinfo=IST)
BASE = "https://cis.kotaksecurities.com"
TOKEN = "neo-access-SECRET-token"
MPIN = "654321"
SECRET = "JBSWY3DPEHPK3PXP"


def settings(**kw: Any) -> SimpleNamespace:
    base = dict(
        kotak_access_token=TOKEN,
        kotak_mobile="9876543210",
        kotak_ucc="ABC12",
        kotak_mpin=MPIN,
        kotak_totp_secret=SECRET,
        kotak_base_url="",
        history_data_dir="/nonexistent",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def fo_epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 14, 30, tzinfo=IST).timestamp()) - 315511200


FO_CSV = (
    "pSymbol,pExchSeg,pTrdSymbol,pSymbolName,pOptionType,lLotSize,lExpiryDate,dStrikePrice;\n"
    + "\n".join(
        f"{tok},nse_fo,TCS{d:%y%b}{k}{t},TCS,{t},175,{fo_epoch(d)},{k * 100}"
        for tok, (d, k, t) in enumerate(
            [
                (date(2026, 10, 27), 4000, "CE"),
                (date(2026, 10, 27), 4000, "PE"),
                (date(2026, 11, 24), 4100, "CE"),
                (date(2026, 11, 24), 4100, "XX"),
            ],
            start=100,
        )
    )
)
CM_CSV = (
    "pSymbol,pExchSeg,pTrdSymbol,pSymbolName\n"
    "11536,nse_cm,TCS-EQ,TCS\n"
    "99999,nse_cm,TCS-BL,TCS\n"
    "1594,nse_cm,INFY-EQ,INFY\n"
)


def quote(token: str, ltp: float, bid: float, ask: float, seg: str = "nse_fo") -> dict[str, Any]:
    return {
        "exchange_token": token,
        "exchange": seg,
        "ltp": str(ltp),
        "ohlc": {"open": "1", "high": "1", "low": "1", "close": str(ltp - 10)},
        "depth": {
            "buy": [
                {"price": str(bid), "quantity": "350", "orders": "2"},
                {"price": "0", "quantity": "0"},
            ],
            "sell": [{"price": str(ask), "quantity": "525", "orders": "3"}],
        },
    }


def chain_json() -> dict[str, Any]:
    def leg(tok: int, k: int, t: str, ltp: float) -> dict[str, Any]:
        return {
            "instrument": {
                "neoSymbol": f"nse_fo|{tok}",
                "symbol": f"TCS{k}{t}",
                "optionType": t,
                "strikePrice": str(k),
                "moneyness": "ATM",
            },
            "quote": {
                "ltp": str(ltp),
                "open": "1",
                "high": "1",
                "low": "1",
                "prevClose": "1",
                "close": None,
                "volume": 5000,
            },
            "openInterest": {
                "current": 200000,
                "previous": 180000,
                "change": 20000,
                "changePct": 11.1,
            },
        }

    strikes = range(3800, 4250, 50)
    return {
        "data": {
            "common_data": {
                "mktLot": "175",
                "multiplier": "1",
                "unlSymbol": "TCS",
                "exSeg": "nse_fo",
                "expiryDt": "2026-10-27",
            },
            "call": [leg(1000 + k, k, "CE", max(4010 - k, 0) + 40) for k in strikes],
            "put": [leg(2000 + k, k, "PE", max(k - 4010, 0) + 40) for k in strikes],
        }
    }


def fut_json() -> dict[str, Any]:
    return {
        "data": {
            "fut": [
                {
                    "inst": {
                        "neoSymbol": "nse_fo|53001",
                        "symbol": "TCS26OCTFUT",
                        "expiryDt": "27-OCT-2026",
                    },
                    "quote": {"ltp": "4030.5", "pc": "4000", "vol": "100"},
                    "oi": {"cur": "1000000", "prev": "950000", "chg": "50000", "chgPct": "5.2"},
                },
            ]
        }
    }


class Recorder:
    """httpx transport that answers like Kotak and records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path
        if path.endswith("tradeApiLogin"):
            return httpx.Response(
                200, json={"data": {"token": "VIEW", "sid": "SID1", "kType": "View"}}
            )
        if path.endswith("tradeApiValidate"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "token": "TRADE-SESSION",
                        "sid": "SID2",
                        "baseUrl": BASE,
                        "feedUrl": "wss://feed",
                        "kType": "Trade",
                    }
                },
            )
        if path.endswith("file-paths"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "filesPaths": [
                            "https://lapi.kotaksecurities.com/wso2-scripmaster/v1/prod/d/transformed/nse_fo.csv",
                            "https://lapi.kotaksecurities.com/wso2-scripmaster/v1/prod/d/transformed-v1/nse_cm-v1.csv",
                        ]
                    }
                },
            )
        if path.endswith("nse_fo.csv"):
            return httpx.Response(200, text=FO_CSV)
        if path.endswith("nse_cm-v1.csv"):
            return httpx.Response(200, text=CM_CSV)
        if "/quotes/neosymbol/" in path:
            keys = path.split("/neosymbol/")[1].rsplit("/", 1)[0].split(",")
            out = []
            for k in keys:
                seg, tok = k.split("|")
                out.append(
                    quote(tok, 4010.0, 4009.5, 4010.5, seg)
                    if seg == "nse_cm"
                    else quote(tok, 50.0, 49.5, 50.5)
                )
            return httpx.Response(200, json=out)
        if path.endswith("option-chain"):
            body = fut_json() if req.url.params.get("instrument_type") == "fut" else chain_json()
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"stat": "Not_Ok", "emsg": "unknown"})


def client(rec: Recorder, base: str | None = BASE, **kw: Any) -> KotakClient:
    return KotakClient(base, httpx.Client(transport=httpx.MockTransport(rec)), 0, settings(**kw))


def test_totp_rfc6238_vector() -> None:
    # RFC 6238 SHA-1 test secret "12345678901234567890", T=59 -> 94287082 (8 digits)
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp(secret, at=59, digits=8) == "94287082"
    assert re.fullmatch(r"\d{6}", totp(SECRET))


def test_allowlist_blocks_everything_but_login_and_market_data() -> None:
    _check_url("POST", "https://mis.kotaksecurities.com/login/1.0/tradeApiLogin", None)
    _check_url("GET", f"{BASE}/script-details/1.0/quotes/neosymbol/nse_cm|11536/all", BASE)
    _check_url(
        "GET", f"{BASE}/market-data/1.0/watchlist/option-chain?exchange=nse_fo&underlying=TCS", BASE
    )
    _check_url("GET", "https://lapi.kotaksecurities.com/x/nse_fo.csv", BASE)
    for method, url in [
        ("POST", f"{BASE}/quick/order/rule/ms/place"),
        ("POST", f"{BASE}/quick/order/vr/modify"),
        ("POST", f"{BASE}/quick/order/cancel"),
        ("GET", f"{BASE}/portfolio/v2/positions"),
        ("GET", f"{BASE}/quick/user/limits"),
        ("GET", "https://evil.example.com/market-data/1.0/watchlist/option-chain"),
        ("GET", f"{BASE}/market-data/1.0/historical/details"),
        ("PUT", f"{BASE}/script-details/1.0/masterscrip/file-paths"),
        ("GET", "http://lapi.kotaksecurities.com/x.csv"),
    ]:
        with pytest.raises(KotakBlocked):
            _check_url(method, url, BASE)


def test_login_learns_base_url_and_never_keeps_the_session() -> None:
    rec = Recorder()
    c = client(rec, base=None)
    info = c.login()
    assert info == {"base_url": BASE, "feed_url": "wss://feed"} and c.base_url == BASE
    first, second = rec.requests
    assert first.headers["Authorization"] == TOKEN and first.headers["neo-fin-key"] == "neotradeapi"
    body1 = json.loads(first.content)
    assert (
        body1["mobileNumber"] == "+919876543210"
        and body1["ucc"] == "ABC12"
        and len(body1["totp"]) == 6
    )
    assert second.headers["sid"] == "SID1" and second.headers["Auth"] == "VIEW"
    assert json.loads(second.content) == {"mpin": MPIN}
    assert "TRADE-SESSION" not in json.dumps(vars(c), default=str)
    c.quotes(["nse_cm|11536"])
    data = rec.requests[-1]
    assert data.headers["Authorization"] == TOKEN
    assert (
        "Auth" not in data.headers
        and "sid" not in data.headers
        and "neo-fin-key" not in data.headers
    )


def test_errors_are_scrubbed() -> None:
    def bad(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"status": "error", "message": "Invalid MPIN.", "errorCode": "401"}
        )

    c = KotakClient(None, httpx.Client(transport=httpx.MockTransport(bad)), 0, settings())
    with pytest.raises(KotakError) as e:
        c.login()
    for secret in (TOKEN, MPIN, SECRET):
        assert secret not in str(e.value)
    with pytest.raises(KotakError, match="baseUrl"):
        KotakClient(None, httpx.Client(transport=httpx.MockTransport(bad)), 0, settings()).quotes(
            ["x|1"]
        )


def test_parsers() -> None:
    assert fo_expiry(fo_epoch(date(2026, 10, 27))) == date(2026, 10, 27)
    assert parse_cash_master(CM_CSV, {"TCS", "INFY", "SBIN"}) == {
        "TCS": "nse_cm|11536",
        "INFY": "nse_cm|1594",
    }
    fno = parse_fo_master(FO_CSV, {"TCS"})
    assert fno["TCS"].lot_size == 175 and fno["TCS"].expiries == (
        date(2026, 10, 27),
        date(2026, 11, 24),
    )
    q = parse_quotes([quote("101", 50.0, 49.5, 50.5)], TS)["nse_fo|101"]
    assert (q.bid, q.ask, q.bid_qty, q.ask_qty, q.prev_close, q.bid_qty_depth) == (
        49.5,
        50.5,
        350,
        525,
        40.0,
        350,
    )
    ch = parse_option_chain(chain_json(), "TCS", date(2026, 10, 27), 4010.0, TS)
    assert len(ch.rows) == 18 and ch.atm_strike() == 4000.0
    r = ch.get(4000.0, "CE")
    assert (
        r
        and r.ltp == 50.0
        and r.oi == 200000
        and r.bid is None
        and r.instrument_key == "nse_fo|5000"
    )
    assert parse_futures(fut_json()) == {
        date(2026, 10, 27): {"last": 4030.5, "prev_close": 4000.0, "oi": 1e6, "chg_oi": 5e4}
    }


def test_login_job_and_instruments_collector(engine: Engine) -> None:
    seed_instruments(engine, {"TCS": 150, "INFY": 400})
    rec = Recorder()
    c = client(rec, base=None)
    assert login_job(engine, c) == 1
    with session_scope(engine) as s:
        sess = get_config(s, live.SESSION_KEY)
        assert sess["base_url"] == BASE and sess["ok"] and "token" not in json.dumps(sess).lower()
    KotakInstrumentsCollector(c).run(engine)
    with session_scope(engine) as s:
        tcs, infy = s.get(Instrument, "TCS"), s.get(Instrument, "INFY")
        assert tcs and tcs.instrument_key == "nse_cm|11536" and tcs.lot_size == 175 and tcs.is_fno
        assert infy and infy.instrument_key == "nse_cm|1594" and not infy.is_fno
        assert get_config(s, "fno_expiries")["TCS"] == ["2026-10-27", "2026-11-24"]


def test_live_source_chain_depth_iv_and_future(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_instruments(engine, {"TCS": 175})
    with session_scope(engine) as s:
        inst = s.get(Instrument, "TCS")
        assert inst
        inst.instrument_key = "nse_cm|11536"
    rec = Recorder()
    src = LiveOptionData(engine, client(rec))
    monkeypatch.setattr(
        live, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: TS)})
    )
    ch = src.chain("TCS", date(2026, 10, 27))
    assert ch and ch.spot == 4010.0
    atm = ch.get(4000.0, "CE")
    assert atm and (atm.bid, atm.ask, atm.bid_qty, atm.ask_qty) == (49.5, 50.5, 350, 525)
    assert atm.iv and 10 < atm.iv < 200  # solved from the mid, in %
    assert sum("/quotes/neosymbol/nse_fo" in r.url.path for r in rec.requests) == 1  # batched
    assert src.future("TCS", date(2026, 10, 27)) == {
        "last": 4030.5,
        "prev_close": 4000.0,
        "oi": 1e6,
        "chg_oi": 5e4,
    }
    paths = [r.url.path for r in rec.requests]
    assert sum("option-chain" in p for p in paths) == 2 and all("order" not in p for p in paths)
    with session_scope(engine) as s:
        assert store_chain(s, ch) == 18


def test_iv_percentile_and_implied_move(engine: Engine) -> None:
    with session_scope(engine) as s:
        for d in range(30):  # 30 days of ATM IV history 20..49
            s.add(
                OptionQuote(
                    ts=TS - timedelta(days=d + 1),
                    symbol="TCS",
                    expiry=date(2026, 10, 27),
                    strike=4000.0,
                    type="CE",
                    iv=20.0 + d,
                    spot=4000.0,
                )
            )
    with session_scope(engine) as s:
        assert s.scalar(select(OptionQuote.id).limit(1)) is not None
        p = iv_percentile(s, "TCS", 35.0, TS)
        assert p is not None and 45 <= p <= 60
        assert iv_percentile(s, "INFY", 35.0, TS) is None
    assert n_day_move_pct(30.0, 365) == pytest.approx(30.0)
    assert edge_ratio(6.0, 4.0) == 1.5 and edge_ratio(None, 4.0) is None
    assert event_iv_premium(40.0, 28.0) == 12.0


ORDER_PATTERNS = re.compile(
    r"place_order|modify_order|cancel_order|/quick/order|neo_api_client|kotakneoapi|tradeApiLogin.*Auth"
)


def test_no_order_code_anywhere() -> None:
    """Paper trading only: no order endpoint, SDK or order function anywhere in the worker."""
    root = Path(__file__).resolve().parents[1] / "app"
    hits = [
        f"{p.relative_to(root)}:{i}"
        for p in root.rglob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if ORDER_PATTERNS.search(line)
    ]
    assert hits == []
    src = (root / "market" / "kotak.py").read_text(encoding="utf-8")
    assert (
        '"Auth"' not in src.split("def login")[0] and src.count('"Auth"') == 1
    )  # login step 2 only
