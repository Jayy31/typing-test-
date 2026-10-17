"""Phase 7 collectors: insider trades, bulk/block deals, shareholding, ratings, SEBI, USFDA.

Each row is stored once (unique ref) with its event date AND public disclosure time; material
rows for F&O stocks become candidate evidence (app.deep.evidence). Immaterial rows are stored
with the reason so the thresholds can be reviewed.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.collectors.base import Collector
from app.collectors.nse import HEADERS, nse_get
from app.config import IST
from app.db.models import (
    BulkBlockDeal,
    InsiderTrade,
    Instrument,
    NewsItem,
    Rating,
    RegulatoryAction,
    Shareholding,
    StockStats,
)
from app.db.session import session_scope
from app.deep import evidence, matching, parsers, rules
from app.processing.entity_map import InstrumentInfo
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


def src(name: str) -> dict[str, Any]:
    return dict(load_yaml("deep_data.yaml")["sources"][name])


def _range(days: int) -> dict[str, str]:
    today = datetime.now(IST).date()
    return {
        "from": (today - timedelta(days=days)).strftime("%d-%m-%Y"),
        "to": today.strftime("%d-%m-%Y"),
    }


def fno_symbols(s: Session) -> set[str]:
    return {i.symbol for i in s.scalars(select(Instrument)) if i.is_fno}


def stats_of(s: Session, symbol: str) -> tuple[float | None, float | None]:
    st = s.get(StockStats, symbol)
    return (st.adv20_inr, st.mcap_inr) if st else (None, None)


def known_promoters(s: Session) -> dict[str, set[str]]:
    """Promoter / promoter-group names per stock from insider filings (deal counterparty)."""
    out: dict[str, set[str]] = {}
    for sym, person in s.execute(
        select(InsiderTrade.symbol, InsiderTrade.person).where(
            InsiderTrade.counterparty.in_(("promoter", "promoter_group"))
        )
    ):
        if person:
            out.setdefault(sym, set()).add(person.strip().upper())
    return out


class InsiderCollector(Collector[parsers.InsiderRow]):
    name = "nse_insider"

    def fetch(self) -> Any:
        return nse_get(src("insider")["path"].format(**_range(7)), self.timeout_s)

    def parse(self, raw: Any) -> list[parsers.InsiderRow]:
        return parsers.parse_insider(raw)

    def store(self, items: list[parsers.InsiderRow], engine: Engine | None) -> int:
        return store_insider(items, engine)


def store_insider(items: list[parsers.InsiderRow], engine: Engine | None = None) -> int:
    added = 0
    with session_scope(engine) as s:
        fno = fno_symbols(s)
        for r in items:
            if r.symbol not in fno or s.scalar(
                select(InsiderTrade.id).where(InsiderTrade.ref == r.ref)
            ):
                continue
            mat = rules.value_materiality("insider", r.value_inr, *stats_of(s, r.symbol))
            row = InsiderTrade(
                ref=r.ref,
                symbol=r.symbol,
                person=r.person,
                person_category=r.person_category,
                counterparty=r.counterparty,
                side=r.side,
                quantity=r.quantity,
                value_inr=r.value_inr,
                mode=r.mode,
                event_date=r.event_date,
                disclosed_at=r.disclosed_at,
                subtype=r.subtype,
                material=mat.material,
                materiality_json=mat.as_json(),
                raw_json=r.raw,
            )
            if mat.material:
                row.news_id = evidence.emit(
                    s,
                    category="insider",
                    ref=r.ref,
                    symbol=r.symbol,
                    subtype=r.subtype,
                    disclosed_at=r.disclosed_at,
                    title=f"insider {r.side} by {r.counterparty.replace('_', ' ')} "
                    f"({r.person or 'unnamed'}), Rs {(r.value_inr or 0) / 1e7:.2f} cr",
                    url=None,
                    detail=f"Trade date {r.event_date}; disclosed {r.disclosed_at:%d-%b %H:%M}; "
                    + "; ".join(mat.reasons),
                )
            s.add(row)
            added += 1
    return added


class DealsCollector(Collector[parsers.DealRow]):
    timeout_s = 30.0

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.name = f"nse_{kind}_deals"  # type: ignore[misc]

    def fetch(self) -> Any:
        return nse_get(src(f"{self.kind}_deals")["path"].format(**_range(5)), self.timeout_s)

    def parse(self, raw: Any) -> list[parsers.DealRow]:
        return parsers.parse_deals(raw, self.kind)

    def store(self, items: list[parsers.DealRow], engine: Engine | None) -> int:
        return store_deals(items, engine)


def store_deals(items: list[parsers.DealRow], engine: Engine | None = None) -> int:
    added = 0
    with session_scope(engine) as s:
        fno = fno_symbols(s)
        promoters = known_promoters(s)
        for r in items:
            if r.symbol not in fno or s.scalar(
                select(BulkBlockDeal.id).where(BulkBlockDeal.ref == r.ref)
            ):
                continue
            if (
                r.counterparty == "other"
                and r.client
                and r.client.strip().upper() in promoters.get(r.symbol, set())
            ):
                r.counterparty = "promoter"
            mat = rules.value_materiality("deals", r.value_inr, *stats_of(s, r.symbol))
            row = BulkBlockDeal(
                ref=r.ref,
                kind=r.kind,
                symbol=r.symbol,
                client=r.client,
                counterparty=r.counterparty,
                side=r.side,
                quantity=r.quantity,
                price=r.price,
                value_inr=r.value_inr,
                event_date=r.event_date,
                disclosed_at=r.disclosed_at,
                subtype=r.subtype,
                material=mat.material,
                materiality_json=mat.as_json(),
                raw_json=r.raw,
            )
            if mat.material:
                row.news_id = evidence.emit(
                    s,
                    category=f"{r.kind}_deal",
                    ref=r.ref,
                    symbol=r.symbol,
                    subtype=r.subtype,
                    disclosed_at=r.disclosed_at,
                    title=f"{r.kind} deal: {r.counterparty.replace('_', ' ')} {r.side} "
                    f"({r.client or 'unnamed'}), Rs {(r.value_inr or 0) / 1e7:.2f} cr",
                    url=None,
                    detail=f"Deal date {r.event_date} at Rs {r.price}; " + "; ".join(mat.reasons),
                )
            s.add(row)
            added += 1
    return added


class ShareholdingCollector(Collector[parsers.HoldingRow]):
    name = "nse_shareholding"
    timeout_s = 30.0

    def fetch(self) -> Any:
        return nse_get(src("shareholding")["path"], self.timeout_s)

    def parse(self, raw: Any) -> list[parsers.HoldingRow]:
        return parsers.parse_shareholding(raw)

    def store(self, items: list[parsers.HoldingRow], engine: Engine | None) -> int:
        return store_shareholding(items, engine)


def store_shareholding(items: list[parsers.HoldingRow], engine: Engine | None = None) -> int:
    """New quarter vs the previous stored quarter -> holding / pledge subtypes (one evidence row
    each)."""
    added = 0
    with session_scope(engine) as s:
        fno = fno_symbols(s)
        for r in sorted(items, key=lambda x: x.event_date):
            if r.symbol not in fno:
                continue
            if s.scalar(
                select(Shareholding.id).where(
                    Shareholding.symbol == r.symbol, Shareholding.event_date == r.event_date
                )
            ):
                continue
            prev = s.scalars(
                select(Shareholding)
                .where(Shareholding.symbol == r.symbol, Shareholding.event_date < r.event_date)
                .order_by(Shareholding.event_date.desc())
                .limit(1)
            ).first()
            subtypes = (
                rules.shareholding_subtypes(
                    {
                        "promoter_pct": prev.promoter_pct,
                        "fii_pct": prev.fii_pct,
                        "dii_pct": prev.dii_pct,
                        "pledge_pct": prev.pledge_pct,
                    },
                    r.stakes(),
                )
                if prev
                else []
            )
            row = Shareholding(
                symbol=r.symbol,
                event_date=r.event_date,
                disclosed_at=r.disclosed_at,
                promoter_pct=r.promoter_pct,
                fii_pct=r.fii_pct,
                dii_pct=r.dii_pct,
                public_pct=r.public_pct,
                pledge_pct=r.pledge_pct,
                subtypes=subtypes,
                subtype=subtypes[0] if subtypes else None,
                material=bool(subtypes),
                materiality_json={"vs_quarter": str(prev.event_date) if prev else None},
                raw_json=r.raw,
            )
            for st in subtypes:
                nid = evidence.emit(
                    s,
                    category="shareholding",
                    ref=f"{r.symbol}:{r.event_date}",
                    symbol=r.symbol,
                    subtype=st,
                    disclosed_at=r.disclosed_at,
                    title=f"shareholding {r.event_date:%b-%Y}: {st.replace('_', ' ')}",
                    url=None,
                    detail=f"Quarter {r.event_date} vs {prev.event_date if prev else '-'}: "
                    f"{_stake_text(prev, r)}",
                )
                row.news_id = row.news_id or nid
            s.add(row)
            added += 1
    return added


def _stake_text(prev: Shareholding | None, r: parsers.HoldingRow) -> str:
    if prev is None:
        return "first quarter on record"
    parts = []
    for key in ("promoter_pct", "fii_pct", "dii_pct", "pledge_pct"):
        a, b = getattr(prev, key), getattr(r, key)
        if a is not None and b is not None:
            parts.append(f"{key.removesuffix('_pct')} {a:.2f}% -> {b:.2f}%")
    return ", ".join(parts)


def scan_ratings(engine: Engine | None = None, days: int = 3) -> int:
    """Credit-rating disclosures are exchange announcements: classify recent ones."""
    since = datetime.now(IST) - timedelta(days=days)
    added = 0
    with session_scope(engine) as s:
        fno = fno_symbols(s)
        for item in s.scalars(
            select(NewsItem).where(
                NewsItem.source == "nse_announcements", NewsItem.published_at >= since
            )
        ):
            text = f"{item.title} {item.body or ''}"
            act = parsers.rating_action(text)
            symbol = item.title.split(":", 1)[0].strip().upper()
            if act is None or symbol not in fno or item.published_at is None:
                continue
            ref = parsers.ref_of("rating", item.id)
            if s.scalar(select(Rating.id).where(Rating.ref == ref)):
                continue
            action, agency = act
            subtype = {"upgrade": "rating_upgrade", "downgrade": "rating_downgrade"}.get(action)
            row = Rating(
                ref=ref,
                symbol=symbol,
                agency=agency,
                action=action,
                # the filing does not give the rating action date reliably
                event_date=item.published_at.date(),
                disclosed_at=item.published_at,
                title=item.title,
                url=item.url,
                subtype=subtype,
                material=subtype is not None,
            )
            if subtype:
                row.news_id = evidence.emit(
                    s,
                    category="rating",
                    ref=ref,
                    symbol=symbol,
                    subtype=subtype,
                    disclosed_at=item.published_at,
                    title=f"credit rating {action} ({agency or 'agency not named'})",
                    url=item.url,
                    detail=item.title,
                )
            s.add(row)
            added += 1
    return added


class RegulatorCollector(Collector[parsers.RegulatorItem]):
    def __init__(self, regulator: str) -> None:
        self.regulator = regulator
        self.name = f"{regulator}_rss"  # type: ignore[misc]

    def fetch(self) -> Any:
        url = src("sebi_orders" if self.regulator == "sebi" else "usfda")["url"]
        resp = httpx.get(url, headers=HEADERS, timeout=self.timeout_s, follow_redirects=True)
        resp.raise_for_status()
        return resp.content

    def parse(self, raw: Any) -> list[parsers.RegulatorItem]:
        return parsers.parse_sebi(raw) if self.regulator == "sebi" else parsers.parse_usfda(raw)

    def store(self, items: list[parsers.RegulatorItem], engine: Engine | None) -> int:
        return store_regulatory(items, engine)


def store_regulatory(items: list[parsers.RegulatorItem], engine: Engine | None = None) -> int:
    """Regulator items whose company name matches one stock with confidence >= the threshold
    become evidence (sebi_order / usfda_adverse). Others are stored with their best guesses and
    scores for review (weekly digest) and create nothing."""
    added = 0
    with session_scope(engine) as s:
        insts = [
            InstrumentInfo(i.symbol, i.name, i.sector, tuple(i.aliases or []))
            for i in s.scalars(select(Instrument))
            if i.is_fno
        ]
        th = matching.threshold()
        for r in items:
            if s.scalar(select(RegulatoryAction.id).where(RegulatoryAction.ref == r.ref)):
                continue
            cands = matching.match(r.entity, insts)
            best = cands[0] if cands else None
            symbol = best.symbol if best and best.score >= th else None
            subtype = "sebi_order" if r.regulator == "sebi" else "usfda_adverse"
            row = RegulatoryAction(
                ref=r.ref,
                regulator=r.regulator,
                action_type=r.action_type,
                symbol=symbol,
                event_date=r.event_date,
                disclosed_at=r.disclosed_at,
                title=r.title,
                url=r.url,
                subtype=subtype,
                material=symbol is not None,
                match_confidence=best.score if best else 0.0,
                match_candidates=[
                    {"symbol": m.symbol, "score": m.score, "via": m.via} for m in cands
                ],
                materiality_json={"entity": r.entity, "threshold": th},
            )
            if symbol:
                row.news_id = evidence.emit(
                    s,
                    category=r.regulator,
                    ref=r.ref,
                    symbol=symbol,
                    subtype=subtype,
                    disclosed_at=r.disclosed_at,
                    title=r.title[:200],
                    url=r.url,
                    detail=f"{r.regulator.upper()} {r.action_type}: {r.title} "
                    f"(name match {best.score:.2f} via '{best.via}')",  # type: ignore[union-attr]
                )
            s.add(row)
            added += 1
    return added


collect_insider = InsiderCollector().run
collect_bulk_deals = DealsCollector("bulk").run
collect_block_deals = DealsCollector("block").run
collect_shareholding = ShareholdingCollector().run
collect_sebi = RegulatorCollector("sebi").run
collect_usfda = RegulatorCollector("usfda").run
