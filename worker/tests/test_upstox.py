import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, select

from app.config import IST
from app.db.config_store import get_config
from app.db.models import Instrument, OptionQuote
from app.db.session import session_scope
from app.market.implied import edge_ratio, event_iv_premium, n_day_move_pct, straddle_move_pct
from app.market.live import UpstoxInstrumentsCollector, atm_iv, iv_percentile, store_chain
from app.market.upstox import (
    UpstoxClient,
    UpstoxError,
    parse_instruments,
    parse_option_chain,
    parse_quotes,
)
from tests.helpers import seed_instruments

TS = datetime(2026, 10, 16, 11, 0, tzinfo=IST)
TOKEN = "analytics-SECRET"


def chain_json(spot: float = 4000.0) -> dict[str, Any]:
    def leg(ltp: float, iv: float, oi: int = 200000) -> dict[str, Any]:
        return {
            "instrument_key": "NSE_FO|1",
            "market_data": {
                "ltp": ltp,
                "volume": 5000,
                "oi": oi,
                "close_price": ltp,
                "bid_price": ltp - 0.5,
                "bid_qty": 175,
                "ask_price": ltp + 0.5,
                "ask_qty": 175,
                "prev_oi": oi,
            },
            "option_greeks": {
                "delta": 0.5,
                "gamma": 0.001,
                "theta": -2,
                "vega": 3,
                "iv": iv,
                "pop": 50,
            },
        }

    rows = []
    for k in range(3800, 4250, 50):
        rows.append(
            {
                "expiry": "2026-10-27",
                "pcr": 1.0,
                "strike_price": k,
                "underlying_key": "NSE_EQ|INE467B01029",
                "underlying_spot_price": spot,
                "call_options": leg(max(spot - k, 0) + 40, 25.0),
                "put_options": leg(max(k - spot, 0) + 40, 0.0),
            }
        )
    return {"status": "success", "data": rows}


def test_parse_option_chain() -> None:
    ch = parse_option_chain(chain_json(), "TCS", date(2026, 10, 27), TS)
    assert ch.spot == 4000.0 and len(ch.rows) == 18 and ch.atm_strike() == 4000.0
    ce, pe = ch.get(4000.0, "CE"), ch.get(4000.0, "PE")
    assert (
        ce is not None and ce.bid == 39.5 and ce.ask == 40.5 and ce.iv == 25.0 and ce.oi == 200000
    )
    assert pe is not None and pe.iv is None  # IV 0 treated as missing
    assert atm_iv(ch) == 25.0
    assert straddle_move_pct(ch) == pytest.approx(80 / 4000 * 100)


def test_parse_quotes_rekeys_and_derives_prev_close() -> None:
    raw = {
        "status": "success",
        "data": {
            "NSE_EQ:TCS": {
                "instrument_token": "NSE_EQ|INE467B01029",
                "last_price": 4050.0,
                "net_change": 50.0,
                "ohlc": {"open": 4000, "high": 4060, "low": 3990, "close": 4050.0},
                "depth": {
                    "buy": [{"quantity": 10, "price": 4049.5, "orders": 1}],
                    "sell": [{"quantity": 12, "price": 4050.5, "orders": 2}],
                },
            }
        },
    }
    q = parse_quotes(raw, TS)["NSE_EQ|INE467B01029"]
    assert (q.last_price, q.prev_close, q.bid, q.ask) == (4050.0, 4000.0, 4049.5, 4050.5)


def _ms(d: date) -> int:
    return int(
        datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST).astimezone(UTC).timestamp() * 1000
    )


def instruments_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "segment": "NSE_EQ",
            "instrument_type": "EQ",
            "trading_symbol": "TCS",
            "instrument_key": "NSE_EQ|INE467B01029",
            "isin": "INE467B01029",
            "lot_size": 1,
        },
        {
            "segment": "NSE_EQ",
            "instrument_type": "EQ",
            "trading_symbol": "INFY",
            "instrument_key": "NSE_EQ|INE009A01021",
            "isin": "INE009A01021",
            "lot_size": 1,
        },
    ]
    for d in (
        date(2026, 10, 27),
        date(2026, 11, 24),
        date(2026, 11, 3),
    ):  # 3-Nov: a non-monthly date
        for t in ("CE", "PE"):
            rows.append(
                {
                    "segment": "NSE_FO",
                    "instrument_type": t,
                    "underlying_symbol": "TCS",
                    "instrument_key": "NSE_FO|9",
                    "expiry": _ms(d),
                    "strike_price": 4000.0,
                    "lot_size": 175,
                }
            )
    rows.append(
        {
            "segment": "NSE_FO",
            "instrument_type": "FUT",
            "underlying_symbol": "TCS",
            "expiry": _ms(date(2026, 10, 27)),
        }
    )
    return rows


