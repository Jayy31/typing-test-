#!/usr/bin/env bash
# Phone supervisor (inside Ubuntu, started by ~/.termux/boot/desk): keeps the worker running and
# every 5 minutes deploys a new commit on main the same way deploy/update.sh does on a VM:
# migrate -> restart -> heartbeat within 10 min -> keep, else roll back and alert on Telegram.
set -uo pipefail
DESK=/opt/desk
REPO=$DESK/repo
STATE=$DESK/state
export PATH="$HOME/.local/bin:$PATH" TZ=Asia/Kolkata
cd "$REPO/worker"
PID=""

telegram() {
  local token chat
  token=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$DESK/.env" | cut -d= -f2-)
  chat=$(grep -E '^TELEGRAM_CHAT_ID=' "$DESK/.env" | cut -d= -f2-)
  [ -n "$token" ] && curl -s -m 15 -o /dev/null "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat}" --data-urlencode "text=$1" || true
}

start() { uv run --no-dev python -m app.scheduler >> "$DESK/worker.log" 2>&1 & PID=$!; }
stop() { [ -n "$PID" ] && kill "$PID" 2>/dev/null && wait "$PID" 2>/dev/null; PID=""; }
use() { git -C "$REPO" checkout -q --detach "$1" && uv sync -q --frozen --no-dev; }

deploy() {
  git -C "$REPO" fetch -q origin main || return 0
  local new cur
  new=$(git -C "$REPO" rev-parse origin/main)
  cur=$(cat "$STATE/current" 2>/dev/null || true)
  [ "$new" = "$cur" ] && return 0
  [ "$new" = "$(cat "$STATE/failed" 2>/dev/null)" ] && return 0
  echo "$(date -Is) deploying $new (was ${cur:-none})"
  stop
  if use "$new" && uv run --no-dev alembic upgrade head; then
    local since; since=$(date -Iseconds); start
    for _ in $(seq 1 20); do
      sleep 30
      if uv run --no-dev python -m app.preflight --heartbeat "$since" >/dev/null 2>&1; then
        echo "$new" > "$STATE/current"; echo "$(date -Is) deployed $new"; return 0
      fi
    done
  fi
  echo "$(date -Is) no heartbeat in 10 min: rolling back"
  echo "$new" > "$STATE/failed"
  stop
  [ -n "$cur" ] && use "$cur"
  start
  telegram "DEPLOY FAILED on the phone: new commit gave no heartbeat in 10 min. Rolled back. Check ~/desk.log and /opt/desk/worker.log"
}

trap 'stop; exit 0' TERM INT
start
while true; do
  for _ in $(seq 1 10); do  # 10 x 30 s = 5 min; restart the worker if it died
    sleep 30
    if ! kill -0 "$PID" 2>/dev/null; then
      echo "$(date -Is) worker stopped: restarting"; start
    fi
  done
  deploy
done
