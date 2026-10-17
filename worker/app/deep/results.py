"""Results fast-path (Phase 7): new quarterly results -> structured extract -> surprise score ->
evidence -> an immediate signal cycle for the stock.

Extraction: XBRL first (no AI); if that fails, a strict ResultsExtract schema on the cheap model
with the mid model as fallback. Surprise is measured against the company's OWN trend (no
consensus data): revenue and profit growth vs the median of its last four year-on-year growths,
margin change, and stated guidance.
"""

import logging
import math
import statistics
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

import defusedxml.ElementTree as ET  # type: ignore[import-untyped]
from pydantic import BaseModel, Field
from sqlalchemy import Engine, select

from app.ai.client import AiClient, AiOutputError, BudgetExceededError
from app.collectors import nse_http
from app.collectors.base import record_source
from app.collectors.nse import HEADERS, nse_get
from app.config import IST
from app.db.models import EventCalendar, Instrument, ResultsExtract
from app.db.session import session_scope
from app.deep import evidence, parsers
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
PROMPT_VERSION = "results_extract_v1"
CRORE = 1e7

# XBRL element local names (Ind-AS taxonomy) -> field; first found wins
XBRL_FIELDS = {
    "revenue": ("RevenueFromOperations", "InterestEarned", "TotalRevenueFromOperations"),
    "pat": (
        "ProfitOrLossAttributableToOwnersOfParent",
        "ProfitLossForPeriod",
        "NetProfitLossForThePeriod",
        "ProfitLossForThePeriod",
    ),
    "pbt": ("ProfitBeforeTax", "ProfitLossBeforeTax"),
    "finance_costs": ("FinanceCosts",),
    "depreciation": (
        "DepreciationDepletionAndAmortisationExpense",
        "DepreciationAndAmortisationExpense",
    ),
}


class ResultsExtractOut(BaseModel):
    """Strict AI schema (Phase 7)."""

    period_end: date
    consolidated: bool
    revenue_cr: float | None = Field(description="Current quarter revenue, Rs crore")
    revenue_prev_year_cr: float | None = Field(description="Same quarter last year, Rs crore")
    pat_cr: float | None
    pat_prev_year_cr: float | None
    ebitda_cr: float | None
    ebitda_prev_year_cr: float | None
    guidance: Literal["raised", "cut", "maintained", "none"]
    exceptional_items: bool
    notes: list[str] = Field(max_length=2)


@dataclass
class Figures:
    period_end: date
    consolidated: bool | None
    method: str  # xbrl / ai
    revenue: float | None = None
    pat: float | None = None
    ebitda: float | None = None
    revenue_py: float | None = None
    pat_py: float | None = None
    ebitda_py: float | None = None
    guidance: str = "none"
    notes: list[str] = field(default_factory=list)
    model: str | None = None

    @staticmethod
    def _yoy(a: float | None, b: float | None) -> float | None:
        return None if a is None or not b else (a / b - 1) * 100 * (1 if b > 0 else -1)

    @property
    def revenue_yoy(self) -> float | None:
        return self._yoy(self.revenue, self.revenue_py)

    @property
    def pat_yoy(self) -> float | None:
        return self._yoy(self.pat, self.pat_py)

    @property
    def margin_change_pp(self) -> float | None:
        if None in (self.ebitda, self.revenue, self.ebitda_py, self.revenue_py):
            return None
        if not self.revenue or not self.revenue_py:
            return None
        return 100 * (self.ebitda / self.revenue - self.ebitda_py / self.revenue_py)  # type: ignore[operator]


