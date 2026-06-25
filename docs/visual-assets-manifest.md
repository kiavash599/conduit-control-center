# Visual Assets Manifest

> [!NOTE]
> This manifest was originally **extracted** from the Persian user-guide Markdown (`docs/fa/user-guide/*.md`). All guide screenshots are now **captured** (`available`) under `docs/screenshots/` and integrated into both editions — **none are pending**. All 19 diagrams are **rendered** (`available`) under `docs/diagrams/svg/` and `docs/diagrams/png/`. Retired placeholder names are preserved as `superseded` rows for provenance. See [`screenshots/README.md`](screenshots/README.md) and [`screenshots/INTEGRATION-SYNC.md`](screenshots/INTEGRATION-SYNC.md).

## Summary

- Diagrams: **19 unique** (`cloudflare-ddns-flow.svg` referenced twice → 20 references). All `available`.
- Screenshots: **28 unique** (`cloudflare-add-dns-record.png` reused in §5.12 + §5.13; `cloudflare-domain-active.png` reused in `docs/tls-setup.md`). 21 in the bilingual guide + 7 TLS-onboarding shots in `docs/tls-setup.md` (English canonical). All `available`; **0 pending**.
- Superseded placeholder names (historical, never produced as files): **4** — `advanced-settings.png`, `cloudflare-a-record.png`, `cloudflare-orange-cloud.png`, `dashboard-navigation.png`.
- Educational images: **0**.
- Total unique assets: **47** (19 diagrams + 28 screenshots).
- Prose mention of "Screenshot" that is **not** an asset reference: 1 (ch11) — see Ambiguities.

Status values: `available` = asset produced on disk; `superseded` = retired placeholder name kept for history; `ambiguous` = traceability note only.

## Screenshots

| ID | Referenced file/name | Chapter | Section | Status |
|----|----------------------|---------|---------|--------|
| SCR-01 | `raspberry-pi-imager-main-window.png` | 03 | 3.3 What is Raspberry Pi Imager? | available |
| SCR-02 | `ubuntu-selection.png` | 03 | 3.4 Choosing the operating system | available |
| — | `storage-selection.png` | 03 | 3.5 Choosing the memory card | available |
| — | `advanced-settings-localisation.png` | 03 | 3.6 Advanced settings — Localisation | available |
| — | `advanced-settings-user.png` | 03 | 3.6 Advanced settings — User | available |
| — | `advanced-settings-wifi.png` | 03 | 3.6 Advanced settings — Wi-Fi | available |
| — | `advanced-settings-ssh.png` | 03 | 3.6 Advanced settings — Remote access (SSH) | available |
| SCR-04 | `write-complete.png` | 03 | 3.7 Writing the OS (pre-write summary) | available |
| SCR-05 | `cloudflare-signup.png` | 05 | 5.9 Creating a Cloudflare account | available |
| SCR-06 | `cloudflare-domain-active.png` | 05 | 5.10 Adding the domain (Active) | available |
| — | `cloudflare-dns-records.png` | 05 | 5.12 Creating the Conduit record (DNS → Records) | available |
| — | `cloudflare-add-dns-record.png` | 05 | 5.12 Add record / 5.13 Proxied (**reused**) | available |
| — | `cloudflare-token-list.png` | 05 | 5.14 API Token — token list | available |
| — | `cloudflare-token-create.png` | 05 | 5.14 API Token — create | available |
| SCR-09 | `cloudflare-token-permissions.png` | 05 | 5.14 API Token — permissions (1 of 4-image series) | available |
| — | `cloudflare-token-summary.png` | 05 | 5.14 API Token — summary | available |
| SCR-10 | `login-page.png` | 07 | 7.1 First login | available |
| SCR-11 | `dashboard-overview.png` | 07 | 7.3 Dashboard structure / 7.4 The Dashboard section | available |
| — | `advisor-card.png` | 08 | 8.4 Recommendation severity levels | available |
| — | `conduit-config.png` | 09 | 9.2 Configured and Effective | available |
| — | `backup-restore.png` | 12 | 12.8 Inspect Before Restore | available |
| — | `cloudflare-ssl-domain-overview.png` | tls-setup | Before You Begin (SSL/TLS mode) | available |
| — | `cloudflare-ssl-overview.png` | tls-setup | Before You Begin (SSL/TLS mode) | available |
| — | `cloudflare-ssl-full-strict.png` | tls-setup | Before You Begin (SSL/TLS mode) | available |
| — | `cloudflare-origin-server.png` | tls-setup | A.1 Create the certificate | available |
| — | `cloudflare-origin-create.png` | tls-setup | A.1 Create the certificate | available |
| — | `cloudflare-origin-configure.png` | tls-setup | A.1 Create the certificate | available |
| — | `cloudflare-origin-cert-key.png` | tls-setup | A.1 Create the certificate (key redacted) | available |

