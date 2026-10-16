"""Telegram message texts. Plain text only (no parse_mode), so nothing needs escaping."""

from datetime import date

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
    return "n/a" if x is None else (f"{x:+.1f}%" if signed else f"{x:.0f}%")


def v2_lines(sig: Signal, analogues_line: str | None = None) -> list[str]:
    """DESIGN_V2 lines, shown only when the value exists. Calibration status is always explicit."""
    lines: list[str] = []
    if sig.predicted_move_pct is not None:
        src = SOURCE_LABEL.get(sig.predicted_move_source or "", sig.predicted_move_source or "?")
        lines.append(f"Predicted move: {pct(sig.predicted_move_pct)} (source: {src})")
    if sig.implied_move_pct is not None or sig.edge_ratio is not None:
        lines.append(
            f"Implied move: {pct(sig.implied_move_pct, False)}   "
            f"Edge ratio: {'n/a' if sig.edge_ratio is None else f'{sig.edge_ratio:.2f}'}"
        )
    calib = "" if sig.calibrator_id else " (not yet calibrated)"
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
