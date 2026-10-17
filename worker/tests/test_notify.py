import json
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Engine, select

from app.ai.client import AiClient
from app.collectors.nse import parse_ban_list, parse_corporate_actions
from app.config import IST, Settings
from app.db.config_store import get_config
from app.db.models import Alert, JobRun
from app.db.session import session_scope
from app.health.cleanup import cleanup
from app.notify import templates
from app.notify.outbox import enqueue_alert
from app.notify.telegram import TelegramBot, chunk
from app.scheduler import heartbeat
from app.signals.service import run_signal_cycle
from tests.helpers import FakeMessages, analyst_output, seed_instruments, seed_news

TOKEN = "123456:SECRET-TOKEN"
CHAT = "42"


class Recorder:
    """httpx mock transport that records Telegram calls and replays canned updates."""

    def __init__(self, updates: list[dict[str, Any]] | None = None, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.updates = updates or []
        self.fail = fail

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        self.calls.append((method, body))
        if self.fail:
            return httpx.Response(
                401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
            )
        result: Any = self.updates if method == "getUpdates" else {"message_id": 1}
        return httpx.Response(200, json={"ok": True, "result": result})

    def sent_texts(self) -> list[str]:
        return [b["text"] for m, b in self.calls if m == "sendMessage"]


def bot(engine: Engine, rec: Recorder, **kw: Any) -> TelegramBot:
    settings = Settings(_env_file=None, telegram_bot_token=TOKEN, telegram_chat_id=CHAT, **kw)  # type: ignore[call-arg]
    return TelegramBot(settings, engine, httpx.Client(transport=httpx.MockTransport(rec)))


def update(text: str, chat: int = int(CHAT), uid: int = 1) -> dict[str, Any]:
    return {"update_id": uid, "message": {"chat": {"id": chat}, "text": text, "message_id": uid}}


def test_chunk_respects_limit() -> None:
    text = "\n".join(f"line {i} " + "x" * 90 for i in range(200))
    parts = chunk(text, 1000)
    assert all(len(p) <= 1000 for p in parts) and "\n".join(parts) == text
    assert chunk("x" * 2500, 1000) and all(len(p) <= 1000 for p in chunk("x" * 2500, 1000))


def test_enqueue_is_idempotent(engine: Engine) -> None:
    with session_scope(engine) as s:
        assert enqueue_alert(s, "health", "a", "k1") is True
        assert enqueue_alert(s, "health", "b", "k1") is False
        assert len(s.scalars(select(Alert)).all()) == 1


def test_send_pending_delivers_and_marks_sent(engine: Engine) -> None:
    rec = Recorder()
    with session_scope(engine) as s:
        enqueue_alert(s, "health", "SYSTEM PROBLEM x", "h1")
    assert bot(engine, rec).send_pending(delay_s=0) == 1
    assert rec.sent_texts() == ["SYSTEM PROBLEM x"]
    assert rec.calls[0][1]["chat_id"] == CHAT
    with session_scope(engine) as s:
        a = s.scalars(select(Alert)).one()
        assert a.status == "sent" and a.sent_at is not None
    assert bot(engine, rec).send_pending(delay_s=0) == 0  # not re-sent


def test_signal_alert_has_buttons(engine: Engine) -> None:
    seed_instruments(engine)
    seed_news(engine)
    run_signal_cycle(
        engine,
        AiClient(
            Settings(_env_file=None, ai_model_analyst="m"),
            engine,  # type: ignore[call-arg]
            FakeMessages(analyst_output()),
        ),
    )
    rec = Recorder()
    bot(engine, rec, dashboard_base_url="https://dash.example/").send_pending(delay_s=0)
    (call,) = [b for m, b in rec.calls if m == "sendMessage"]
    kb = call["reply_markup"]["inline_keyboard"][0]
    assert kb[0] == {"text": "View full analysis", "url": "https://dash.example/signals/1"}
    assert {"text": "Why filtered", "callback_data": "why:1"} in kb and kb[-1][
        "callback_data"
    ] == "dismiss"
    assert "NEW SIGNAL  #S-1" in call["text"] and "CALL" in call["text"]


def test_failures_retry_then_give_up_and_never_leak_token(engine: Engine, caplog: Any) -> None:
    with session_scope(engine) as s:
        enqueue_alert(s, "health", "x", "h1")
    rec = Recorder(fail=True)
    b = bot(engine, rec)
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            b.send_pending(delay_s=0)
    with session_scope(engine) as s:
        a = s.scalars(select(Alert)).one()
        assert a.status == "failed" and a.attempts == 3
        assert TOKEN not in (a.error or "") and "401" in (a.error or "")
    assert TOKEN not in caplog.text
    assert logging.getLogger("httpx").level >= logging.WARNING


def test_stale_and_muted_alerts_are_not_sent(engine: Engine) -> None:
    rec = Recorder()
    with session_scope(engine) as s:
        enqueue_alert(s, "signal", "old signal", "s1")
        enqueue_alert(s, "signal", "new signal", "s2")
        enqueue_alert(s, "health", "problem", "h1")
        old = s.scalars(select(Alert).where(Alert.dedupe_key == "s1")).one()
        old.created_at = datetime.now(IST) - timedelta(hours=8)
    b = bot(engine, rec)
    b.reply_for("/mute")
    assert b.send_pending(delay_s=0) == 1
    assert rec.sent_texts() == [
        "problem"
    ]  # health always goes; muted chatter and stale are skipped
    with session_scope(engine) as s:
        st = {a.dedupe_key: a.status for a in s.scalars(select(Alert))}
    assert st == {"s1": "expired", "s2": "muted", "h1": "sent"}
    assert b.reply_for("/unmute") == "Alerts on."


def test_commands_only_from_owner_chat(engine: Engine) -> None:
    rec = Recorder([update("/help", chat=999, uid=5), update("/help", uid=6)])
    b = bot(engine, rec)
    assert b.poll() == 2
    assert len(rec.sent_texts()) == 1 and "Commands" in rec.sent_texts()[0]
    with session_scope(engine) as s:
        assert get_config(s, "telegram_offset") == 7  # offset advanced past the last update


def test_why_command_shows_full_reasoning(engine: Engine) -> None:
    seed_instruments(engine)
    seed_news(engine)
    run_signal_cycle(
        engine,
        AiClient(
            Settings(_env_file=None, ai_model_analyst="m"),
            engine,  # type: ignore[call-arg]
            FakeMessages(analyst_output()),
        ),
    )
    text = bot(engine, Recorder()).reply_for("/why S-1")
    for section in (
        "WHY IT WAS FILTERED",
        "LINKED NEWS",
        "SENTIMENT",
        "MACRO AT SIGNAL TIME",
        "OPTION DATA",
        "SCORE BREAKDOWN",
        "ELIGIBILITY",
        "RISKS",
        "COUNTER-ARGUMENTS",
        "HISTORICAL ANALOGUES",
        "TIMELINE",
    ):
        assert section in text, section
    assert (
        "telecom_tariff_hike" in text and "impact: 80 x 0.25" in text and "PASS fno_segment" in text
    )
    assert bot(engine, Recorder()).reply_for("/why 1") == text
    assert "No signal" in bot(engine, Recorder()).reply_for("/why S-999")
    assert bot(engine, Recorder()).reply_for("/why abc").startswith("Usage")


def test_signals_open_closed_stats_commands(engine: Engine) -> None:
    b = bot(engine, Recorder())
    assert b.reply_for("/signals") == "No live signals."
    assert b.reply_for("/open") == "No open paper trades."
    assert b.reply_for("/closed") == "No closed trades yet."
    assert "no closed trades" in b.reply_for("/stats")
    assert b.reply_for("/nonsense").startswith("Unknown")


def test_dismiss_and_why_buttons(engine: Engine) -> None:
    cb_dismiss = {
        "update_id": 1,
        "callback_query": {
            "id": "c1",
            "data": "dismiss",
            "message": {"chat": {"id": 42}, "message_id": 77},
        },
    }
    cb_foreign = {
        "update_id": 2,
        "callback_query": {
            "id": "c2",
            "data": "dismiss",
            "message": {"chat": {"id": 1}, "message_id": 78},
        },
    }
    rec = Recorder([cb_dismiss, cb_foreign])
    bot(engine, rec).poll()
    methods = [m for m, _ in rec.calls]
    assert methods.count("deleteMessage") == 1 and rec.calls[-1][1].get("message_id") == 77


def test_templates() -> None:
    assert templates.sentiment_label(0.7) == "Strongly positive"
    assert templates.sentiment_label(-0.3) == "Negative"
    assert templates.sentiment_label(0.0) == "Mixed"
    assert templates.first_sentence("Airtel hikes tariffs. ARPU rises.") == "Airtel hikes tariffs"
    assert (
        templates.confidence_label(82, 80) == "High"
        and templates.confidence_label(70, 80) == "Medium"
    )


def test_heartbeat_queues_deduped_health_alert(engine: Engine) -> None:
    with session_scope(engine) as s:
        for _ in range(3):
            s.add(
                JobRun(
                    job="news_day",
                    started_at=datetime.now(IST),
                    finished_at=datetime.now(IST),
                    status="error",
                    error="boom",
                )
            )
    assert heartbeat(engine) == 1
    assert heartbeat(engine) == 1  # still failing, but the alert is not duplicated
    with session_scope(engine) as s:
        (a,) = s.scalars(select(Alert)).all()
        assert a.kind == "health" and "news_day" in a.text and "boom" in a.text


def test_cleanup_keeps_errors_and_recent_runs(engine: Engine) -> None:
    old = datetime.now(IST) - timedelta(days=30)
    with session_scope(engine) as s:
        s.add(JobRun(job="a", started_at=old, status="ok"))
        s.add(JobRun(job="a", started_at=old, status="error", error="x"))
        s.add(JobRun(job="a", started_at=datetime.now(IST), status="ok"))
    assert cleanup(engine) == 1
    with session_scope(engine) as s:
        assert sorted(r.status for r in s.scalars(select(JobRun))) == ["error", "ok"]


def test_parse_ban_list_and_corporate_actions() -> None:
    csv = "Securities in Ban For Trade Date 15-OCT-2026:\n1,RBLBANK\n2,idea\n"
    assert parse_ban_list(csv) == ["RBLBANK", "IDEA"]
    assert parse_ban_list("Securities in Ban For Trade Date 15-OCT-2026:\n") == []
    rows = [
        {
            "symbol": "TCS",
            "subject": "Face Value Split (Sub-Division) - From Rs 10 To Rs 5",
            "exDate": "20-Oct-2026",
        },
        {"symbol": "INFY", "subject": "Bonus 1:1", "exDate": "21-Oct-2026"},
        {"symbol": "ITC", "subject": "Dividend - Rs 7 Per Share", "exDate": "22-Oct-2026"},
        {"symbol": "SBIN", "subject": "Annual General Meeting", "exDate": "23-Oct-2026"},
    ]
    assert [(e["symbol"], e["event_type"]) for e in parse_corporate_actions(rows)] == [
        ("TCS", "split"),
        ("INFY", "bonus"),
        ("ITC", "dividend"),
    ]


def test_why_prices_have_two_decimals() -> None:
    from app.notify.explain import _px

    assert _px(23.16078799670254) == "23.16" and _px(None) == "n/a"
