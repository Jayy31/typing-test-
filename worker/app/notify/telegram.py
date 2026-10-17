"""Telegram bot over plain HTTPS (httpx): send alerts, poll commands, answer buttons.

Security: the bot token lives in every request URL, so errors are re-raised WITHOUT the URL
and the httpx logger is silenced. Only the configured TELEGRAM_CHAT_ID is ever obeyed.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.config import IST, Settings, get_settings
from app.db.config_store import get_config, set_config
from app.db.models import Alert, PaperTrade, Signal
from app.db.session import session_scope
from app.notify.explain import build_why
from app.notify.templates import sid
from app.timeutil import as_ist

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs at INFO
logging.getLogger("httpcore").setLevel(logging.WARNING)
API = "https://api.telegram.org"
MAX_LEN = 4000
STALE_AFTER = timedelta(hours=6)


class TelegramError(RuntimeError):
    """Telegram call failed. The message never contains the token."""


def chunk(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split on blank lines / newlines so each part fits Telegram's message limit."""
    parts: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:
            line, rest = line[:limit], line[limit:]
            parts.append((cur + line) if not cur else cur)
            cur, line = "", rest
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


class TelegramBot:
    def __init__(
        self,
        settings: Settings | None = None,
        engine: Engine | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.engine = engine
        self._http = http or httpx.Client(timeout=30.0)

    @property
    def configured(self) -> bool:
        return bool(self.settings.telegram_bot_token and self.settings.telegram_chat_id)

    def call(self, method: str, **params: Any) -> Any:
        url = f"{API}/bot{self.settings.telegram_bot_token}/{method}"
        try:
            resp = self._http.post(url, json=params)
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramError(f"{method} failed: {type(exc).__name__}") from None
        if not data.get("ok"):
            raise TelegramError(
                f"{method} failed: {data.get('error_code')} {data.get('description')}"
            )
        return data["result"]

    def send(self, text: str, buttons: list[dict[str, str]] | None = None) -> None:
        parts = chunk(text)
        for i, part in enumerate(parts):
            params: dict[str, Any] = {
                "chat_id": self.settings.telegram_chat_id,
                "text": part,
                "disable_web_page_preview": True,
            }
            if buttons and i == len(parts) - 1:
                params["reply_markup"] = {"inline_keyboard": [buttons]}
            self.call("sendMessage", **params)

    def signal_buttons(self, signal_id: int) -> list[dict[str, str]]:
        buttons = []
        if self.settings.dashboard_base_url:
            url = f"{self.settings.dashboard_base_url.rstrip('/')}/signals/{signal_id}"
            buttons.append({"text": "View full analysis", "url": url})
        buttons += [
            {"text": "Why filtered", "callback_data": f"why:{signal_id}"},
            {"text": "Dismiss", "callback_data": "dismiss"},
        ]
        return buttons

    # ---- outbox ---------------------------------------------------------------------
    def send_pending(self, delay_s: float = 1.1, limit: int = 20) -> int:
        """Deliver queued alerts in order. Muted chatter is skipped; health alerts always go."""
        if not self.configured:
            return 0
        sent = 0
        now = datetime.now(IST)
        with session_scope(self.engine) as s:
            muted = bool(get_config(s, "alerts_muted", False))
            rows = list(
                s.scalars(
                    select(Alert).where(Alert.status == "pending").order_by(Alert.id).limit(limit)
                )
            )
            for a in rows:
                if now - as_ist(a.created_at) > STALE_AFTER:
                    a.status = "expired"
                elif muted and a.kind != "health":
                    a.status = "muted"
        with session_scope(self.engine) as s:
            ids = [
                a.id
                for a in s.scalars(
                    select(Alert).where(Alert.status == "pending").order_by(Alert.id).limit(limit)
                )
            ]
        for alert_id in ids:
            with session_scope(self.engine) as s:
                alert = s.get(Alert, alert_id)
                assert alert is not None
                try:
                    buttons = (
                        self.signal_buttons(alert.signal_id)
                        if alert.kind == "signal" and alert.signal_id
                        else None
                    )
                    self.send(alert.text, buttons)
                    alert.status, alert.sent_at = "sent", datetime.now(IST)
                    sent += 1
                except TelegramError as exc:
                    alert.attempts += 1
                    alert.error = str(exc)
                    alert.status = "failed" if alert.attempts >= 3 else "pending"
                    log.warning("alert %s not sent: %s", alert.id, exc)
            if delay_s:
                time.sleep(delay_s)
        return sent

    # ---- commands -------------------------------------------------------------------
    def poll(self) -> int:
        """Fetch new updates once (short poll) and answer them. Returns updates handled."""
        if not self.configured:
            return 0
        with session_scope(self.engine) as s:
            offset = int(get_config(s, "telegram_offset", 0))
        updates = self.call(
            "getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"]
        )
        for u in updates:
            try:
                self._handle(u)
            except TelegramError as exc:
                log.warning("reply failed: %s", exc)
            offset = u["update_id"] + 1
        if updates:
            with session_scope(self.engine) as s:
                set_config(s, "telegram_offset", offset)
        return len(updates)

    def _allowed(self, chat_id: Any) -> bool:
        return str(chat_id) == str(self.settings.telegram_chat_id)

    def _handle(self, u: dict[str, Any]) -> None:
        if "callback_query" in u:
            cb = u["callback_query"]
            msg = cb.get("message") or {}
            if not self._allowed((msg.get("chat") or {}).get("id")):
                return
            data = cb.get("data", "")
            self.call("answerCallbackQuery", callback_query_id=cb["id"])
            if data == "dismiss":
                self.call("deleteMessage", chat_id=msg["chat"]["id"], message_id=msg["message_id"])
            elif data.startswith("why:"):
                self.send(self.reply_for(f"/why {data[4:]}"))
            return
        msg = u.get("message") or {}
        if not self._allowed((msg.get("chat") or {}).get("id")) or not msg.get("text"):
            return
        self.send(self.reply_for(msg["text"]))

    def reply_for(self, text: str) -> str:
        cmd, _, arg = text.strip().partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/start", "/help"):
            return HELP
        with session_scope(self.engine) as s:
            if cmd == "/why":
                digits = arg.strip().upper().removeprefix("S-")
                if not digits.isdigit():
                    return "Usage: /why S-1042"
                return build_why(s, int(digits)) or f"No signal {arg.strip()}."
            if cmd == "/signals":
                return _live_signals(s)
            if cmd == "/open":
                return _open_trades(s)
            if cmd == "/closed":
                return _closed_trades(s)
            if cmd == "/stats":
                return _stats(s)
            if cmd == "/calibration":
                return _calibration(s)
            if cmd == "/portfolio":
                return _portfolio(s)
            if cmd == "/exposure":
                return _exposure(s)
            if cmd in ("/mute", "/unmute"):
                set_config(s, "alerts_muted", cmd == "/mute")
                return (
                    "Signal alerts muted (health alerts still arrive)."
                    if cmd == "/mute"
                    else "Alerts on."
                )
        return "Unknown command. Try /help"


