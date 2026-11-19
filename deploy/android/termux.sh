#!/data/data/com.termux/files/usr/bin/bash
# Phone (Android + Termux) setup, part 1 of 2. Run inside the Termux app:
#   curl -fsSL https://raw.githubusercontent.com/Jayy31/typing-test-/main/deploy/android/termux.sh -o termux.sh
#   bash termux.sh
# Installs Ubuntu (proot-distro, no root needed), runs the worker installer inside it, and adds a
# Termux:Boot script so the worker starts again after the phone restarts. Safe to re-run.
set -euo pipefail
REPO=${1:-Jayy31/typing-test-}
RAW="https://raw.githubusercontent.com/${REPO}/main/deploy/android"

echo "== 1. Termux packages"
pkg update -y && pkg upgrade -y
pkg install -y proot-distro curl termux-api

echo "== 2. Ubuntu"
proot-distro list 2>/dev/null | grep -q "ubuntu.*installed" || proot-distro install ubuntu || true

echo "== 3. Worker install inside Ubuntu"
# --shared-tmp: Termux's $TMPDIR is /tmp inside Ubuntu
curl -fsSL "$RAW/install.sh" -o "$TMPDIR/desk-install.sh"
proot-distro login ubuntu --shared-tmp -- bash /tmp/desk-install.sh "$REPO"

echo "== 4. Start at boot (needs the Termux:Boot app, opened once)"
mkdir -p "$HOME/.termux/boot"
cat > "$HOME/.termux/boot/desk" <<'BOOT'
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
pgrep -f "deploy/android/run.sh" >/dev/null && exit 0
nohup proot-distro login ubuntu --shared-tmp -- bash /opt/desk/repo/deploy/android/run.sh \
  >> "$HOME/desk.log" 2>&1 &
BOOT
chmod +x "$HOME/.termux/boot/desk"

echo "== 5. Start now"
bash "$HOME/.termux/boot/desk"
sleep 5
echo "Worker started. Logs: tail -f ~/desk.log   Stop: pkill -f deploy/android/run.sh"
echo "Quick check arrives on Telegram 2 h after this first start, full report after 24 h."
