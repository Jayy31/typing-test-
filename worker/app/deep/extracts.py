"""Cheap-model extracts (Phase 7): monthly auto sales from exchange filings and macro releases
from official press-release feeds. Cheap model first, mid model as fallback; strict schemas."""

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import feedparser
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import Engine, select

from app.ai.client import AiClient, AiOutputError, BudgetExceededError
from app.collectors.base import record_source
from app.collectors.nse import HEADERS
from app.config import IST
from app.db.config_store import get_config, set_config
from app.db.models import BusinessUpdate, Instrument, MacroRelease, NewsItem
from app.db.session import session_scope
from app.deep import evidence, parsers
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
PROMPTS = Path(__file__).resolve().parents[1] / "ai" / "prompts"


class AutoSalesOut(BaseModel):
    is_sales_update: bool
    period: date | None
    total_units: float | None
    total_units_prev_year: float | None
    yoy_pct_printed: float | None = Field(description="Only if printed")


class MacroOut(BaseModel):
    name: Literal["cpi", "wpi", "iip", "gdp", "gst", "pmi_mfg", "pmi_services", "repo_rate", "none"]
    period: date | None
    value: float | None
    prior: float | None


def extract[T: BaseModel](ai: AiClient, prompt: str, user: str, output: type[T]) -> tuple[T, str]:
    """Cheap model, then the mid model on a schema failure. Returns (output, model id)."""
    system = (PROMPTS / f"{prompt}.md").read_text(encoding="utf-8")
    cfg = load_yaml("ai.yaml")["agents"]["extractor"]
    last: Exception | None = None
    for role in ("extractor", "extractor_fallback"):
        try:
            return ai.parse(
                role, system, user[: int(cfg["doc_chars"])], output, int(cfg["max_tokens"])
            ), ai.model_for(role)
        except AiOutputError as exc:
            last = exc
    raise AiOutputError(f"{prompt}: both models failed: {last}")


SEEN_AUTO = "deep.auto_sales.seen"
SEEN_MACRO = "deep.macro_releases.seen"


def _mark_seen(engine: Engine | None, key: str, ref: object, keep: int = 2000) -> None:
    with session_scope(engine) as s:
        seen = list(get_config(s, key, []) or [])
        seen.append(ref)
        set_config(s, key, seen[-keep:])


def auto_sales_yoy(out: AutoSalesOut) -> float | None:
    if out.total_units and out.total_units_prev_year:
        return (out.total_units / out.total_units_prev_year - 1) * 100
    return out.yoy_pct_printed


def auto_subtype(yoy: float | None) -> str | None:
    m = float(load_yaml("deep_data.yaml")["materiality"]["auto_sales"]["min_abs_yoy_pct"])
    if yoy is None or abs(yoy) < m:
        return None
    return "auto_sales_strong" if yoy > 0 else "auto_sales_weak"


