"""Pure parsers for Phase 7 sources (unit-tested with sample payloads in tests/samples/deep).

NSE field names follow the public JSON endpoints as documented by community clients; they could
not be checked from the build sandbox. Unknown fields fall back to alternates, and rows that do
not parse are skipped (counted), never guessed.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any

import feedparser

from app.config import IST
from app.deep import rules
from app.yaml_config import load_yaml

DT_FMTS = (
    "%d-%b-%Y %H:%M:%S",
    "%d-%b-%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)
D_FMTS = ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d-%B-%Y")


def ref_of(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:64]


def parse_dt(value: Any) -> datetime | None:
    s = str(value or "").strip()
    for fmt in DT_FMTS:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def parse_date(value: Any) -> date | None:
    s = str(value or "").strip()
    for fmt in D_FMTS:
        try:
            return datetime.strptime(s.title() if "%b" in fmt or "%B" in fmt else s, fmt).date()
        except ValueError:
            continue
    dt = parse_dt(s)
    return dt.date() if dt else None


def num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _first(row: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if row.get(k) not in (None, "", "-", "NIL", "Nil"):
            return row[k]
    return None


def _at(d: date, hhmm: str) -> datetime:
    return datetime.combine(d, time.fromisoformat(hhmm), IST)


@dataclass
class InsiderRow:
    ref: str
    symbol: str
    person: str | None
    person_category: str | None
    counterparty: str
    side: str
    quantity: float | None
    value_inr: float | None
    mode: str | None
    event_date: date | None
    disclosed_at: datetime
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def subtype(self) -> str:
        return f"insider_{self.side}_{self.counterparty}"


def parse_insider(payload: Any) -> list[InsiderRow]:
    """NSE corporates-pit JSON ({'data': [...]}) -> insider trades. disclosed_at = exchange
    broadcast time ('date'); event_date = trade date (acqfromDt). Pledges/revocations skipped."""
    rows = payload.get("data", []) if isinstance(payload, dict) else payload
    out: list[InsiderRow] = []
    for r in rows or []:
        symbol = str(r.get("symbol") or "").strip().upper()
        side = rules.side_of(_first(r, "tdpTransactionType", "acqMode_type", "transactionType"))
        disclosed = parse_dt(_first(r, "date", "broadcastDateTime", "intimDt"))
        sec_type = str(r.get("secType") or "Equity Shares").lower()
        if not symbol or side is None or disclosed is None or "equity" not in sec_type:
            continue
        category = _first(r, "personCategory", "category")
        out.append(
            InsiderRow(
                ref=ref_of(
                    "pit",
                    symbol,
                    r.get("acqName"),
                    side,
                    r.get("secAcq"),
                    r.get("acqfromDt"),
                    disclosed,
                ),
                symbol=symbol,
                person=_first(r, "acqName", "personName"),
                person_category=category,
                counterparty=rules.insider_counterparty(category),
                side=side,
                quantity=num(r.get("secAcq")),
                value_inr=num(r.get("secVal")),
                mode=_first(r, "acqMode"),
                event_date=parse_date(_first(r, "acqfromDt", "acqtoDt")),
                disclosed_at=disclosed,
                raw=dict(r),
            )
        )
    return out


@dataclass
class DealRow:
    ref: str
    kind: str
    symbol: str
    client: str | None
    side: str
    quantity: float | None
    price: float | None
    event_date: date
    disclosed_at: datetime
    counterparty: str = "other"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def value_inr(self) -> float | None:
        return self.quantity * self.price if self.quantity and self.price else None

    @property
    def subtype(self) -> str:
        return f"deal_{self.side}_{self.counterparty}"


def parse_deals(
    payload: Any, kind: str, promoters: dict[str, set[str]] | None = None
) -> list[DealRow]:
    """NSE historical bulk/block deals JSON -> deals. The report is public after the close, so
    disclosed_at = deal date at `disclosure.deals_published_time` (never earlier)."""
    hhmm = str(load_yaml("deep_data.yaml")["disclosure"]["deals_published_time"])
    rows = payload.get("data", []) if isinstance(payload, dict) else payload
    out: list[DealRow] = []
    for r in rows or []:
        symbol = str(_first(r, "BD_SYMBOL", "symbol") or "").strip().upper()
        d = parse_date(_first(r, "BD_DT_DATE", "date", "mTIMESTAMP"))
        side = rules.side_of(_first(r, "BD_BUY_SELL", "buySell"))
        if not symbol or d is None or side is None:
            continue
        client = _first(r, "BD_CLIENT_NAME", "clientName")
        qty = num(_first(r, "BD_QTY_TRD", "qty"))
        price = num(_first(r, "BD_TP_WATP", "watp", "tradePrice"))
        out.append(
            DealRow(
                ref=ref_of(kind, symbol, client, side, qty, price, d),
                kind=kind,
                symbol=symbol,
                client=client,
                side=side,
                quantity=qty,
                price=price,
                event_date=d,
                disclosed_at=_at(d, hhmm),
                counterparty=rules.deal_counterparty(client, (promoters or {}).get(symbol)),
                raw=dict(r),
            )
        )
    return out


@dataclass
class HoldingRow:
    symbol: str
    event_date: date  # quarter end
    disclosed_at: datetime
    promoter_pct: float | None
    fii_pct: float | None
    dii_pct: float | None
    public_pct: float | None
    pledge_pct: float | None
    raw: dict[str, Any] = field(default_factory=dict)

    def stakes(self) -> dict[str, float | None]:
        return {
            "promoter_pct": self.promoter_pct,
            "fii_pct": self.fii_pct,
            "dii_pct": self.dii_pct,
            "pledge_pct": self.pledge_pct,
        }


def parse_shareholding(payload: Any) -> list[HoldingRow]:
    """NSE shareholding-pattern master JSON -> quarterly stakes. disclosed_at = submission /
    broadcast time; without a time the configured end-of-day time is used."""
    hhmm = str(load_yaml("deep_data.yaml")["disclosure"]["shareholding_default_time"])
    rows = payload.get("data", []) if isinstance(payload, dict) else payload
    out: list[HoldingRow] = []
    for r in rows or []:
        symbol = str(r.get("symbol") or "").strip().upper()
        qend = parse_date(_first(r, "date", "asOnDate", "quarterEnded"))
        sub = _first(r, "broadcastDt", "submissionDate", "xbrlSubmitDt")
        disclosed = parse_dt(sub) or (_at(d, hhmm) if (d := parse_date(sub)) else None)
        if not symbol or qend is None or disclosed is None:
            continue
        out.append(
            HoldingRow(
                symbol=symbol,
                event_date=qend,
                disclosed_at=disclosed,
                promoter_pct=num(_first(r, "pr_and_prgrp", "promoterAndPromoterGroup")),
                fii_pct=num(_first(r, "fii", "fpi", "foreignPortfolioInvestors")),
                dii_pct=num(_first(r, "dii", "mutualFunds", "institutionsDomestic")),
                public_pct=num(_first(r, "public_val", "public")),
                pledge_pct=num(_first(r, "pledge", "pledgedPct", "encumberedPct")),
                raw=dict(r),
            )
        )
    return out


RATING_UP = re.compile(r"\bupgrad", re.I)
RATING_DOWN = re.compile(r"\bdowngrad|\bwatch with negative|\boutlook (revised )?to negative", re.I)
RATING_REAFFIRM = re.compile(r"\breaffirm|\bre-affirm", re.I)
AGENCIES = (
    "CRISIL",
    "ICRA",
    "CARE",
    "India Ratings",
    "Fitch",
    "Moody",
    "S&P",
    "Acuite",
    "Brickwork",
    "Infomerics",
)


def rating_action(text: str) -> tuple[str, str | None] | None:
    """Credit-rating announcement text -> (action, agency); None if not a rating disclosure."""
    if "rating" not in text.lower():
        return None
    agency = next((a for a in AGENCIES if a.lower() in text.lower()), None)
    if RATING_DOWN.search(text):
        return "downgrade", agency
    if RATING_UP.search(text):
        return "upgrade", agency
    if RATING_REAFFIRM.search(text):
        return "reaffirm", agency
    return "other", agency


@dataclass
class RegulatorItem:
    ref: str
    regulator: str
    action_type: str | None
    title: str
    url: str | None
    disclosed_at: datetime
    event_date: date | None
    entity: str  # company name from the title, mapped to a stock by the caller


SEBI_ORDER = re.compile(
    r"(?:order|adjudication order|settlement order|interim order)[^.]*?"
    r"in (?:the matter|respect) of\s+(.+)",
    re.I,
)
USFDA_ADVERSE = re.compile(
    r"warning letter|import alert|official action indicated|\bOAI\b|form 483", re.I
)


def _feed_dt(e: Any) -> datetime | None:
    """feedparser time tuples are UTC."""
    st = e.get("published_parsed") or e.get("updated_parsed")
    if not st:
        return None
    y, mo, d, h, mi, sec = st[:6]
    return datetime(y, mo, d, h, mi, sec, tzinfo=UTC).astimezone(IST)


def parse_sebi(content: bytes | str) -> list[RegulatorItem]:
    """SEBI RSS -> orders naming an entity ('Order in the matter of X Ltd')."""
    out: list[RegulatorItem] = []
    for e in feedparser.parse(content).entries:
        title = str(e.get("title") or "").strip()
        m = SEBI_ORDER.search(title)
        when = _feed_dt(e)
        if not m or when is None:
            continue
        kind = (
            "settlement"
            if "settlement" in title.lower()
            else "adjudication"
            if "adjudication" in title.lower()
            else "order"
        )
        out.append(
            RegulatorItem(
                ref_of("sebi", e.get("link"), title),
                "sebi",
                kind,
                title,
                e.get("link"),
                when,
                when.date(),
                m.group(1).strip(" ."),
            ),
        )
    return out


def parse_usfda(content: bytes | str) -> list[RegulatorItem]:
    """FDA warning-letter / import-alert RSS -> adverse items; entity = the title's company."""
    out: list[RegulatorItem] = []
    for e in feedparser.parse(content).entries:
        title = str(e.get("title") or "").strip()
        text = f"{title} {e.get('summary') or ''}"
        when = _feed_dt(e)
        if when is None or not (
            USFDA_ADVERSE.search(text) or "warning-letters" in str(e.get("link") or "")
        ):
            continue
        out.append(
            RegulatorItem(
                ref_of("usfda", e.get("link"), title),
                "usfda",
                "warning_letter",
                title,
                e.get("link"),
                when,
                when.date(),
                title.split(" - ")[0].strip(),
            ),
        )
    return out
