.PHONY: setup migrate test run lint

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
