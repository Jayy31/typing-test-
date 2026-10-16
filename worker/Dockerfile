# Build from the repo root:  docker build -f worker/Dockerfile -t news-options-worker .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 TZ=Asia/Kolkata
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app/worker
COPY worker/pyproject.toml worker/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY worker/ ./
COPY data/ /app/data/

RUN useradd --create-home worker
USER worker
CMD ["uv", "run", "--no-dev", "python", "-m", "app.scheduler"]
