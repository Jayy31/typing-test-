from datetime import date, time

import duckdb
import numpy as np
import pytest

from app.ai.schemas import Scenario
from app.config import get_settings
from app.history import integrity, iv_backfill, live_sync, nse_files
from app.history.analogues import AnalogueResult
from app.history.backup import backup
from app.history.store import StoreBusyError, connect, init_schema
from app.pricing.bs import price
from app.signals.prediction import combine


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "h.duckdb"))
    monkeypatch.setenv("HISTORY_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def mem() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    init_schema(con)
    return con


def test_parse_equity_filters_series():
    raw = (
        b"SYMBOL, SERIES, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, TTL_TRD_QNTY, DELIV_PER\n"
        b"INFY, EQ, 100, 110, 95, 105, 1000, 45.5\nINFY, BE, 1, 1, 1, 1, 1, -\n"
    )
    rows = nse_files.parse_equity(raw, date(2024, 1, 2))
    assert rows == [(date(2024, 1, 2), "INFY", 100.0, 110.0, 95.0, 105.0, 1000, 45.5)]


def test_parse_equity_missing_column_raises():
    with pytest.raises(nse_files.FormatError):
        nse_files.parse_equity(b"SYMBOL,SERIES\nINFY,EQ\n", date(2024, 1, 2))


def test_parse_fo_udiff():
    raw = (
        b"FinInstrmTp,TckrSymb,XpryDt,StrkPric,OptnTp,OpnPric,HghPric,LwPric,ClsPric,SttlmPric,"
        b"OpnIntrst,ChngInOpnIntrst,TtlTradgVol,NewBrdLotQty,UndrlygPric\n"
        b"STO,INFY,2024-08-29,1800,CE,20,25,18,22,22,1000,50,300,400,1790\n"
        b"STF,INFY,2024-08-29,,,,,,,,,,,400,1790\n"
    )
    p = nse_files.parse_fo(raw, date(2024, 8, 1))
    assert len(p.options) == 1 and p.options[0][2] == date(2024, 8, 29)
    assert p.lots == {"INFY": 400} and p.underlying == {"INFY": 1790.0}


def test_fo_kind_switches_format():
    assert nse_files.fo_kind(date(2024, 7, 5)) == "fo_old"
    assert nse_files.fo_kind(date(2024, 7, 8)) == "fo_udiff"


def test_r1_guard(monkeypatch):
    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", "")
    get_settings.cache_clear()
    with pytest.raises(integrity.LookAheadError):
        integrity.require_post_cutoff(date(2026, 1, 1), "prediction")  # unset = all contaminated
    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", "2025-06-30")
    get_settings.cache_clear()
    with pytest.raises(integrity.LookAheadError):
        integrity.require_post_cutoff(date(2025, 6, 30), "prediction")
    integrity.require_post_cutoff(date(2025, 7, 1), "prediction")
    get_settings.cache_clear()


def test_iv_backfill_recovers_vol():
    con = mem()
    d, exp, spot = date(2024, 3, 1), date(2024, 3, 28), 1000.0
    t = (exp - d).days / 365
    r = get_settings().risk_free_rate
    con.execute(
        "INSERT INTO hist_prices VALUES (?, 'ABC', ?, ?, ?, ?, 1, 50)", [d, spot, spot, spot, spot]
    )
    for k in (980.0, 1000.0, 1020.0):
        for typ in ("CE", "PE"):
            px = float(price(spot, k, t, r, 0.3, typ))
            con.execute(
                "INSERT INTO hist_options VALUES (?, 'ABC', ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 1)",
                [d, exp, k, typ, px, px, px, px, px],
            )
    assert iv_backfill.run(con) == 6
    (atm,) = con.execute("SELECT atm_iv FROM hist_atm_iv WHERE symbol = 'ABC'").fetchone()
    assert atm == pytest.approx(30.0, abs=0.1)
    assert iv_backfill.run(con) == 0  # incremental


def _an(n: int) -> AnalogueResult:
    return AnalogueResult("stock", "all", 3, n, 2.0, 1.8, 0.7, 1.5, 0.5, 3.0)


def _tree() -> list[Scenario]:
    return [
        Scenario(
            name="up",
            probability=0.6,
            stock_move_pct=4.0,
            timing_days=2,
            triggers=["x"],
            invalidation="y",
        ),
        Scenario(
            name="flat",
            probability=0.4,
            stock_move_pct=0.0,
            timing_days=2,
            triggers=["x"],
            invalidation="y",
        ),
    ]


def test_combine_blends_only_usable_analogues():
    base, sc = combine(_tree(), "bullish", _an(2), True, 0.5)
    assert base is not None and base.source == "scenario_tree" and len(sc) == 2
    pred, sc = combine(_tree(), "bullish", _an(50), True, 0.5)
    assert pred is not None and pred.source == "blended" and len(sc) == 5
    assert sum(s.probability for s in sc) == pytest.approx(1.0)
    assert pred.move_pct == pytest.approx(
        0.5 * 2.4 + 0.5 * (0.3 * 0.5 + 0.4 * 1.8 + 0.3 * 3.0), abs=0.01
    )


def test_curated_and_store_lock(store):
    path = store / "ev.yaml"
    path.write_text(
        "events:\n  - {date: 2024-01-12, symbol: INFY, sector: IT, event_type: results,"
        " direction: bearish, title: guidance cut, time: '16:10'}\n"
    )
    with connect(write=True) as con:
        assert live_sync.load_curated(con, path) == 1
        assert live_sync.load_curated(con, path) == 1  # idempotent
        with pytest.raises(StoreBusyError):
            with connect(write=True):
                pass
        row = con.execute("SELECT symbol, time, source FROM hist_events").fetchall()
    assert row == [("INFY", time(16, 10), "curated")]


def test_backup_off_by_default(store):
    assert backup(client=object()) == 0


def test_backup_uploads_changed_files_once(store, monkeypatch):
    for k, v in {
        "R2_BACKUP_ENABLED": "true",
        "R2_ACCOUNT_ID": "a",
        "R2_ACCESS_KEY": "b",
        "R2_SECRET_KEY": "c",
        "R2_BUCKET": "bk",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    (store / "parquet" / "x").mkdir(parents=True)
    (store / "parquet" / "x" / "a.parquet").write_bytes(b"1")

    class Fake:
        def __init__(self):
            self.keys = []

        def upload_file(self, src, bucket, key):
            self.keys.append(key)

    f = Fake()
    assert backup(f) == 2 and f.keys == ["parquet/x/a.parquet", "history.duckdb"]
    f2 = Fake()
    assert backup(f2) == 1 and f2.keys == ["history.duckdb"]


def test_vectorised_iv_matches_prices():
    from app.pricing.bs import implied_vol_vec

    px = np.array([float(price(100, 100, 0.1, 0.065, 0.25, "CE"))])
    iv = implied_vol_vec(
        px, np.array([100.0]), np.array([100.0]), np.array([0.1]), 0.065, np.array([True])
    )
    assert iv[0] == pytest.approx(0.25, abs=1e-3)