### Superseded screenshot placeholders (historical — never produced as files)

| ID | Retired placeholder | Replaced by | Section | Status |
|----|---------------------|-------------|---------|--------|
| SCR-03 | `advanced-settings.png` | `storage-selection.png` + `advanced-settings-{localisation,user,wifi,ssh}.png` | 03 §3.5–§3.6 | superseded |
| SCR-07 | `cloudflare-a-record.png` | `cloudflare-dns-records.png` + `cloudflare-add-dns-record.png` | 05 §5.12 | superseded |
| SCR-08 | `cloudflare-orange-cloud.png` | reuse of `cloudflare-add-dns-record.png` | 05 §5.13 | superseded |
| — | `dashboard-navigation.png` | `dashboard-overview.png` (SCR-11) | 07 §7.3 | superseded |

> SCR-03/07/08 retain their original IDs as historical anchors; they are **not** counted in the 21 available screenshots. No SCR-IDs were renumbered. New assets added since the original extraction are identified by filename (`—`).

## Diagrams

| ID | Referenced file/name | Chapter | Section | Status |
|----|----------------------|---------|---------|--------|
| DGM-01 | `network-ip-address.svg` | 04 | 4.1 What is an IP address? | available |
| DGM-02 | `network-public-ip.svg` | 04 | 4.2 What is a Public IP? | available |
| DGM-03 | `network-private-ip.svg` | 04 | 4.3 What is a Private IP? | available |
| DGM-04 | `network-public-ip-conduit.svg` | 04 | 4.3 Private IP (Conduit) | available |
| DGM-05 | `network-nat.svg` | 04 | 4.4 What is NAT? | available |
| DGM-06 | `network-dhcp.svg` | 04a | 4.5 What is DHCP? | available |
| DGM-07 | `network-dhcp-reservation.svg` | 04a | 4.7 What is DHCP Reservation? | available |
| DGM-08 | `network-port-forwarding.svg` | 04a | 4.9 What is Port Forwarding? | available |
| DGM-09 | `domain-name-concept.svg` | 05 | 5.1 What is a Domain? | available |
| DGM-10 | `dns-resolution.svg` | 05 | 5.2 What is DNS? | available |
| DGM-11 | `subdomain-concept.svg` | 05 | 5.3 What is a Subdomain? | available |
| DGM-12 | `cloudflare-ddns-flow.svg` | 05 | 5.5 & 5.16 Dynamic DNS (**reused**) | available |
| DGM-13 | `personal-mode-states.svg` | 10 | Personal Mode states | available |
| DGM-14 | `personal-mode-pairing.svg` | 10 | Personal Mode pairing | available |
| DGM-15 | `ryve-claim-flow.svg` | 11 | Ryve claim flow | available |
| DGM-16 | `backup-create-flow.svg` | 12 | Backup creation flow | available |
| DGM-17 | `restore-rollback-flow.svg` | 12 | Restore / rollback flow | available |
| DGM-18 | `security-trust-boundaries.svg` | 14 | Security trust boundaries | available |
| DGM-19 | `csrf-double-submit.svg` | 14 | CSRF double-submit | available |

