"""AI post-mortems of closed paper trades (v2.1 B4), mid model (AI_MODEL_ANALYST).

One per closed trade, idempotent. The model sees the original thesis, scenarios and
probabilities and what actually happened (fills with trigger vs fill, P&L attribution, stock
move, later news); it returns one primary cause from a fixed list so causes can be counted in
the weekly digest. Paused with the other non-tagger AI work when the budget is near its cap.
"""

import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine, select

from app.ai.client import AiClient, AiOutputError, BudgetExceededError
from app.ai.schemas import PostMortemOutput
from app.config import IST
from app.db.models import (
    Analysis,
    NewsItem,
    NewsTag,
    PaperTrade,
    PostMortem,
    Scenario,
    Signal,
    TradeEvent,
)
from app.db.session import session_scope
from app.notify.outbox import enqueue_alert
from app.timeutil import as_ist
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
PROMPT_VERSION = "postmortem_v1"
PROMPT = (
    Path(__file__).resolve().parents[1] / "ai" / "prompts" / f"{PROMPT_VERSION}.md"
).read_text(encoding="utf-8")
MAX_PER_RUN = 5


def build_message(
    tr: PaperTrade,
    sig: Signal | None,
    an: Analysis | None,
    scenarios: list[Scenario],
    events: list[TradeEvent],
    later_news: list[str],
) -> str:
    sc = (
        "\n".join(
            f"- {x.name}: p={x.probability:.2f}, move {x.stock_move_pct:+.1f}% in {x.timing_days}d"
            for x in scenarios
        )
        or "- none"
    )
    ev = (
        "\n".join(
            f"- {e.ts:%Y-%m-%d %H:%M} {e.event}: premium {e.premium}, lots {e.lots}, trigger {e.trigger_level}, "
            f"gap {bool(e.gap)}, slippage Rs {e.slippage_inr or 0:,.0f}, spot {e.spot}"
            for e in events
        )
        or "- none"
    )
    p = sig
    return (
        f"Trade: {tr.symbol} {tr.option_type} {tr.strike} exp {tr.expiry}, {tr.lots} lot(s) x {tr.lot_size}, "
        f"booking {tr.booking_mode}\n"
        f"Entry {tr.entry_ts:%Y-%m-%d %H:%M} at {tr.entry_premium} (spot {tr.spot_entry}); exit {tr.exit_ts} at "
        f"{tr.exit_premium} (spot {tr.spot_exit}), reason {tr.exit_reason}\n"
        f"P&L Rs {tr.pnl or 0:,.0f} ({tr.pnl_pct or 0:+.1f}% of premium) after costs; total slippage Rs {tr.slippage_inr or 0:,.0f}; "
        f"best {tr.mfe_pct}%, worst {tr.mae_pct}% of premium; fills {tr.fillability}\n"
        f"P&L attribution: {tr.attribution_json}\n"
        f"Stock benchmark (same capital in the stock): {tr.benchmark_stock_pct}%\n\n"
        f"ORIGINAL VIEW ({an.prediction_source if an else '?'} prediction, {an.model if an else ''}):\n"
        f"Direction {an.direction if an else '?'}; thesis: {an.thesis if an else ''}\n"
        f"Predicted move {p.predicted_move_pct if p else None}% (raw AI {p.predicted_move_raw_pct if p else None}%), "
        f"implied {p.implied_move_pct if p else None}%, edge ratio {p.edge_ratio if p else None}, "
        f"P(T1 before SL) {p.p_t1_before_sl if p else None} ({p.calibration_method if p else ''}), "
        f"EV {p.expected_value if p else None}%, IV percentile {p.iv_percentile if p else None}\n"
        f"Scenarios:\n{sc}\nRisks: {an.risks if an else None}\n\n"
        f"TRADE EVENTS:\n{ev}\n\n"
        f"LATER NEWS ON THE STOCK:\n" + ("\n".join(f"- {n}" for n in later_news) or "- none")
    )


def run_post_mortems(engine: Engine | None = None, ai: AiClient | None = None) -> int:
    ai = ai or AiClient(engine=engine)
    if not ai.configured("postmortem"):
        return 0
    max_tokens = int(load_yaml("ai.yaml")["agents"]["postmortem"]["max_tokens"])
    with session_scope(engine) as s:
        done = set(s.scalars(select(PostMortem.trade_id)))
        todo = [
            t.id
            for t in s.scalars(
                select(PaperTrade).where(PaperTrade.status == "CLOSED").order_by(PaperTrade.exit_ts)
            )
            if t.id not in done
        ][:MAX_PER_RUN]
    n = 0
    for tid in todo:
        with session_scope(engine) as s:
            tr = s.get(PaperTrade, tid)
            assert tr is not None
            sig = s.get(Signal, tr.signal_id)
            an = s.get(Analysis, sig.analysis_id) if sig and sig.analysis_id else None
            scen = (
                list(s.scalars(select(Scenario).where(Scenario.analysis_id == an.id))) if an else []
            )
            events = list(
                s.scalars(
                    select(TradeEvent).where(TradeEvent.trade_id == tid).order_by(TradeEvent.ts)
                )
            )
            news = [
                f"{as_ist(i.published_at or i.fetched_at):%Y-%m-%d %H:%M} {i.title}"
                for i in s.scalars(
                    select(NewsItem)
                    .join(NewsTag, NewsTag.news_id == NewsItem.id)
                    .where(NewsTag.symbol == tr.symbol, NewsItem.published_at >= tr.entry_ts)
                    .order_by(NewsItem.published_at)
                    .limit(8)
                )
            ]
            msg = build_message(tr, sig, an, scen, events, news)
            analysis_id, source = (an.id if an else None), (an.prediction_source if an else None)
        try:
            out, usage = ai.parse_with_usage(
                "postmortem", PROMPT, msg, PostMortemOutput, max_tokens
            )
        except BudgetExceededError as exc:
            with session_scope(engine) as s:
                enqueue_alert(
                    s,
                    "health",
                    exc.alert_text(),
                    f"budget:{exc.scope}:postmortem:{datetime.now(IST).date()}",
                )
            break
        except AiOutputError as exc:
            log.warning("post-mortem for trade %s rejected: %s", tid, exc)
            continue
        with session_scope(engine) as s:
            s.add(
                PostMortem(
                    trade_id=tid,
                    analysis_id=analysis_id,
                    prediction_source=source,
                    primary_cause=out.primary_cause,
                    secondary_causes=list(out.secondary_causes),
                    thesis_correct=out.thesis_correct,
                    summary=out.summary,
                    output_json=out.model_dump(mode="json"),
                    model=usage.model,
                    prompt_ver=PROMPT_VERSION,
                    cost_inr=usage.cost_inr,
                )
            )
        n += 1
    return n
