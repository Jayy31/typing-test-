from sqlalchemy import Engine, select

from app.collectors.fno_meta import (
    SYMBOL_MASTER,
    parse_lot_sizes,
    parse_symbol_master,
    sync_instruments,
)
from app.db.models import Instrument
from app.db.session import session_scope

LOTS_CSV = """UNDERLYING,SYMBOL,Nov-2026,Dec-2026,Jan-2027
Derivatives on Individual Securities,,,,
TCS,TCS,175,175,175
HDFC Bank,HDFCBANK, 550 ,550,550
Bajaj Auto,BAJAJ-AUTO,75,,
Derivatives on Indices,,,,
Nifty 50,NIFTY,75,75,75
Bank Nifty,BANKNIFTY,30,30,30
"""


def test_parse_lot_sizes_skips_indices_and_headers() -> None:
    assert parse_lot_sizes(LOTS_CSV) == {"TCS": 175, "HDFCBANK": 550, "BAJAJ-AUTO": 75}


def test_symbol_master_is_valid_and_unique() -> None:
    rows = parse_symbol_master(SYMBOL_MASTER.read_text(encoding="utf-8"))
    symbols = [r.symbol for r in rows]
    assert len(symbols) == len(set(symbols))
    by_symbol = {r.symbol: r for r in rows}
    assert "Airtel" in by_symbol["BHARTIARTL"].aliases
    assert "SBI" in by_symbol["SBIN"].aliases
    assert all(r.sector for r in rows)


def test_sync_sets_fno_flag_and_lot_size(engine: Engine) -> None:
    rows = parse_symbol_master(SYMBOL_MASTER.read_text(encoding="utf-8"))
    sync_instruments(rows, {"TCS": 175}, engine)
    with session_scope(engine) as s:
        tcs = s.get(Instrument, "TCS")
        infy = s.get(Instrument, "INFY")
        assert tcs is not None and tcs.is_fno and tcs.lot_size == 175
        assert infy is not None and not infy.is_fno and infy.lot_size is None


def test_sync_is_idempotent_and_updates(engine: Engine) -> None:
    rows = parse_symbol_master(SYMBOL_MASTER.read_text(encoding="utf-8"))
    sync_instruments(rows, {"TCS": 175}, engine)
    sync_instruments(rows, {"TCS": 150}, engine)
    with session_scope(engine) as s:
        assert len(s.scalars(select(Instrument)).all()) == len(rows)
        tcs = s.get(Instrument, "TCS")
        assert tcs is not None and tcs.lot_size == 150
