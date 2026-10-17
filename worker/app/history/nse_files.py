"""NSE archive files for the 5-year backfill: download (polite, cached, resumable) and parse.

Parsers are pure and read column names from config/history.yaml, so a format surprise is a
config fix. Each parser returns normalised rows ready for the DuckDB history tables.
"""

import csv
import io
import logging
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from app.collectors import nse_http
from app.config import get_settings
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/",
}


class FormatError(ValueError):
    """A file did not have the columns config/history.yaml expects."""


def url_for(kind: str, d: date) -> str:
    src = load_yaml("history.yaml")["sources"][kind]
    return str(src["url"]).format(
        ddmmyyyy=d.strftime("%d%m%Y"),
        yyyymmdd=d.strftime("%Y%m%d"),
        yyyy=d.strftime("%Y"),
        mmm=d.strftime("%b").upper(),
        ddMMMyyyy=d.strftime("%d%b%Y").upper(),
    )


def fo_kind(d: date) -> str:
    """Old format until mid-2024, UDiFF after (config dates)."""
    src = load_yaml("history.yaml")["sources"]
    return "fo_old" if d <= date.fromisoformat(src["fo_old"]["until"]) else "fo_udiff"


def _text(raw: bytes) -> str:
    """Unzip if needed; decode leniently (NSE files are ASCII/latin-1)."""
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            raw = z.read(z.namelist()[0])
    return raw.decode("utf-8-sig", errors="replace")


def _rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for r in reader:
        out.append({(k or "").strip(): (v or "").strip() for k, v in r.items()})
    return out


def _need(row: dict[str, str], cols: dict[str, str], kind: str) -> None:
    missing = [c for c in cols.values() if c not in row]
    if missing:
        raise FormatError(f"{kind}: missing column(s) {missing}; got {sorted(row)[:20]}")


def _num(x: str) -> float | None:
    try:
        return float(x.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def parse_equity(raw: bytes, d: date) -> list[tuple[Any, ...]]:
    """-> (date, symbol, open, high, low, close, volume, delivery_pct) for configured series."""
    cfg = load_yaml("history.yaml")["sources"]["equity"]
    cols, series = cfg["columns"], set(cfg["series"])
    rows = _rows(_text(raw))
    if rows:
        _need(rows[0], cols, "equity")
    out = []
    for r in rows:
        if r[cols["series"]] not in series:
            continue
        close = _num(r[cols["close"]])
        if close is None:
            continue
        vol = _num(r[cols["volume"]])
        out.append(
            (
                d,
                r[cols["symbol"]],
                _num(r[cols["open"]]),
                _num(r[cols["high"]]),
                _num(r[cols["low"]]),
                close,
                int(vol) if vol is not None else None,
                _num(r[cols["delivery_pct"]]),
            )
        )
    return out


@dataclass(frozen=True)
class FoParsed:
    options: list[
        tuple[Any, ...]
    ]  # (date, symbol, expiry, strike, type, o, h, l, c, settle, oi, chg_oi, volume)
    lots: dict[str, int]  # symbol -> lot size (UDiFF only)
    underlying: dict[str, float]  # symbol -> underlying price (UDiFF only)


def parse_fo(raw: bytes, d: date, kind: str | None = None) -> FoParsed:
    kind = kind or fo_kind(d)
    cfg = load_yaml("history.yaml")["sources"][kind]
    cols, fmt = cfg["columns"], cfg["expiry_format"]
    rows = _rows(_text(raw))
    if rows:
        _need(rows[0], cols, kind)
    opts, lots, und = [], {}, {}
    for r in rows:
        if r[cols["instrument"]] != cfg["instrument"]:
            continue
        typ = r[cols["type"]]
        if typ not in ("CE", "PE"):
            continue
        try:
            expiry = datetime.strptime(
                r[cols["expiry"]].title() if "%b" in fmt else r[cols["expiry"]], fmt
            ).date()
        except ValueError:
            continue
        sym = r[cols["symbol"]]
        oi, chg, vol = _num(r[cols["oi"]]), _num(r[cols["chg_oi"]]), _num(r[cols["volume"]])
        opts.append(
            (
                d,
                sym,
                expiry,
                _num(r[cols["strike"]]),
                typ,
                _num(r[cols["open"]]),
                _num(r[cols["high"]]),
                _num(r[cols["low"]]),
                _num(r[cols["close"]]),
                _num(r[cols["settle"]]),
                int(oi) if oi is not None else None,
                int(chg) if chg is not None else None,
                int(vol) if vol is not None else None,
            )
        )
        if "lot_size" in cols and r.get(cols["lot_size"]):
            lot = _num(r[cols["lot_size"]])
            if lot:
                lots[sym] = int(lot)
        if "underlying" in cols and r.get(cols["underlying"]):
            u = _num(r[cols["underlying"]])
            if u:
                und[sym] = u
    return FoParsed(opts, lots, und)


def parse_index(raw: bytes, d: date) -> list[tuple[Any, ...]]:
    cfg = load_yaml("history.yaml")["sources"]["index"]
    cols, names = cfg["columns"], {n.lower() for n in cfg["names"]}
    rows = _rows(_text(raw))
    if rows:
        _need(rows[0], cols, "index")
    out = []
    for r in rows:
        if r[cols["name"]].lower() in names:
            close = _num(r[cols["close"]])
            if close is not None:
                out.append((d, r[cols["name"]], close))
    return out


class Downloader:
    """Polite downloader with an on-disk cache: raw files are kept, so re-runs never re-download."""

    def __init__(self, http: httpx.Client | None = None, cache_dir: Path | None = None) -> None:
        cfg = load_yaml("history.yaml")["http"]
        self.http = http or nse_http.client(
            headers=HEADERS, timeout=float(cfg["timeout_s"]), follow_redirects=True
        )
        self.min_interval = float(cfg["min_interval_s"])
        self.retries = int(cfg["retries"])
        self.cache = cache_dir or Path(get_settings().history_data_dir) / "raw"
        self._last = 0.0
        self._warmed = False

    def _wait(self) -> None:
        gap = self.min_interval - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()

    def get(self, kind: str, d: date) -> bytes | None:
        """File bytes, or None when NSE has no file for that day (holiday / not published)."""
        target = self.cache / kind / f"{d.isoformat()}.bin"
        if target.exists():
            data = target.read_bytes()
            return data or None
        if not self._warmed:
            try:
                self.http.get("https://www.nseindia.com/")  # cookies
            except httpx.HTTPError:
                pass
            self._warmed = True
        url = url_for(kind, d)
        for attempt in range(self.retries + 1):
            self._wait()
            try:
                resp = self.http.get(url)
            except httpx.HTTPError as exc:
                if attempt == self.retries:
                    raise RuntimeError(f"{kind} {d}: {type(exc).__name__}") from None
                continue
            if resp.status_code == 404:
                if d < date.today():  # remember "no file" (holiday); today's may come later
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"")
                return None
            if resp.status_code == 200 and resp.content:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(resp.content)
                return resp.content
            if attempt == self.retries:
                raise RuntimeError(f"{kind} {d}: HTTP {resp.status_code}")
        return None
