"""F&O metadata: symbol master (sectors, aliases) and lot sizes into `instruments`.

The symbol master CSV in data/ is the curated source for names, sectors and aliases.
Lot sizes come from the NSE F&O lot-size file; a stock listed there is in the F&O segment.
"""

import csv
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import Engine, select

from app.collectors.base import Collector
from app.config import IST
from app.db.models import Instrument
from app.db.session import session_scope

log = logging.getLogger(__name__)

SYMBOL_MASTER = Path(__file__).resolve().parents[3] / "data" / "symbol_master.csv"
NSE_LOT_SIZE_URL = "https://archives.nseindia.com/content/fo/fo_mktlots.csv"
NSE_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; news-options-research)"}


class SymbolRow(BaseModel):
    symbol: str
    name: str
    sector: str
    sub_sector: str | None = None
    aliases: list[str] = Field(default_factory=list)


def parse_symbol_master(text: str) -> list[SymbolRow]:
    """CSV columns: symbol,name,sector,sub_sector,aliases (aliases separated by '|')."""
    rows: list[SymbolRow] = []
    for r in csv.DictReader(io.StringIO(text)):
        aliases = [a.strip() for a in (r.get("aliases") or "").split("|") if a.strip()]
        rows.append(
            SymbolRow(
                symbol=r["symbol"].strip().upper(),
                name=r["name"].strip(),
                sector=r["sector"].strip(),
                sub_sector=(r.get("sub_sector") or "").strip() or None,
                aliases=aliases,
            )
        )
    return rows


def parse_lot_sizes(text: str) -> dict[str, int]:
    """Parse NSE fo_mktlots.csv: UNDERLYING, SYMBOL, <month> lot, ... -> {symbol: nearest lot}.

    Index rows (NIFTY, BANKNIFTY, ...) are skipped, we only trade stock options. The first
    numeric month column is the current-month lot size. Rows without one are ignored.
    """
    index_symbols = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
    lots: dict[str, int] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3:
            continue
        symbol = row[1].strip().upper()
        if not symbol or symbol == "SYMBOL" or symbol in index_symbols:
            continue
        for cell in row[2:]:
            cell = cell.strip()
            if cell.isdigit():
                lots[symbol] = int(cell)
                break
    return lots


def fetch_lot_sizes(timeout: float = 20.0) -> dict[str, int]:
    resp = httpx.get(NSE_LOT_SIZE_URL, headers=NSE_HEADERS, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return parse_lot_sizes(resp.text)


def sync_instruments(
    rows: list[SymbolRow], lot_sizes: dict[str, int], engine: Engine | None = None
) -> int:
    """Upsert the symbol master. F&O membership and lot size come from lot_sizes."""
    now = datetime.now(IST)
    with session_scope(engine) as s:
        existing = {i.symbol: i for i in s.scalars(select(Instrument))}
        for row in rows:
            inst = existing.get(row.symbol) or Instrument(symbol=row.symbol)
            inst.name = row.name
            inst.sector = row.sector
            inst.sub_sector = row.sub_sector
            inst.aliases = row.aliases
            inst.is_fno = row.symbol in lot_sizes
            inst.lot_size = lot_sizes.get(row.symbol)
            inst.updated_at = now
            s.add(inst)
        unknown = set(lot_sizes) - {r.symbol for r in rows}
        if unknown:
            log.warning("F&O symbols missing from symbol master: %s", sorted(unknown))
    return len(rows)


class FnoMetaCollector(Collector[SymbolRow]):
    """Symbol master (local CSV) + NSE lot sizes -> instruments."""

    name = "nse_fno_meta"

    def fetch(self) -> Any:
        return SYMBOL_MASTER.read_text(encoding="utf-8"), fetch_lot_sizes(self.timeout_s)

    def parse(self, raw: Any) -> list[SymbolRow]:
        text, self._lots = raw
        return parse_symbol_master(text)

    def store(self, items: list[SymbolRow], engine: Engine | None) -> int:
        return sync_instruments(items, self._lots, engine)


refresh_fno_universe = FnoMetaCollector().run
