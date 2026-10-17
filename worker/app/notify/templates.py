"""Telegram message texts. Plain text only (no parse_mode), so nothing needs escaping."""

from datetime import date
from typing import Any

from app.db.models import Signal


def sentiment_label(agg: float) -> str:
    if agg >= 0.6:
        return "Strongly positive"
    if agg >= 0.25:
        return "Positive"
    if agg <= -0.6:
        return "Strongly negative"
    if agg <= -0.25:
        return "Negative"
    return "Mixed"


def confidence_label(score: float, high_at: float) -> str:
    return "High" if score >= high_at else "Medium"


def first_sentence(text: str, limit: int = 160) -> str:
    one = text.strip().split(". ")[0].rstrip(".")
    return one if len(one) <= limit else one[: limit - 1] + "…"


def sid(signal_id: int) -> str:
    return f"S-{signal_id}"


SOURCE_LABEL = {
    "scenario_tree": "scenario tree",
    "analogues": "historical analogues",
    "blended": "blended model",
}


def pct(x: float | None, signed: bool = True) -> str:
    return "n/a" if x is None else (f"{x:+.1f}%" if signed else f"{x:.1f}%")


def v2_lines(sig: Signal, analogues_line: str | None = None) -> list[str]:
    """DESIGN_V2 lines, shown only when the value exists. Calibration status is always explicit."""
    lines: list[str] = []
    if sig.predicted_move_pct is not None:
        src = SOURCE_LABEL.get(sig.predicted_move_source or "", sig.predicted_move_source or "?")
        shrink_note = ""
        if (
            sig.shrink_weight is not None
            and sig.shrink_weight < 1
            and sig.predicted_move_raw_pct is not None
        ):
            shrink_note = f", shrunk {1 - sig.shrink_weight:.0%} toward market; raw AI {pct(sig.predicted_move_raw_pct)}"
        lines.append(f"Predicted move: {pct(sig.predicted_move_pct)} (source: {src}{shrink_note})")
    if sig.implied_move_pct is not None or sig.edge_ratio is not None:
        lines.append(
            f"Implied move: {pct(sig.implied_move_pct, False)}   "
            f"Edge ratio: {'n/a' if sig.edge_ratio is None else f'{sig.edge_ratio:.2f}'}"
        )
    calib = (
        f" (calibrated: {sig.calibration_method})"
        if sig.calibrator_id and sig.calibration_method not in (None, "shrink")
        else " (not yet calibrated)"
    )
    probs = []
    if sig.p_t1_before_sl is not None:
        probs.append(f"P(T1 before SL): {sig.p_t1_before_sl * 100:.0f}%")
    elif sig.p_move is not None:
        probs.append(f"P(move our way): {sig.p_move * 100:.0f}%")
    if sig.expected_value is not None:
        inr = (
            f" (Rs {sig.expected_value_inr:,.0f}/lot)" if sig.expected_value_inr is not None else ""
        )
        probs.append(f"EV: {sig.expected_value:+.0f}%{inr}")
    if probs:
        lines.append("   ".join(probs) + calib)
    if analogues_line:
        lines.append(analogues_line)
    return lines


def new_signal(
    sig: Signal,
    thesis: str,
    agg_sentiment: float,
    n_sources: int,
    window_days: int,
    high_at: float,
    entry_line: str | None = None,
    analogues_line: str | None = None,
) -> str:
    strike = f"{sig.strike:g}" if sig.strike else "pending"
    expiry = f"{sig.expiry:%d-%b-%Y}" if sig.expiry else "pending"
    return (
        f"NEW SIGNAL  #{sid(sig.id)}   Score {sig.score:.0f}/100   "
        f"Confidence: {confidence_label(sig.score, high_at)}\n"
        f"{sig.symbol}  |  {sig.direction}  |  Strike: {strike}  |  Expiry {expiry}\n"
        f"{entry_line or 'Entry / SL / targets: pending live option data'}\n"
        f"Trigger: {first_sentence(thesis)} (Sector: {sig.sector})\n"
        f"Sentiment: {sentiment_label(agg_sentiment)} ({n_sources} sources)   "
        f"Event window: {window_days} day(s)\n"
        + "".join(f"{line}\n" for line in v2_lines(sig, analogues_line))
        + f"Reply /why {sid(sig.id)} for the full reasoning"
    )


def signal_update(sig: Signal, old_score: float, kind: str, reason: str) -> str:
    return (
        f"SIGNAL {kind}  #{sid(sig.id)}   {sig.symbol} {sig.direction}\n"
        f"Score {old_score:.0f} -> {sig.score:.0f}\nWhy: {first_sentence(reason, 220)}\n"
        f"Reply /why {sid(sig.id)} for details"
    )


def signal_cancelled(sig: Signal, reason: str) -> str:
    return (
        f"SIGNAL CANCELLED  #{sid(sig.id)}   {sig.symbol} {sig.direction}\n"
        f"Why: {first_sentence(reason, 220)}"
    )


