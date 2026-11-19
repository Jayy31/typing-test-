# Worker on an Android phone (Poco F1 trial)

Chosen on 2026-11-19 as a free trial host, instead of a paid VM. The Google Cloud account is already a paid account, so it has no free-trial credit.

The worker runs in Ubuntu inside Termux (proot-distro, no root needed). Android cannot run Docker, so the worker runs straight from a git checkout. The rest stays the same: Supabase, the Vercel dashboard, Telegram and Kotak.

Updates work like on a VM: every 5 minutes, a new commit on `main` is migrated and restarted. If no heartbeat arrives within 10 minutes, it rolls back and alerts you on Telegram.

If the phone keeps stopping the worker, or the battery worries you, move to a paid VM (`gcp.md`, about ₹2,000–2,500 a month). Nothing else changes.

## 0. Battery safety (do this first)
- Phone out of its case, on a hard, cool surface, away from anything flammable.
- Charge in cycles if you can: a smart plug or timer, e.g. 2 hours on, 2 hours off.
- Check every week or two that the back is not bulging. If it is, unplug it and stop using it.

## 1. Install three apps from F-Droid, not the Play Store
The Play Store version of Termux is old and broken.
1. Install F-Droid from `f-droid.org` (allow "install unknown apps" for your browser).
2. From F-Droid, install **Termux**, **Termux:Boot** and **Termux:API**.
3. Open **Termux:Boot** once. This lets it start at boot.

## 2. Stop MIUI killing Termux
In Settings, for each of the three apps:
- **Apps → Manage apps → Termux → Battery saver → No restrictions.**
- **Apps → Manage apps → Termux → Autostart → On.**
- Do the same for Termux:Boot.
- Open the recent apps screen, long-press Termux and tap the **lock** icon.
- **Settings → Wi-Fi → Additional settings:** keep Wi-Fi on during sleep (if your MIUI version shows this option).
- **Developer options** (optional): turn off **MIUI optimisation** only if Termux still gets killed after the steps above.

## 3. Install the worker
Open Termux and type (or paste from a message to yourself) these two lines:
```bash
curl -fsSL https://raw.githubusercontent.com/Jayy31/typing-test-/main/deploy/android/termux.sh -o termux.sh
bash termux.sh
```
- It takes 10–20 minutes: it installs Ubuntu, then Python and the libraries.
- It then asks for each setting. Values, and where to get them, are in `deploy.md` section 5. Secrets are hidden as you type. An empty answer keeps the default.
- At the end it runs the self-check (preflight). Send Claude a screenshot of the result.

## 4. Daily use
- **Logs:** `tail -f ~/desk.log` (the supervisor) and `proot-distro login ubuntu -- tail -f /opt/desk/worker.log` (the worker).
- **Stop:** `pkill -f deploy/android/run.sh`. **Start again:** `bash ~/.termux/boot/desk`.
- **Change a setting:** `proot-distro login ubuntu -- nano /opt/desk/.env`, then stop and start.
- **After a phone restart:** Termux:Boot starts the worker by itself, within a minute of unlocking.
- **If it stops:** you get a Telegram health alert, and healthchecks.io emails you. Open Termux and start it again.

## Not tested from the build sandbox
The scripts are syntax-checked only. Termux, proot-distro and MIUI cannot run in the sandbox. If a step fails, send Claude the screenshot and the last lines of the output.