HELP = (
    "Commands:\n/why S-<id>  full reasoning for a signal\n/signals  live signals\n"
    "/open  open paper trades\n/closed  recent closed trades\n/stats  performance (with/without unfillable, panel vs single)\n"
    "/calibration  calibration by source (records and independent events)\n"
    "/portfolio  equity and drawdown\n/exposure  open risk per theme\n"
    "/mute  /unmute  pause or resume signal alerts (health alerts always arrive)"
)


def _live_signals(s: Session) -> str:
    live = s.scalars(
        select(Signal)
        .where(Signal.status.in_(("NEW", "ACTIVE", "UPGRADED", "DOWNGRADED")))
        .order_by(Signal.score.desc())
        .limit(15)
    ).all()
    lines = [f"#{sid(g.id)} {g.symbol} {g.direction} {g.score:.0f} ({g.status})" for g in live]
    return "\n".join(lines) or "No live signals."


def _open_trades(s: Session) -> str:
    from app.trading.portfolio import open_value

    trades = s.scalars(select(PaperTrade).where(PaperTrade.status == "OPEN").limit(20)).all()
    lines = []
    for t in trades:
        unreal, _, _ = open_value(t)
        mark = t.last_premium if t.last_premium is not None else t.entry_premium
        flag = f"  [{t.review_flag}]" if t.review_flag else ""
        lines.append(
            f"T{t.id} {t.symbol} {t.strike:g} {t.option_type} {t.expiry:%d-%b}: entry {t.entry_premium:.2f} "
            f"now {mark:.2f}, P&L Rs {unreal:+,.0f}{flag}"
        )
    return "\n".join(lines) or "No open paper trades."