> DGM-01–12 are integrated in **both** editions (EN + FA). DGM-13–19 are produced and integrated in the **English** edition (ch 10/11/12/14); their Persian integration is tracked separately — see Remaining issues.

### Non-asset prose mention

| ID | Note | Chapter | Status |
|----|------|---------|--------|
| ALT-01 | Prose only — `از Screenshotهای غیرضروری خودداری کنید.` ("avoid unnecessary screenshots"); no filename/placeholder | 11 §(prose) | ambiguous — not an asset |

## Per-chapter coverage

| Chapter | Visual references |
|---|---|
| 01-introduction | none |
| 02-hardware-requirements | none |
| 03-installing-ubuntu | SCR-01, SCR-02, storage-selection, advanced-settings-{localisation,user,wifi,ssh}, SCR-04 (8) |
| 04-networking-fundamentals | DGM-01 … DGM-05 |
| 04a-understanding-home-internet-equipment | DGM-06 … DGM-08 |
| 05-domains-dns-and-cloudflare | DGM-09 … DGM-12, SCR-05, SCR-06, cloudflare-dns-records, cloudflare-add-dns-record, cloudflare-token-{list,create,permissions,summary} (8 screenshots) |
| 06-installing-ccc | none |
| 07-first-login-and-dashboard-tour | SCR-10 (login-page), SCR-11 (dashboard-overview) |
| 08-contribution-advisor | advisor-card |
| 09-conduit-configuration | conduit-config |
| 10-personal-mode | DGM-13, DGM-14 |
| 11-ryve-integration | DGM-15 (ALT-01 = prose mention only; not an asset) |
| 12-backup-and-restore | DGM-16, DGM-17, backup-restore |
| 13-system-maintenance-and-troubleshooting | none |
| 14-security-model | DGM-18, DGM-19 |
| 15-advanced-administration | none |
| 16-faq | none |
| docs/tls-setup.md (Path A, EN canonical) | `cloudflare-domain-active` (reused) + `cloudflare-ssl-domain-overview`, `cloudflare-ssl-overview`, `cloudflare-ssl-full-strict`, `cloudflare-origin-server`, `cloudflare-origin-create`, `cloudflare-origin-configure`, `cloudflare-origin-cert-key` (7 new) |

## Ambiguities (Challenge → Evidence → Recommendation)

### DGM-12 — same diagram referenced twice
- **Challenge:** Is `cloudflare-ddns-flow.svg` one asset or two?
- **Evidence:** Referenced in both §5.5 and §5.16 of `05-domains-dns-and-cloudflare.md`, identical filename.
- **Recommendation:** **One** asset (DGM-12) reused in both sections. No rename. Status `available`.

### ALT-01 — prose mention, not an asset
- **Challenge:** A line of `11-ryve-integration.md` contains the word "Screenshot".
- **Evidence:** The full line is `از Screenshotهای غیرضروری خودداری کنید.` ("avoid unnecessary screenshots") — editorial guidance, no filename or placeholder.
- **Recommendation:** **Not** an asset; excluded from counts. Recorded for traceability only.

### Naming — `storage-selection.png`
- **Challenge:** Does any captured asset differ from its original placeholder name?
- **Evidence:** The §3.5 memory-card screenshot was produced as `storage-selection.png` (the working name `advanced-settings-storage.png` was **renamed** to the canonical `storage-selection.png`).
- **Recommendation:** Canonical name = `storage-selection.png`. Recorded here for history; no further rename.

### Naming — general
- All filenames are clean, consistent kebab-case. Multi-panel/series assets use descriptive suffixes (`advanced-settings-<panel>`, `cloudflare-token-<step>`). No further `recommended-rename`.

## Remaining issues

- **FA feature-diagram parity:** DGM-13–19 are integrated in the English edition only. Persian integration of these 7 diagrams is **out of scope** for the K.5 screenshot program and is tracked as a separate future task.
