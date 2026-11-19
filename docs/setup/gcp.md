# Worker VM on Google Cloud (free trial)

> **2026-11-19:** this Google account has no free-trial credit (paid account). The worker is trialled on a phone first (`phone.md`); this guide is the paid fallback.

Chosen 2026-11-18 after Oracle failed: there was no A1 capacity, and upgrading needed a large Pay-As-You-Go card hold (see `oracle-progress.md`).

The GCP free trial gives credit for a limited time; check the amount and the number of days on the sign-up page. On a free-trial account, Google does not charge the card when the trial ends. The VM stops instead, unless you upgrade. Plan the move to a paid VM or a home machine before the trial ends.

No code change is needed. The installer uses a folder on the boot disk when there is no separate data disk (press Enter at the disk prompt).

Do every step from your personal laptop.

## 1. Sign up
1. Go to `cloud.google.com/free`, click **Get started for free**, and sign in with your personal Google account.
2. Choose country India and account type **Individual**, accept the terms, and add the card.
3. **Read the screen before you confirm the card.** A small verification charge or hold that is refunded is normal. If it shows a large amount (thousands of rupees), stop and tell Claude.
4. Do **not** click "Activate full account" or upgrade. Stay on the free trial.

## 2. Project and Compute Engine
1. In the top bar, open the project picker and click **New project**. Name it `desk`, create it, then select it.
2. In the menu, go to **Compute Engine → VM instances** and click **Enable** for the API. This takes a minute or two.

## 3. Budget alert (safety net)
In the menu, go to **Billing → Budgets & alerts → Create budget**:
- Name: `desk`.
- Amount: ₹1,000 a month.
- Alerts: at 50%, 90% and 100%, by email to you.

The budget only sends emails; it does not stop anything.

## 4. Create the VM
Go to **VM instances → Create instance** and set:

| Field | Value |
|---|---|
| Name | `desk-worker` |
| Region / zone | `asia-south1 (Mumbai)`, any zone, e.g. `asia-south1-a` |
| Machine | General purpose → **E2** → `e2-medium` (2 vCPU, 4 GB) |
| Boot disk → Change | **Ubuntu**, **Ubuntu 24.04 LTS (x86/64)**, disk type **Balanced persistent disk**, size **120 GB** |
| Firewall | leave both the HTTP and HTTPS boxes **unticked** (nothing listens) |
| Networking | default network, external IP **Ephemeral** (the default) |

Click **Create**. The monthly estimate on the right is paid from the trial credit.

If Mumbai says "resources not available", try another zone in `asia-south1`, then `asia-south2` (Delhi).

## 5. Make the image public
Do this before step 7, so the VM can download the worker image.

On GitHub, go to your profile → **Packages** → `typing-test--worker` → Package settings → Change visibility → **Public**.

## 6. Connect
In the VM list, click **SSH** next to `desk-worker`. A terminal opens in the browser; allow the key transfer when asked. There are no key files to manage and no ports to open.

## 7. Install the worker
In the SSH window, run:
```bash
curl -fsSL https://raw.githubusercontent.com/jayy31/typing-test-/main/deploy/bootstrap.sh -o bootstrap.sh
sudo bash bootstrap.sh
```
- **Block device for /data:** press **Enter** to use the boot disk.
- It then asks for each setting. The values, and where to get them, are in `deploy.md` section 5. Secret values stay hidden while you type. An empty answer keeps the default.
- At the end it runs the preflight. Send Claude a screenshot of the result.

## 8. After it runs
- **Telegram:** health messages arrive within minutes, the quick check after 2 hours, and the first-run report after 24 hours.
- **Kotak:** never whitelist the VM's IP (see `docs/notes/kotak-api.md`).
- **NSE:** if NSE blocks requests from Google IPs, the 24-hour report says so. Then set `NSE_HTTP_PROXY`.
- **Stopping the VM:** VM instances → Stop pauses the CPU charge, but the disk still uses some credit.