# ---- XBRL ------------------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_xbrl(content: bytes | str, period_end: date | None = None) -> Figures | None:
    """Ind-AS results XBRL -> current quarter + year-ago quarter figures (Rs crore).
    Returns None when revenue or profit for the current quarter is missing."""
    root = ET.fromstring(content)
    periods: dict[str, tuple[date | None, date]] = {}
    for ctx in root.iter():
        if _local(ctx.tag) != "context":
            continue
        start = end = None
        for el in ctx.iter():
            name = _local(el.tag)
            if name == "startDate":
                start = date.fromisoformat((el.text or "").strip())
            elif name in ("endDate", "instant"):
                end = date.fromisoformat((el.text or "").strip())
        cid = ctx.get("id")
        if end is not None and cid:
            periods[str(cid)] = (start, end)
    values: dict[str, dict[str, float]] = {}
    consolidated: bool | None = None
    for el in root.iter():
        name = _local(el.tag)
        if name in (
            "NatureOfReportStandaloneConsolidated",
            "WhetherResultsAreConsolidatedOrStandalone",
        ):
            consolidated = "consol" in (el.text or "").lower()
        ref = el.get("contextRef")
        if not ref or el.text is None:
            continue
        try:
            v = float(el.text.strip())
        except ValueError:
            continue
        values.setdefault(name, {})[ref] = v

    def quarter(ctx: str) -> bool:
        s, e = periods.get(ctx, (None, None))
        return s is not None and e is not None and 80 <= (e - s).days <= 100

    q_ctx = [c for c in periods if quarter(c)]
    if not q_ctx:
        return None
    ends = sorted({periods[c][1] for c in q_ctx}, reverse=True)
    cur_end = period_end if period_end in ends else ends[0]
    py_end = next((e for e in ends if abs((cur_end - e).days - 365) <= 3), None)

    def pick(field_name: str, end: date | None) -> float | None:
        if end is None:
            return None
        for elname in XBRL_FIELDS[field_name]:
            for ctx, v in values.get(elname, {}).items():
                if ctx in q_ctx and periods[ctx][1] == end:
                    return v / CRORE
        return None

    def ebitda(end: date | None) -> float | None:
        parts = [pick(k, end) for k in ("pbt", "finance_costs", "depreciation")]
        return None if any(p is None for p in parts) else sum(parts)  # type: ignore[arg-type]

    fig = Figures(
        period_end=cur_end,
        consolidated=consolidated,
        method="xbrl",
        revenue=pick("revenue", cur_end),
        pat=pick("pat", cur_end),
        ebitda=ebitda(cur_end),
        revenue_py=pick("revenue", py_end),
        pat_py=pick("pat", py_end),
        ebitda_py=ebitda(py_end),
    )
    return fig if fig.revenue is not None and fig.pat is not None else None


# ---- AI fallback -------------------------------------------------------------------------
def ai_extract(ai: AiClient, symbol: str, text: str) -> Figures:
    """Cheap model first; the mid model if the cheap one fails the schema. Raises AiOutputError."""
    from pathlib import Path

    cfg = load_yaml("ai.yaml")["agents"]["extractor"]
    system = (
        Path(__file__).resolve().parents[1] / "ai" / "prompts" / f"{PROMPT_VERSION}.md"
    ).read_text(encoding="utf-8")
    user = f"Company: {symbol}\n\nFiling:\n{text[: int(cfg['doc_chars'])]}"
    last: Exception | None = None
    for role in ("extractor", "extractor_fallback"):
        try:
            out = ai.parse(role, system, user, ResultsExtractOut, int(cfg["max_tokens"]))
            return Figures(
                period_end=out.period_end,
                consolidated=out.consolidated,
                method="ai",
                revenue=out.revenue_cr,
                pat=out.pat_cr,
                ebitda=out.ebitda_cr,
                revenue_py=out.revenue_prev_year_cr,
                pat_py=out.pat_prev_year_cr,
                ebitda_py=out.ebitda_prev_year_cr,
                guidance=out.guidance,
                notes=list(out.notes),
                model=ai.model_for(role),
            )
        except AiOutputError as exc:
            last = exc
            log.info("results extract for %s failed on %s: %s", symbol, role, exc)
    raise AiOutputError(f"results extract failed on both models: {last}")


# ---- surprise ---------------------------------------------------------------------------
def surprise(
    fig: Figures, history: list[tuple[float | None, float | None]]
) -> tuple[float, dict[str, Any]]:
    """Score in [-1, 1] vs the company's own trend. history = earlier (revenue_yoy, pat_yoy),
    newest first. Missing components are left out and the weights renormalised."""
    cfg = load_yaml("deep_data.yaml")["results"]
    w, sc = cfg["weights"], cfg["scale"]
    default = float(cfg["default_trend_yoy_pct"])

    def trend(i: int) -> float:
        vals = [v for h in history[:4] if (v := h[i]) is not None]
        return float(statistics.median(vals)) if len(vals) >= 4 else default

    comps: dict[str, float] = {}
    if fig.revenue_yoy is not None:
        comps["revenue"] = math.tanh((fig.revenue_yoy - trend(0)) / float(sc["revenue_pct"]))
    if fig.pat_yoy is not None:
        comps["pat"] = math.tanh((fig.pat_yoy - trend(1)) / float(sc["pat_pct"]))
    if fig.margin_change_pp is not None:
        comps["margin"] = math.tanh(fig.margin_change_pp / float(sc["margin_pp"]))
    total_w = sum(float(w[k]) for k in comps)
    score = sum(float(w[k]) * v for k, v in comps.items()) / total_w if total_w else 0.0
    adj = float(cfg["guidance_adjust"])
    score += adj if fig.guidance == "raised" else -adj if fig.guidance == "cut" else 0.0
    score = max(-1.0, min(1.0, score))
    return round(score, 3), {
        "components": {k: round(v, 3) for k, v in comps.items()},
        "trend_revenue_yoy": trend(0),
        "trend_pat_yoy": trend(1),
        "trend_quarters": len(history[:4]),
        "guidance": fig.guidance,
    }