def _stats(s: Session) -> str:
    def line(label: str, trades: list[PaperTrade]) -> str:
        if not trades:
            return f"{label}: no closed trades"
        pnl = [t.pnl or 0.0 for t in trades]
        wins = [p for p in pnl if p > 0]
        return f"{label}: {len(pnl)} trades, win rate {len(wins) / len(pnl):.0%}, P&L Rs {sum(pnl):+,.0f}"

    closed = list(s.scalars(select(PaperTrade).where(PaperTrade.status == "CLOSED")))
    fillable = [t for t in closed if t.fillability != "unfillable"]
    slip = sum(t.slippage_inr or 0.0 for t in closed)
    gaps = sum(1 for t in closed if (t.slippage_inr or 0) < 0)
    src = dict(s.execute(select(Signal.id, Signal.prediction_source)).all())
    return (
        line("All", closed)
        + "\n"
        + line("Excluding unfillable", fillable)
        + "\n"
        + line("Panel predictions", [t for t in closed if src.get(t.signal_id) == "panel"])
        + "\n"
        + line("Single analyst", [t for t in closed if src.get(t.signal_id) == "single"])
        + f"\nGap/slippage vs trigger levels: Rs {slip:+,.0f} across {gaps} trade(s)"
    )


def _calibration(s: Session) -> str:
    from app.calibration import service as calibration
    from app.db.models import Calibrator

    lines = []
    for target in calibration.TARGETS:
        rep = calibration.report(s, target)
        parts = [
            f"{src} {r['records']} rec / {r['events']} events, Brier {r['brier_used']:.3f}"
            if (r := rep.get(src) or {}).get("records")
            else f"{src} none"
            for src in calibration.SOURCES
        ]
        lines.append(f"{target}: " + "; ".join(parts))
        if rep.get("cutoff") is None:
            lines.append("  AI_MODEL_KNOWLEDGE_CUTOFF not set: nothing counts yet (R1)")
    active = s.scalars(select(Calibrator).where(Calibrator.active.is_(True))).all()
    lines += [
        f"active {c.target}/{c.source}: {c.method} v{c.version}, {c.n_events} events, Brier {c.brier:.3f}"
        for c in active
    ] or ["No calibrator yet: probabilities are NOT YET CALIBRATED (shrunk toward the market)."]
    return "\n".join(lines)


def _portfolio(s: Session) -> str:
    from app.db.models import PortfolioSnapshot

    snap = s.scalars(select(PortfolioSnapshot).order_by(PortfolioSnapshot.ts.desc())).first()
    if snap is None:
        return "No portfolio snapshot yet."
    return (
        f"Equity Rs {snap.equity:,.0f} (capital {snap.capital:,.0f})\nRealised {snap.realised_inr:+,.0f}, "
        f"open {snap.unrealised_inr:+,.0f}\nDrawdown {snap.drawdown_pct:.2f}%  open trades {snap.open_trades}, "
        f"premium at risk Rs {snap.premium_at_risk_inr:,.0f}, capital used Rs {snap.capital_used_inr or 0:,.0f}"
        f"\nas of {snap.ts:%d-%b %H:%M}"
    )


def _exposure(s: Session) -> str:
    from app.trading.portfolio import exposure

    ex = exposure(s)
    return (
        "\n".join(
            f"{k}: {v['trades']} trade(s) {', '.join(v['symbols'])}, at risk Rs {v['at_risk_inr']:,.0f}, "
            f"delta notional Rs {v['delta_notional_inr']:,.0f}"
            for k, v in sorted(ex.items())
        )
        or "No open exposure."
    )


def _closed_trades(s: Session) -> str:
    trades = s.scalars(
        select(PaperTrade)
        .where(PaperTrade.status != "OPEN")
        .order_by(PaperTrade.id.desc())
        .limit(10)
    ).all()
    lines = [
        f"T{t.id} {t.symbol} {t.option_type}: Rs {t.pnl or 0:+,.0f} ({t.pnl_pct or 0:+.1f}%) {t.exit_reason}"
        + ("" if t.fillability in (None, "fillable") else f" [{t.fillability}]")
        for t in trades
    ]
    return "\n".join(lines) or "No closed trades yet."


def send_alerts_job() -> int:
    return TelegramBot().send_pending()


def poll_telegram_job() -> int:
    return TelegramBot().poll()
