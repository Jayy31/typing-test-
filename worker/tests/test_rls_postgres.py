"""Phase 9 dashboard access policies on a real Postgres (CI runs a Postgres service; locally set
TEST_POSTGRES_URL, e.g. postgresql+psycopg://postgres:pg@localhost:5432/postgres). Skipped otherwise."""

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL not set")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from alembic import command
    from alembic.config import Config

    from app.config import get_settings

    admin = create_engine(str(URL), isolation_level="AUTOCOMMIT")
    name = f"rls_{uuid.uuid4().hex[:8]}"
    with admin.connect() as c:
        c.execute(text(f"CREATE DATABASE {name}"))
    url = str(URL).rsplit("/", 1)[0] + f"/{name}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "app/db/migrations"))
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0011")  # the migrations also reverse cleanly
    command.upgrade(cfg, "head")
    eng = create_engine(url)
    yield eng
    eng.dispose()
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
    get_settings.cache_clear()


def _as(c, email: str) -> None:  # type: ignore[no-untyped-def]
    c.execute(text("SET ROLE authenticated"))
    c.execute(
        text("SELECT set_config('request.jwt.claims', :c, false)"), {"c": f'{{"email":"{email}"}}'}
    )


def _try(c, sql: str) -> bool:  # type: ignore[no-untyped-def]
    c.execute(text("SAVEPOINT s"))
    try:
        c.execute(text(sql))
    except Exception:  # noqa: BLE001
        c.execute(text("ROLLBACK TO SAVEPOINT s"))
        return False
    c.execute(text("RELEASE SAVEPOINT s"))
    return True


def test_owner_only_reads_and_switch_writes_with_audit(db, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.db.owner import sync_owner

    monkeypatch.setenv("DASHBOARD_ALLOWED_EMAIL", "Owner@Example.com")
    get_settings.cache_clear()
    assert sync_owner(db) == 1
    with db.begin() as c:
        c.execute(
            text(
                "INSERT INTO signals (symbol, direction, score, status) VALUES ('TCS','bullish',70,'NEW')"
            )
        )
        c.execute(text("INSERT INTO config VALUES ('alerts_muted', 'false')"))
    with db.begin() as c:
        _as(c, "intruder@example.com")
        assert c.execute(text("SELECT count(*) FROM signals")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM config")).scalar() == 0
        assert not _try(c, "INSERT INTO config VALUES ('feat.deep_data', 'false')")
        _as(c, "owner@example.com")
        assert c.execute(text("SELECT count(*) FROM signals")).scalar() == 1
        assert _try(c, "INSERT INTO config VALUES ('feat.deep_data', 'false')")
        assert _try(c, "UPDATE config SET value_json = 'true' WHERE key = 'alerts_muted'")
        assert _try(c, "INSERT INTO config VALUES ('brief.morning_enabled', 'false')")
        assert not _try(c, "INSERT INTO config VALUES ('source_health', '{}')")  # not a switch
        assert not _try(c, "INSERT INTO config VALUES ('feat.simulator', '\"yes\"')")  # not boolean
        assert not _try(c, "UPDATE signals SET score = 1")  # read-only everywhere else
        assert not _try(c, "DELETE FROM config WHERE key = 'alerts_muted'")
        c.execute(text("RESET ROLE"))
    with db.connect() as c:
        rows = c.execute(
            text("SELECT key, old_value, new_value, changed_by FROM config_audit ORDER BY id")
        ).all()
        tables = (
            c.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"
                )
            )
            .scalars()
            .all()
        )
        policies = set(
            c.execute(
                text("SELECT tablename FROM pg_policies WHERE policyname='owner_read'")
            ).scalars()
        )
    assert [(r[0], r[1], r[2], r[3]) for r in rows[1:]] == [
        ("feat.deep_data", None, False, "owner@example.com"),
        ("alerts_muted", False, True, "owner@example.com"),
        ("brief.morning_enabled", None, False, "owner@example.com"),
    ]
    assert (
        rows[0][0] == "alerts_muted" and rows[0][3] != "owner@example.com"
    )  # the worker's own write
    assert set(tables) == policies  # every table readable by the owner


