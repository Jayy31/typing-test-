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


def new_signal(
    sig: Signal,
    thesis: str,
    agg_sentiment: float,
    n_sources: int,
    window_days: int,
    high_at: float,
    entry_line: str | None = None,
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
        f"Reply /why {sid(sig.id)} for the full reasoning"
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