def subtype_for(score: float) -> str:
    m = float(load_yaml("deep_data.yaml")["materiality"]["results"]["min_abs_surprise"])
    return "results_beat" if score >= m else "results_miss" if score <= -m else "results_inline"


# ---- feed + store -----------------------------------------------------------------------
@dataclass
class Filing:
    symbol: str
    period_end: date | None
    disclosed_at: datetime
    xbrl_url: str | None
    consolidated: bool | None
    raw: dict[str, Any]


def parse_results_feed(payload: Any) -> list[Filing]:
    rows = payload.get("data", []) if isinstance(payload, dict) else payload
    out: list[Filing] = []
    for r in rows or []:
        symbol = str(r.get("symbol") or "").strip().upper()
        when = parsers.parse_dt(
            r.get("broadCastDate") or r.get("broadcastDate") or r.get("filingDate")
        )
        if not symbol or when is None:
            continue
        cons = str(r.get("consolidated") or "").lower()
        out.append(
            Filing(
                symbol=symbol,
                period_end=parsers.parse_date(r.get("toDate")),
                disclosed_at=when,
                xbrl_url=r.get("xbrl") or None,
                consolidated=None if not cons else cons.startswith("consol"),
                raw=dict(r),
            )
        )
    # consolidated first, so it wins over standalone for the same quarter
    return sorted(out, key=lambda f: (f.symbol, f.period_end or date.min, not f.consolidated))


def _download(url: str) -> bytes:
    resp = nse_http.get(url, headers=HEADERS, timeout=20.0)  # proxy only if url is an NSE host
    resp.raise_for_status()
    return resp.content


def process_filing(
    f: Filing,
    engine: Engine | None,
    ai: AiClient | None,
    now: datetime,
    download: Callable[[str], bytes] = _download,  # injectable for tests
) -> str | None:
    """Extract + score + evidence for one filing. Returns the symbol if new evidence was made."""
    with session_scope(engine) as s:
        inst = s.get(Instrument, f.symbol)
        if inst is None or not inst.is_fno:
            return None
        if f.period_end and s.scalar(
            select(ResultsExtract.id).where(
                ResultsExtract.symbol == f.symbol, ResultsExtract.period_end == f.period_end
            )
        ):
            return None
    fig: Figures | None = None
    doc: bytes | None = None
    if f.xbrl_url:
        try:
            doc = download(f.xbrl_url)
            fig = parse_xbrl(doc, f.period_end)
        except Exception as exc:  # noqa: BLE001 - fall back to the AI extract
            log.info("XBRL failed for %s: %s", f.symbol, exc)
    if fig is None and ai is not None and doc is not None:
        try:
            fig = ai_extract(ai, f.symbol, doc.decode("utf-8", "ignore"))
        except (AiOutputError, BudgetExceededError, RuntimeError) as exc:
            log.warning("results extract for %s failed: %s", f.symbol, exc)
    if fig is None:
        return None
    if fig.consolidated is None:
        fig.consolidated = f.consolidated
    with session_scope(engine) as s:
        if s.scalar(
            select(ResultsExtract.id).where(
                ResultsExtract.symbol == f.symbol, ResultsExtract.period_end == fig.period_end
            )
        ):
            return None
        hist = [
            (r.revenue_yoy_pct, r.pat_yoy_pct)
            for r in s.scalars(
                select(ResultsExtract)
                .where(
                    ResultsExtract.symbol == f.symbol, ResultsExtract.period_end < fig.period_end
                )
                .order_by(ResultsExtract.period_end.desc())
                .limit(4)
            )
        ]
        score, detail = surprise(fig, hist)
        subtype = subtype_for(score)
        row = ResultsExtract(
            symbol=f.symbol,
            period_end=fig.period_end,
            disclosed_at=f.disclosed_at,
            method=fig.method,
            consolidated=fig.consolidated,
            revenue=fig.revenue,
            pat=fig.pat,
            ebitda=fig.ebitda,
            revenue_yoy_pct=fig.revenue_yoy,
            pat_yoy_pct=fig.pat_yoy,
            margin_change_pp=fig.margin_change_pp,
            guidance=fig.guidance,
            surprise_score=score,
            surprise_json={**detail, "notes": fig.notes},
            model=fig.model,
            prompt_ver=PROMPT_VERSION if fig.method == "ai" else "xbrl_v1",
            source_url=f.xbrl_url,
            latency_s=round((now - f.disclosed_at).total_seconds(), 1),
            subtype=subtype,
            material=subtype != "results_inline",
            raw_json=f.raw,
        )
        if row.material:
            row.news_id = evidence.emit(
                s,
                category="results",
                ref=f"{f.symbol}:{fig.period_end}",
                symbol=f.symbol,
                subtype=subtype,
                disclosed_at=f.disclosed_at,
                title=f"Q results {fig.period_end:%b-%Y}: {subtype.split('_')[1]} vs own trend "
                f"(surprise {score:+.2f})",
                url=f.xbrl_url,
                detail=_detail_text(fig, score, detail),
            )
        s.add(row)
        return f.symbol if row.material else None


