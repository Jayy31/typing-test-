#!/usr/bin/env bash
# Phone setup, part 2 of 2: runs INSIDE the Ubuntu of proot-distro (termux.sh calls it).
# Same result as deploy/bootstrap.sh but without Docker (Android cannot run it): the repo is
# checked out at /opt/desk/repo, Python deps via uv, settings in /opt/desk/.env (secrets hidden),
# history at /opt/desk/history, migrations, then the preflight self-check. Safe to re-run.
set -euo pipefail
REPO=${1:-Jayy31/typing-test-}
DESK=/opt/desk
export DEBIAN_FRONTEND=noninteractive
mkdir -p "$DESK/state" "$DESK/history"

echo "== a. System packages"
apt-get update -q && apt-get install -y -q git curl ca-certificates tzdata build-essential
ln -sf /usr/share/zoneinfo/Asia/Kolkata /etc/localtime

echo "== b. uv (Python manager)"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

echo "== c. Code"
if [ -d "$DESK/repo/.git" ]; then
  git -C "$DESK/repo" fetch -q origin main && git -C "$DESK/repo" checkout -q --detach origin/main
else
  git clone -q "https://github.com/${REPO}.git" "$DESK/repo"
fi
cd "$DESK/repo/worker"
uv sync --frozen --no-dev

echo "== d. Settings ($DESK/.env)"
ENV=$DESK/.env
touch "$ENV" && chmod 600 "$ENV"
grep -q '^HISTORY_DATA_DIR=' "$ENV" || echo "HISTORY_DATA_DIR=$DESK/history" >> "$ENV"
grep -q '^DUCKDB_PATH=' "$ENV" || echo "DUCKDB_PATH=$DESK/history/history.duckdb" >> "$ENV"
SECRET_RE='KEY|TOKEN|SECRET|PASSWORD|MPIN|DATABASE_URL|PROXY|PING_URL'
while IFS= read -r line; do
  key=${line%%=*}
  [[ "$line" =~ ^[A-Z0-9_]+= ]] || continue
  grep -q "^${key}=" "$ENV" && continue          # already set: keep (re-run safe)
  if [[ "$key" =~ $SECRET_RE ]]; then
    read -rsp "$key (hidden, empty = default): " val < /dev/tty; echo
  else
    read -rp "$key (empty = default): " val < /dev/tty
  fi
  printf '%s=%s\n' "$key" "$val" >> "$ENV"
done < "$DESK/repo/.env.example"
ln -sf "$ENV" "$DESK/repo/worker/.env"   # the worker reads .env from its folder

echo "== e. Migrations"
uv run --no-dev alembic upgrade head
git -C "$DESK/repo" rev-parse HEAD > "$DESK/state/current"

echo "== f. Self-check"
uv run --no-dev python -m app.preflight || true
