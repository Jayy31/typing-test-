# Oracle VM setup: progress (paused 2026-11-17)

Owner is doing the Oracle setup from the personal laptop (not the company PC). Resume here.

## Done
- Oracle Cloud account created and fully provisioned (email received).
  - Tenancy (cloud account name): `jayrathood9155`; home region: **India West (Mumbai)**, ap-mumbai-1.
  - Customer type: Individual. Sign-in at cloud.oracle.com -> tenancy -> email/password.
- VM create form filled and reviewed (kept open in a tab):
  - Name `desk-worker`, compartment root, AD-1, on-demand capacity.
  - Image Canonical Ubuntu 24.04; shape VM.Standard.A1.Flex 2 OCPU / 12 GB (Always Free).
  - Security: Shielded / Confidential computing off.
  - Networking: create new VCN `vcn-20261117-2220` + new public subnet `10.0.0.0/24`.
    The public IPv4 toggle could not be enabled (console quirk), so the VM has no public IP yet.
  - SSH: "Generate a key pair for me"; **private and public key downloaded** (owner confirmed).
  - Boot volume 50 GB, 10 VPU, in-transit encryption on; no block volume yet.

## Blocked / decisions
- `Create` failed: **"Out of capacity for shape VM.Standard.A1.Flex in availability domain AD-1"**.
  Mumbai has a single AD, so the only fix is retrying.
- Pay-As-You-Go upgrade **NOT done, by owner decision**: adding a Mastercard triggered a
  verification hold of 138.19 SGD (~Rs 9-10k); the card declined. Earlier guidance ("small hold,
  about Rs 100") was wrong for the upgrade. Do not retry the card; stay on Free Tier.
  - Free Tier risk: Oracle may reclaim a VM that looks idle for 7 days. The worker is busy, but this is
    not guaranteed. Data on the separate 100 GB disk would survive; re-run the installer.

## Next steps (in order)
1. Retry `Create`, preferably early morning (6-9 AM IST) or late night. If the old form is closed:
   Compute -> Instances -> Create instance and redo the settings above. On Networking choose
   "Select existing virtual cloud network" if `vcn-20261117-2220` now exists (a failed create may
   or may not have created it).
   - Better chance: Change shape -> A1.Flex **1 OCPU / 6 GB** (resize to 2/12 later when stopped).
2. When RUNNING: add a public IP. Instance -> Networking/Attached VNICs -> primary VNIC ->
   IPv4 addresses -> Edit -> "Ephemeral public IP". Note the IP.
3. Create the 100 GB block volume `desk-data` (same AD) and attach it (Paravirtualized, Read/Write).
4. Security list of the subnet: keep ingress TCP 22 only (0.0.0.0/0 OK, key-only login), egress all.
5. Budget alert: Billing & Cost Management -> Budgets -> Rs 1 with email alert.
6. SSH from the laptop (`ssh -i <key> ubuntu@<IP>`), then run the installer
   (`deploy/bootstrap.sh Jayy31/typing-test-`, steps in `docs/setup/deploy.md` and `setup-guide.pdf`).
7. GitHub package `typing-test--worker` must be Public before the installer (owner to check).
