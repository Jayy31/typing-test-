#!/usr/bin/env bash
# One-time (and re-runnable) VM setup. Safe to run again: every step checks first.
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/deploy/bootstrap.sh -o bootstrap.sh
#   sudo bash bootstrap.sh <owner>/<repo>
# Installs Docker, mounts the block volume at /data (asks before formatting), writes
# /opt/desk/.env (secrets typed hidden), runs migrations, starts the worker (restart=always),
# installs the 5-minute update timer, then runs the preflight self-check.
set -euo pipefail
REPO=${1:?usage: sudo bash bootstrap.sh <github-owner>/<repo>}
REPO_LC=$(echo "$REPO" | tr '[:upper:]' '[:lower:]')
IMAGE="ghcr.io/${REPO_LC}-worker"
RAW="https://raw.githubusercontent.com/${REPO}/main"
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
mkdir -p /opt/desk/state

echo "== 1. Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

echo "== 2. Persistent volume at /data"
if mountpoint -q /data; then
  echo "/data already mounted"
else
  lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT
  read -rp "Block device for /data (e.g. sdb; empty = use the boot disk): " DEV
  if [ -n "$DEV" ]; then
    DEV=/dev/${DEV#/dev/}
    if [ -z "$(blkid -o value -s TYPE "$DEV" || true)" ]; then
      read -rp "$DEV has no filesystem. Format it as ext4 (ERASES it)? type YES: " OK
      [ "$OK" = YES ] || { echo "not formatted; stopping"; exit 1; }
      mkfs.ext4 -L deskdata "$DEV"
    fi
    mkdir -p /data
    UUID=$(blkid -o value -s UUID "$DEV")
    grep -q "$UUID" /etc/fstab || echo "UUID=$UUID /data ext4 defaults,nofail 0 2" >> /etc/fstab
    mount /data
  fi
fi
mkdir -p /data/history && chown 1000:1000 /data/history

echo "== 3. Settings (/opt/desk/.env)"
ENV=/opt/desk/.env
touch "$ENV" && chmod 600 "$ENV"
curl -fsSL "$RAW/.env.example" -o /opt/desk/env.example
grep -q '^DESK_IMAGE=' "$ENV" || echo "DESK_IMAGE=$IMAGE" >> "$ENV"
grep -q '^HISTORY_DATA_DIR=' "$ENV" || echo "HISTORY_DATA_DIR=/data/history" >> "$ENV"
grep -q '^DUCKDB_PATH=' "$ENV" || echo "DUCKDB_PATH=/data/history/history.duckdb" >> "$ENV"
SECRET_RE='KEY|TOKEN|SECRET|PASSWORD|DATABASE_URL|PROXY|PING_URL'
while IFS= read -r line; do
  key=${line%%=*}
  [[ "$line" =~ ^[A-Z0-9_]+= ]] || continue
  grep -q "^${key}=" "$ENV" && continue          # already set: keep (re-run safe)
  if [[ "$key" =~ $SECRET_RE ]]; then
    read -rsp "$key (hidden, empty = default): " val; echo
  else
    read -rp "$key (empty = default): " val
  fi
  printf '%s=%s\n' "$key" "$val" >> "$ENV"
done < /opt/desk/env.example

echo "== 4. Image, migrations, worker"
curl -fsSL "$RAW/deploy/update.sh" -o /opt/desk/update.sh && chmod +x /opt/desk/update.sh
if ! docker pull -q "$IMAGE:main"; then
  echo "cannot pull $IMAGE:main. If the package is private: docker login ghcr.io (token with read:packages)"
  exit 1
fi
/opt/desk/update.sh || true   # migrate + start + wait for heartbeat (first deploy)

echo "== 5. Update timer"
curl -fsSL "$RAW/deploy/desk-update.service" -o /etc/systemd/system/desk-update.service
curl -fsSL "$RAW/deploy/desk-update.timer" -o /etc/systemd/system/desk-update.timer
systemctl daemon-reload && systemctl enable --now desk-update.timer

echo "== 6. Self-check"
docker run --rm --env-file "$ENV" "$IMAGE:main" uv run --no-dev python -m app.preflight || true
docker ps -f name=desk-worker
echo "Done. Quick check arrives on Telegram 2 h after this first start, full report after 24 h."