def test_owner_can_only_decide_pending_suggestions(db, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.db.owner import sync_owner

    monkeypatch.setenv("DASHBOARD_ALLOWED_EMAIL", "owner@example.com")
    get_settings.cache_clear()
    sync_owner(db)
    with db.begin() as c:
        for i, status in ((1, "pending"), (2, "applied")):
            c.execute(
                text(
                    "INSERT INTO suggestions (id, month, kind, param, file, yaml_path, current_value, proposed_value, "
                    f"expected_effect, evidence_json, status) VALUES ({i}, '2026-10-01', 'change', 'p{i}', 'f', 'a.b', 1, 2, 3, '{{}}', '{status}')"
                )
            )
    with db.begin() as c:
        _as(c, "owner@example.com")
        assert _try(c, "UPDATE suggestions SET status = 'accepted' WHERE id = 1")
        assert not _try(c, "UPDATE suggestions SET proposed_value = 99 WHERE id = 1")  # status only
        c.execute(
            text("UPDATE suggestions SET status = 'rejected' WHERE id = 2")
        )  # not pending: no row matches
        c.execute(
            text("UPDATE suggestions SET status = 'applied' WHERE id = 1")
        )  # no longer pending: no-op
        c.execute(text("RESET ROLE"))
    with db.connect() as c:
        rows = dict(c.execute(text("SELECT id, status FROM suggestions")).all())
        who = c.execute(text("SELECT decided_by FROM suggestions WHERE id = 1")).scalar()
    assert rows == {1: "accepted", 2: "applied"} and who == "owner@example.com"


def test_owner_can_delete_lessons_and_it_is_logged(db, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.db.owner import sync_owner

    monkeypatch.setenv("DASHBOARD_ALLOWED_EMAIL", "owner@example.com")
    get_settings.cache_clear()
    sync_owner(db)
    with db.begin() as c:
        c.execute(text("INSERT INTO research_lessons (id, scope_kind, scope, text) VALUES "
                       "(1, 'stock', 'TCS', 'lesson one'), (2, 'sector', 'IT', 'lesson two')"))
    with db.begin() as c:
        _as(c, "intruder@example.com")
        c.execute(text("UPDATE research_lessons SET deleted = true WHERE id = 2"))  # no rows visible
        c.execute(text("RESET ROLE"))
    with db.begin() as c:
        _as(c, "owner@example.com")
        assert _try(c, "UPDATE research_lessons SET deleted = true WHERE id = 1")
        assert not _try(c, "UPDATE research_lessons SET text = 'edited' WHERE id = 2")  # delete only
        assert not _try(c, "DELETE FROM research_lessons WHERE id = 2")
        c.execute(text("UPDATE research_lessons SET deleted = false WHERE id = 1"))  # no undelete
        c.execute(text("RESET ROLE"))
    with db.connect() as c:
        rows = dict(c.execute(text("SELECT id, deleted FROM research_lessons")).all())
        who = c.execute(text("SELECT deleted_by FROM research_lessons WHERE id = 1")).scalar()
        audit = c.execute(text("SELECT key, changed_by FROM config_audit "
                               "WHERE key LIKE 'research.lesson.%'")).all()
    assert rows == {1: True, 2: False} and who == "owner@example.com"
    assert [tuple(a) for a in audit] == [("research.lesson.1", "owner@example.com")]


def test_lab_hypothesis_rationale_is_frozen(db) -> None:  # type: ignore[no-untyped-def]
    with db.begin() as c:
        c.execute(text("INSERT INTO lab_hypotheses (id, name, source, rationale, expected_effect, status) "
                       "VALUES (1, 'h', 'known_pattern', 'why', '+3%', 'registered')"))
    with db.begin() as c:
        c.execute(text("UPDATE lab_hypotheses SET status = 'testing' WHERE id = 1"))  # allowed
        assert not _try(c, "UPDATE lab_hypotheses SET rationale = 'changed' WHERE id = 1")
        assert not _try(c, "UPDATE lab_hypotheses SET expected_effect = '+9%' WHERE id = 1")
    with db.connect() as c:
        row = c.execute(text("SELECT rationale, status FROM lab_hypotheses WHERE id = 1")).one()
    assert tuple(row) == ("why", "testing")