def test_parse_instruments_monthly_expiries_and_lots() -> None:
    eq, fno = parse_instruments(instruments_rows(), {"TCS", "INFY"})
    assert eq["TCS"] == ("NSE_EQ|INE467B01029", "INE467B01029")
    assert fno["TCS"].lot_size == 175
    assert fno["TCS"].expiries == (date(2026, 10, 27), date(2026, 11, 24))  # last per month only
    assert "INFY" not in fno


class FakeHttp:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self.payload, self.status, self.calls = payload, status, []  # type: ignore[var-annotated]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        return httpx.Response(self.status, json=self.payload)


def test_client_get_only_bearer_header_and_scrubbed_errors(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HISTORY_DATA_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    fake = FakeHttp(chain_json())
    c = UpstoxClient(TOKEN, httpx.Client(transport=httpx.MockTransport(fake)), min_interval_s=0)
    c.option_chain("NSE_EQ|INE467B01029", date(2026, 10, 27))
    req = fake.calls[0]
    assert req.method == "GET" and req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(req.url)
    assert any(
        p.name.endswith("v2_option_chain.json")
        for p in (tmp_path / "raw_samples" / "upstox").iterdir()
    )
    bad = UpstoxClient(
        TOKEN,
        httpx.Client(
            transport=httpx.MockTransport(
                FakeHttp({"status": "error", "errors": [{"message": "Invalid token"}]}, 401)
            )
        ),
        min_interval_s=0,
    )
    with pytest.raises(UpstoxError) as e:
        bad.quotes(["NSE_EQ|X"])
    assert TOKEN not in str(e.value) and "401" in str(e.value)
    with pytest.raises(UpstoxError, match="not set"):
        UpstoxClient("", min_interval_s=0).quotes(["x"])
    assert not any(hasattr(c, m) for m in ("place_order", "modify_order", "cancel_order", "post"))
    get_settings.cache_clear()


def test_instruments_collector_updates_keys_lots_expiries(engine: Engine) -> None:
    seed_instruments(engine, {"TCS": 150, "INFY": 400})

    class Src:
        configured = True

        def instruments(self) -> list[dict[str, Any]]:
            return instruments_rows()

    UpstoxInstrumentsCollector(Src()).run(engine)  # type: ignore[arg-type]
    with session_scope(engine) as s:
        tcs, infy = s.get(Instrument, "TCS"), s.get(Instrument, "INFY")
        assert (
            tcs
            and tcs.instrument_key == "NSE_EQ|INE467B01029"
            and tcs.lot_size == 175
            and tcs.is_fno
        )
        assert infy and infy.instrument_key and not infy.is_fno  # no options in this master
        assert get_config(s, "fno_expiries")["TCS"] == ["2026-10-27", "2026-11-24"]


def test_store_chain_window_and_iv_percentile(engine: Engine) -> None:
    with session_scope(engine) as s:
        assert store_chain(s, parse_option_chain(chain_json(), "TCS", date(2026, 10, 27), TS)) == 18
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
        assert iv_percentile(s, "INFY", 35.0, TS) is None  # no history


def test_implied_move_and_edge() -> None:
    assert n_day_move_pct(30.0, 365) == pytest.approx(30.0)
    assert n_day_move_pct(30.0, 7) == pytest.approx(30 * (7 / 365) ** 0.5)
    assert (
        edge_ratio(6.0, 4.0) == 1.5 and edge_ratio(None, 4.0) is None and edge_ratio(3.0, 0) is None
    )
    assert event_iv_premium(40.0, 28.0) == 12.0 and event_iv_premium(None, 28.0) is None
    assert json.dumps({"ok": True})


def test_token_expiry_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.scheduler import token_expiry_warning

    monkeypatch.setenv("BROKER_TOKEN_ISSUED_ON", "2025-10-20")
    get_settings.cache_clear()
    assert token_expiry_warning(datetime(2026, 9, 1, tzinfo=IST)) is None
    msg = token_expiry_warning(datetime(2026, 10, 16, tzinfo=IST))
    assert msg and "2026-10-20" in msg and "4 days left" in msg
    monkeypatch.setenv("BROKER_TOKEN_ISSUED_ON", "")
    get_settings.cache_clear()
    assert token_expiry_warning(datetime(2026, 10, 16, tzinfo=IST)) is None
    get_settings.cache_clear()