def _fmt(v: float | None, unit: str = "%") -> str:
    return "n/a" if v is None else f"{v:+.1f}{unit}"


def _detail_text(fig: Figures, score: float, d: dict[str, Any]) -> str:
    return (
        f"{'Consolidated' if fig.consolidated else 'Standalone'} ({fig.method}). Revenue "
        f"{_fmt(fig.revenue_yoy)} YoY (trend {d['trend_revenue_yoy']:+.1f}%), "
        f"profit {_fmt(fig.pat_yoy)} YoY (trend {d['trend_pat_yoy']:+.1f}%), "
        f"EBITDA margin {_fmt(fig.margin_change_pp, ' pp')}; "
        f"guidance {fig.guidance}; surprise {score:+.2f}. " + " ".join(fig.notes)
    )


# ---- polling ----------------------------------------------------------------------------
_state: dict[str, Any] = {"backoff_until": None, "failures": 0}


def results_day(engine: Engine | None, today: date) -> bool:
    """Any F&O stock with results scheduled today or yesterday (late filings)?"""
    with session_scope(engine) as s:
        return bool(
            s.scalar(
                select(EventCalendar.id)
                .where(
                    EventCalendar.event_type == "results",
                    EventCalendar.event_date.in_((today, today - timedelta(days=1))),
                )
                .limit(1)
            )
        )


def in_fast_window(now: datetime) -> bool:
    cfg = load_yaml("deep_data.yaml")["fast_path"]
    t = now.time().replace(tzinfo=None)
    return time.fromisoformat(cfg["start"]) <= t <= time.fromisoformat(cfg["end"])


def poll_results(
    engine: Engine | None = None,
    ai: AiClient | None = None,
    fetch: Callable[[], Any] | None = None,  # injectable for tests
    cycle: Callable[..., int] | None = None,  # signal cycle for the affected stocks
    now: datetime | None = None,
    download: Callable[[str], bytes] = _download,
) -> int:
    """One poll of the results feed. Each new material extract runs the signal cycle for that
    stock at once (panel caps apply; after-hours signals enter at the next open)."""
    now = now or datetime.now(IST)
    path = load_yaml("deep_data.yaml")["sources"]["financial_results"]["path"]
    try:
        payload = fetch() if fetch else nse_get(path, 20.0)
    except Exception as exc:
        record_source(engine, "nse_results", False, f"{type(exc).__name__}: {exc}")
        _backoff(now)
        raise
    record_source(engine, "nse_results", True)
    _state.update(failures=0, backoff_until=None)
    touched: set[str] = set()
    ai = ai or AiClient(engine=engine)
    for f in parse_results_feed(payload):
        if f.disclosed_at < now - timedelta(days=3):
            continue
        t0 = _time.monotonic()
        sym = process_filing(f, engine, ai, now, download)
        if sym:
            touched.add(sym)
            log.info("results evidence for %s in %.1fs", sym, _time.monotonic() - t0)
    if touched:
        if cycle is None:
            from app.signals.service import run_signal_cycle

            cycle = run_signal_cycle
        cycle(engine, only=touched)
    return len(touched)


def _backoff(now: datetime) -> None:
    cfg = load_yaml("deep_data.yaml")["fast_path"]
    _state["failures"] = int(_state["failures"]) + 1
    wait = min(
        float(cfg["poll_seconds"]) * 2 ** _state["failures"], float(cfg["max_backoff_seconds"])
    )
    _state["backoff_until"] = now + timedelta(seconds=wait)


def fast_path_job(engine: Engine | None = None) -> int:
    """Every poll_seconds: active only on results days within the 09:00-23:30 window."""
    now = datetime.now(IST)
    until = _state.get("backoff_until")
    if not in_fast_window(now) or (until and now < until) or not results_day(engine, now.date()):
        return 0
    return poll_results(engine, now=now)


def slow_path_job(engine: Engine | None = None) -> int:
    """Every 15 minutes on other days: catches unscheduled results."""
    now = datetime.now(IST)
    if results_day(engine, now.date()) and in_fast_window(now):
        return 0  # the fast path covers it
    return poll_results(engine, now=now)
