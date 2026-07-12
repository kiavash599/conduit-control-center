# Software Updates & Signed Releases

*Applies to CCC v0.3.14 and later.* This guide explains how the dashboard **One-Click Update** works, how CCC verifies that an update is authentic (**Signed Releases**), and what the **audit trail** records. It complements Chapter 13 (System Maintenance) and Chapter 14 (Security Model).

> Screenshots in this guide are marked `SCREENSHOT NEEDED`. The written steps are complete and usable without them.

## 1. One-Click Update (dashboard)

CCC updates itself from the dashboard with a single action. On the **Software Updates** page:

1. CCC checks the latest published release and shows whether you are up to date.
2. If an update is available, select **Install Update**. (If you are already current, the button is hidden.)
3. The update runs in the background; the page shows live progress and, on completion, a success state. If the update cannot reach a healthy state, CCC **automatically rolls back** to the previous version and reports it.

<!-- SCREENSHOT NEEDED: SU-01 — Software Updates page, "up to date" state -->
> **Screenshot placeholder - Software Updates page (up to date).** This image will be captured after the next release is produced and One-Click Update is validated.

<!-- SCREENSHOT NEEDED: SU-02 — Software Updates page, update available with Install Update button -->
> **Screenshot placeholder - Software Updates page (update available).** This image will be captured after the next release is produced and One-Click Update is validated.

<!-- SCREENSHOT NEEDED: SU-03 — Update in progress -->
> **Screenshot placeholder - update in progress.** This image will be captured after the next release is produced and One-Click Update is validated.

<!-- SCREENSHOT NEEDED: SU-04 — Update success state -->
> **Screenshot placeholder - update complete.** This image will be captured after the next release is produced and One-Click Update is validated.

Manual `update.sh` over SSH is retained for initial install, disaster recovery, and emergency maintenance; One-Click Update is the standard mechanism for day-to-day upgrades.

## 2. Signed Releases (why an update is trustworthy)

Every CCC release is a **signed, content-fixed artifact**. Before CCC applies an update, the device verifies it **fail-closed** — if verification fails, **nothing privileged runs**:

- The publisher builds a **deterministic** release archive whose SHA-256 **digest** is its identity, and signs a **manifest** (product, version, digest, compatibility) with the project's Ed25519 signing key.
- Your device holds a small **trust store** (`/opt/conduit-cc/trust/allowed_signers`) — the list of keys it will trust. This anchor is provisioned during setup and is **never** delivered inside an update.
- On update, CCC verifies the manifest signature against that trust store **before extracting or applying anything**, and confirms the artifact's digest matches the manifest. A bad signature, wrong product, or digest mismatch aborts the update safely.

This is the protection described in the Security Model (Chapter 14): a compromised download channel cannot make your device install or run an unauthentic release. The design of record is **ADR-0003 — Signed Release Artifacts and On-Device Verification**.

## 3. What the audit trail records

CCC keeps an append-only **update audit log** at `/var/log/conduit-cc-audit/update-audit.jsonl` (readable by the service, written by the privileged updater). Each update attempt records outcomes such as `accepted` (verification passed) and `applied` (deployed successfully), or `reverted` if a rollback occurred. The log **never** contains keys or signatures, and it is **observational only** — it records what happened but never changes whether an update is allowed.

To view recent audit entries over SSH:

```
sudo tail -n 20 /var/log/conduit-cc-audit/update-audit.jsonl
```

## 4. If an update rolls back

An automatic rollback means CCC deployed the new version but it did not become healthy in time, so CCC restored the previous version. The dashboard shows a persistent, dismissible message; your node keeps running the previous version. See Chapter 13 (System Maintenance & Troubleshooting) for diagnosis (worker log and status file), and retry once the cause is resolved.

<!-- SCREENSHOT NEEDED: SU-05 — Rolled-back state message on Software Updates page -->
> **Screenshot placeholder - update rolled back.** This image will be captured after the next release is produced and One-Click Update is validated.
