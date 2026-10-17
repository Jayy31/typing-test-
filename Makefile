.PHONY: setup migrate test run lint smoke-ai preflight golive-quick golive-full

WORKER = cd worker &&

setup:
	$(WORKER) uv sync --python 3.12

migrate:
	$(WORKER) uv run alembic upgrade head

test:
	$(WORKER) uv run pytest -q

run:
	$(WORKER) uv run python -m app.scheduler

lint:
	$(WORKER) uv run ruff check . && uv run mypy app

smoke-ai:  # live AI smoke test (real models, hard limit Rs 50)
	$(WORKER) uv run python -m app.ai.smoke

preflight:  # env, migrations at head, Telegram + Supabase reachable (run before the first start)
	$(WORKER) uv run python -m app.preflight

golive-quick:  # go-live quick check now (NSE endpoints first); also runs itself 2 h after first start
	$(WORKER) uv run python -m app.golive quick

golive-full:  # first-run report now; also runs itself 24 h after first start
	$(WORKER) uv run python -m app.golive full