def sector_watch(sector: str, agg: float, n_items: int, symbols: list[str], day: date) -> str:
    return (
        f"SECTOR WATCH  {sector}   ({day:%d-%b})\n"
        f"Sentiment: {sentiment_label(agg)} ({agg:+.2f}) from {n_items} news items\n"
        f"Stocks in the news: {', '.join(symbols) or 'sector-level only'}"
    )


def health_problem(job: str, failures: int, error: str | None) -> str:
    return (
        f"SYSTEM PROBLEM\nJob '{job}' failed {failures} times in a row.\n"
        f"Last error: {(error or 'unknown')[:300]}"
    )


# ---- trade alerts (Phase 3b) ---------------------------------------------------------
EVENT_LABEL = {
    "T1": "TARGET 1 HIT",
    "T2": "TARGET 2 HIT",
    "T3": "TARGET 3 HIT",
    "SL": "STOP-LOSS HIT",
    "TRAIL": "TRAILING STOP HIT",
    "USTOP": "UNDERLYING STOP HIT",
    "WINDOW": "TIME WINDOW EXIT",
    "THESIS": "THESIS BROKEN EXIT",
    "TIME": "NEAR-EXPIRY EXIT",
    "NOMOVE": "NO-MOVE EXIT",
}


def _contract(tr: Any) -> str:
    return f"{tr.symbol} {tr.strike:g} {tr.option_type} {tr.expiry:%d-%b}"


def trade_entry(tr: Any, sig: Signal, spot_stop: float | None) -> str:
    ustop = f"   Stock stop: {spot_stop:.2f}" if spot_stop else ""
    fill = (
        ""
        if tr.entry_fill == "fillable"
        else f"\nFill check: {tr.entry_fill} (ask qty {tr.entry_ask_qty})"
    )
    plan = "1 lot at each target" if tr.booking_mode == "per_lot" else "all lots at one target"
    return (
        f"PAPER ENTRY  T{tr.id}  (signal #{sid(sig.id)})\n{_contract(tr)}  {tr.lots} lot(s) x {tr.lot_size}\n"
        f"Entry (ask): {tr.entry_premium:.2f}   SL: {tr.sl:.2f}{ustop}\n"
        f"T1: {tr.t1:.2f}  T2: {tr.t2:.2f}  T3: {tr.t3:.2f}+   Spot: {tr.spot_entry:.2f}\n"
        f"Booking: {tr.booking_mode} ({plan}); capital used Rs {tr.entry_premium * tr.lot_size * tr.lots:,.0f}{fill}"
    )


def trade_event(tr: Any, fill: Any, n_lots: int, slippage: float | None, state: Any) -> str:
    pct = (fill.price / tr.entry_premium - 1) * 100
    left_lots = int(round(state.remaining * tr.lots))
    left = (
        f"{left_lots} lot(s) open, stop now {state.stop:.2f}"
        if not state.closed
        else "position closed"
    )
    lines = [
        f"{EVENT_LABEL.get(fill.event, fill.event)}  T{tr.id}  {_contract(tr)}",
        f"Sold {n_lots} of {tr.lots} lots at {fill.price:.2f} ({pct:+.1f}% vs entry {tr.entry_premium:.2f}); {left}",
    ]
    if fill.trigger is not None:
        if fill.gap:
            lines.append(
                f"Gap/slippage: level {fill.trigger:.2f}, filled {fill.price:.2f} (Rs {slippage or 0:+,.0f})"
            )
        elif fill.price > fill.trigger + 1e-9:
            lines.append(
                f"Filled above the level {fill.trigger:.2f} (Rs {slippage or 0:+,.0f} better)"
            )
    return "\n".join(lines)


def trade_closed(tr: Any) -> str:
    bench = (
        f"\nSame period, stock instead: {tr.benchmark_stock_pct:+.2f}% (Rs {tr.benchmark_stock_inr:,.0f})"
        if tr.benchmark_stock_pct is not None
        else ""
    )
    a = tr.attribution_json or {}
    attr = (
        f"\nP&L split: delta {a.get('delta', 0):+,.0f}, theta {a.get('theta', 0):+,.0f}, "
        f"vega {a.get('vega', 0):+,.0f}, spread {a.get('spread', 0):+,.0f}, other {a.get('residual', 0):+,.0f}"
    )
    slip = (
        f"\nGap/slippage vs trigger levels: Rs {tr.slippage_inr:+,.0f}" if tr.slippage_inr else ""
    )
    fill = "" if tr.fillability == "fillable" else f"\nFill check: {tr.fillability}"
    return (
        f"TRADE CLOSED  T{tr.id}  {_contract(tr)}  {tr.lots} lot(s)  ({tr.exit_reason})\n"
        f"P&L after costs: Rs {tr.pnl:,.0f} ({tr.pnl_pct:+.1f}%)   avg exit {tr.exit_premium:.2f}\n"
        f"Best/worst during trade: {tr.mfe_pct:+.0f}% / {tr.mae_pct:+.0f}%{bench}{attr}{slip}{fill}"
    )


def trade_review_flag(tr: Any, other_signal_id: int) -> str:
    return (
        f"REVIEW  T{tr.id}  {_contract(tr)}\nAn opposite signal #{sid(other_signal_id)} was raised while this "
        f"trade is open. Kept open (opposite_signal_action = flag)."
    )