def scan_auto_sales(engine: Engine | None = None, ai: AiClient | None = None) -> int:
    """Monthly sales announcements from Auto-sector F&O stocks -> business_updates + evidence."""
    cfg = load_yaml("deep_data.yaml")["auto_sales"]
    since = datetime.now(IST) - timedelta(days=int(cfg["scan_days"]))
    with session_scope(engine) as s:
        autos = {
            i.symbol
            for i in s.scalars(select(Instrument))
            if i.is_fno and i.sector == cfg["sector"]
        }
        todo = [
            (n.id, n.title, n.body or "", n.url, n.published_at)
            for n in s.scalars(
                select(NewsItem).where(
                    NewsItem.source == "nse_announcements", NewsItem.published_at >= since
                )
            )
            if n.title.split(":", 1)[0].strip().upper() in autos
            and any(k in n.title.lower() for k in cfg["title_keywords"])
        ]
        done = set(get_config(s, SEEN_AUTO, []) or [])
    todo = [t for t in todo if t[0] not in done]
    if not todo:
        return 0
    ai = ai or AiClient(engine=engine)
    added = 0
    for news_id, title, body, url, published in todo:
        symbol = title.split(":", 1)[0].strip().upper()
        _mark_seen(engine, SEEN_AUTO, news_id)  # one AI call per announcement, whatever the result
        try:
            out, model = extract(ai, "auto_sales_v1", f"{title}\n\n{body}", AutoSalesOut)
        except (AiOutputError, BudgetExceededError, RuntimeError) as exc:
            log.warning("auto sales extract %s failed: %s", symbol, exc)
            continue
        if not out.is_sales_update or out.period is None or published is None:
            continue
        yoy = auto_sales_yoy(out)
        subtype = auto_subtype(yoy)
        with session_scope(engine) as s:
            if s.scalar(
                select(BusinessUpdate.id).where(
                    BusinessUpdate.symbol == symbol,
                    BusinessUpdate.period == out.period,
                    BusinessUpdate.metric == "total_units",
                )
            ):
                continue
            row = BusinessUpdate(
                symbol=symbol,
                period=out.period,
                metric="total_units",
                value=out.total_units,
                yoy_pct=yoy,
                disclosed_at=published,
                model=model,
                prompt_ver="auto_sales_v1",
                subtype=subtype,
                material=subtype is not None,
                raw_json={"news_id": news_id, **out.model_dump(mode="json")},
            )
            if subtype:
                row.news_id = evidence.emit(
                    s,
                    category="auto_sales",
                    ref=f"{symbol}:{out.period}",
                    symbol=symbol,
                    subtype=subtype,
                    disclosed_at=published,
                    title=f"{out.period:%b-%Y} sales {yoy:+.1f}% YoY",
                    url=url,
                    detail=f"Total units {out.total_units} vs "
                    f"{out.total_units_prev_year} a year ago",
                )
            s.add(row)
            added += 1
    return added


def collect_macro_releases(engine: Engine | None = None, ai: AiClient | None = None) -> int:
    cfg = load_yaml("deep_data.yaml")["macro_releases"]
    keys = [k.lower() for k in cfg["keywords"]]
    added = 0
    for feed in cfg["feeds"]:
        try:
            resp = httpx.get(feed["url"], headers=HEADERS, timeout=20.0, follow_redirects=True)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - one feed must not stop the others
            record_source(engine, f"macro_{feed['name']}", False, f"{type(exc).__name__}: {exc}")
            continue
        record_source(engine, f"macro_{feed['name']}", True)
        for e in feedparser.parse(resp.content).entries[:30]:
            title = str(e.get("title") or "")
            text = f"{title}\n{e.get('summary') or ''}"
            if not any(k in text.lower() for k in keys):
                continue
            ref = parsers.ref_of(e.get("link"), title)
            with session_scope(engine) as s:
                if ref in (get_config(s, SEEN_MACRO, []) or []):
                    continue
            _mark_seen(engine, SEEN_MACRO, ref)
            when = parsers._feed_dt(e) or datetime.now(IST)
            added += _store_release(engine, ai, feed["name"], text, when)
    return added


def _store_release(
    engine: Engine | None, ai: AiClient | None, source: str, text: str, when: datetime
) -> int:
    ai = ai or AiClient(engine=engine)
    try:
        out, _ = extract(ai, "macro_release_v1", text, MacroOut)
    except (AiOutputError, BudgetExceededError, RuntimeError) as exc:
        log.info("macro extract failed: %s", exc)
        return 0
    if out.name == "none" or out.period is None or out.value is None:
        return 0
    with session_scope(engine) as s:
        if s.scalar(
            select(MacroRelease.id).where(
                MacroRelease.name == out.name, MacroRelease.period == out.period
            )
        ):
            return 0
        s.add(
            MacroRelease(
                name=out.name,
                period=out.period,
                value=out.value,
                prior=out.prior,
                released_at=when,
                source=source,
            )
        )
    return 1
