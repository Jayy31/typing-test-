#!/usr/bin/env bash
# Pull-based redeploy with automatic rollback (run every 5 min by desk-update.timer).
# New image on :main -> alembic upgrade head -> restart -> wait for a heartbeat (10 min) ->
# keep it, or roll back to the previous image and send a Telegram alert.
# Rollback is safe because migrations are add-only (tests/test_migrations_safe.py).
set -euo pipefail
ENV_FILE=/opt/desk/.env
STATE=/opt/desk/state
IMAGE=$(grep -E '^DESK_IMAGE=' "$ENV_FILE" | cut -d= -f2-)
NAME=desk-worker
mkdir -p "$STATE"
exec 9>"$STATE/update.lock"; flock -n 9 || exit 0

run_worker() {  # $1 = image ref
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" --restart=always --env-file "$ENV_FILE" \
    -v /data/history:/data/history "$1" >/dev/null
}

telegram() {
  local token chat
  token=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2-)
  chat=$(grep -E '^TELEGRAM_CHAT_ID=' "$ENV_FILE" | cut -d= -f2-)
  [ -n "$token" ] && curl -s -m 15 -o /dev/null "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat}" --data-urlencode "text=$1" || true
}

docker pull -q "$IMAGE:main" >/dev/null
NEW=$(docker image inspect -f '{{index .RepoDigests 0}}' "$IMAGE:main")
CUR=$(cat "$STATE/current" 2>/dev/null || true)
[ "$NEW" = "$(cat "$STATE/failed" 2>/dev/null)" ] && exit 0  # do not retry a failed image
[ "$NEW" = "$CUR" ] && docker ps -q -f name="^${NAME}$" | grep -q . && exit 0

echo "deploying $NEW (was ${CUR:-none})"
docker run --rm --env-file "$ENV_FILE" "$NEW" uv run --no-dev alembic upgrade head
SINCE=$(date -Iseconds)
run_worker "$NEW"
for _ in $(seq 1 20); do  # 20 x 30 s = 10 min
  sleep 30
  if docker exec "$NAME" uv run --no-dev python -m app.preflight --heartbeat "$SINCE" >/dev/null 2>&1; then
    [ -n "$CUR" ] && echo "$CUR" > "$STATE/previous"
    echo "$NEW" > "$STATE/current"
    echo "deployed $NEW"
    exit 0
  fi
done

echo "no heartbeat within 10 min: rolling back"
if [ -n "$CUR" ]; then
  run_worker "$CUR"
  telegram "DEPLOY FAILED: new worker image gave no heartbeat in 10 min. Rolled back to the previous image. Check: docker logs $NAME"
else
  telegram "DEPLOY FAILED: first worker image gave no heartbeat in 10 min and there is no previous image. Check: docker logs $NAME"
fi
echo "$NEW" > "$STATE/failed"
exit 1
