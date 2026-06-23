# Visual Assets Manifest

> [!NOTE]
> This manifest was originally **extracted** from the Persian user-guide Markdown (`docs/fa/user-guide/*.md`). The core dashboard/feature screenshots are now **captured** (Status `available`) under `docs/screenshots/`; the remaining screenshot entries are still **placeholders to be produced later**.

## Summary

- Diagrams: **12 unique** (`cloudflare-ddns-flow.svg` is referenced twice → 13 references).
- Screenshots: **14** (4 captured under `docs/screenshots/`, 10 pending).
- Educational images: **0** (none found).
- Total references: **24** (23 unique assets).
- Prose mention of "Screenshot" that is **not** an asset reference: 1 (ch11) — see Ambiguities.

All asset filenames are recorded **exactly** as written in the source. Status `pending` = referenced in docs, asset not yet produced.

## Asset table

| ID | Type | Referenced file/name | Source chapter | Context (section) | Status |
|----|------|----------------------|----------------|-------------------|--------|
| SCR-01 | Screenshot | `raspberry-pi-imager-main-window.png` | 03-installing-ubuntu.md (L91) | 3.3 Raspberry Pi Imager چیست؟ | pending |
| SCR-02 | Screenshot | `ubuntu-selection.png` | 03-installing-ubuntu.md (L143) | 3.4 انتخاب سیستم عامل | pending |
| SCR-03 | Screenshot | `advanced-settings.png` | 03-installing-ubuntu.md (L264) | 3.6 تنظیمات پیشرفته Raspberry Pi Imager | pending |
| SCR-04 | Screenshot | `write-complete.png` | 03-installing-ubuntu.md (L288) | 3.7 نوشتن سیستم عامل روی کارت حافظه | pending |
| DGM-01 | Diagram | `network-ip-address.svg` | 04-networking-fundamentals.md (L76) | 4.1 آدرس IP چیست؟ | pending |
| DGM-02 | Diagram | `network-public-ip.svg` | 04-networking-fundamentals.md (L122) | 4.2 Public IP چیست؟ | pending |
| DGM-03 | Diagram | `network-private-ip.svg` | 04-networking-fundamentals.md (L199) | 4.3 Private IP چیست؟ | pending |
| DGM-04 | Diagram | `network-public-ip-conduit.svg` | 04-networking-fundamentals.md (L263) | 4.3 Private IP چیست؟ (Conduit) | pending |
| DGM-05 | Diagram | `network-nat.svg` | 04-networking-fundamentals.md (L341) | 4.4 NAT چیست؟ | pending |
| DGM-06 | Diagram | `network-dhcp.svg` | 04a-understanding-home-internet-equipment.md (L91) | 4.5 DHCP چیست؟ | pending |
| DGM-07 | Diagram | `network-dhcp-reservation.svg` | 04a-understanding-home-internet-equipment.md (L197) | 4.7 DHCP Reservation چیست؟ | pending |
| DGM-08 | Diagram | `network-port-forwarding.svg` | 04a-understanding-home-internet-equipment.md (L323) | 4.9 Port Forwarding چیست؟ | pending |
| DGM-09 | Diagram | `domain-name-concept.svg` | 05-domains-dns-and-cloudflare.md (L95) | 5.1 Domain چیست؟ | pending |
| DGM-10 | Diagram | `dns-resolution.svg` | 05-domains-dns-and-cloudflare.md (L145) | 5.2 DNS چیست؟ | pending |
| DGM-11 | Diagram | `subdomain-concept.svg` | 05-domains-dns-and-cloudflare.md (L215) | 5.3 Subdomain چیست؟ | pending |
| DGM-12 | Diagram | `cloudflare-ddns-flow.svg` | 05-domains-dns-and-cloudflare.md (L306 **and** L800) | 5.5 Dynamic DNS چیست؟ / 5.16 Dynamic DNS چگونه کار می‌کند؟ | ambiguous |
| SCR-05 | Screenshot | `cloudflare-signup.png` | 05-domains-dns-and-cloudflare.md (L495) | 5.9 ایجاد حساب Cloudflare | pending |
| SCR-06 | Screenshot | `cloudflare-domain-active.png` | 05-domains-dns-and-cloudflare.md (L537) | 5.10 افزودن دامنه به Cloudflare | pending |
| SCR-07 | Screenshot | `cloudflare-a-record.png` | 05-domains-dns-and-cloudflare.md (L611) | 5.12 ایجاد رکورد Conduit | pending |
| SCR-08 | Screenshot | `cloudflare-orange-cloud.png` | 05-domains-dns-and-cloudflare.md (L661) | 5.13 چرا رکورد باید Proxied باشد؟ | pending |
| SCR-09 | Screenshot | `cloudflare-token-permissions.png` | 05-domains-dns-and-cloudflare.md (L732) | 5.14 ساخت API Token | pending |
| SCR-10 | Screenshot | `login-page.png` | 07-first-login-and-dashboard-tour.md (L65) | 7.1 اولین ورود | pending |
| SCR-11 | Screenshot | `dashboard-overview.png` | 07-first-login-and-dashboard-tour.md | 7.3 Dashboard structure / 7.4 The Dashboard section | available |
| — | Screenshot | `advisor-card.png` | 08-contribution-advisor.md | 8.4 Recommendation severity levels | available |
| — | Screenshot | `conduit-config.png` | 09-conduit-configuration.md | 9.2 Configured and Effective | available |
| — | Screenshot | `backup-restore.png` | 12-backup-and-restore.md | 12.8 Inspect Before Restore | available |
| ALT-01 | Unknown visual placeholder | _no filename — prose mention_ | 11-ryve-integration.md (L321) | "از Screenshotهای غیرضروری خودداری کنید." | ambiguous |

> [!NOTE]
> SCR-11 was previously the placeholder `dashboard-navigation.png`; it is now covered by the canonical `dashboard-overview.png` (no file rename — the placeholder was never produced). The three rows marked `—` are core-UI screenshots identified by filename rather than a new SCR-ID.

## Per-chapter coverage

| Chapter | Visual references |
|---|---|
| 01-introduction | none |
| 02-hardware-requirements | none |
| 03-installing-ubuntu | SCR-01 … SCR-04 |
| 04-networking-fundamentals | DGM-01 … DGM-05 |
| 04a-understanding-home-internet-equipment | DGM-06 … DGM-08 |
| 05-domains-dns-and-cloudflare | DGM-09 … DGM-12, SCR-05 … SCR-09 |
| 06-installing-ccc | none |
| 07-first-login-and-dashboard-tour | SCR-10, SCR-11 |
| 08-contribution-advisor | none |
| 09-conduit-configuration | none |
| 10-personal-mode | none |
| 11-ryve-integration | ALT-01 (prose mention only; not an asset) |
| 12-backup-and-restore | none |
| 13-system-maintenance-and-troubleshooting | none |
| 14-security-model | none |
| 15-advanced-administration | none |
| 16-faq | none |

## Ambiguities (Challenge → Evidence → Recommendation)

### DGM-12 — same diagram referenced twice
- **Challenge:** Is `cloudflare-ddns-flow.svg` one asset or two?
- **Evidence:** It appears under `دیاگرام مورد نیاز` in both §5.5 (L306) and §5.16 (L800) of `05-domains-dns-and-cloudflare.md`, with the identical filename.
- **Recommendation:** Treat as **one** asset (DGM-12) reused in both sections. No rename. Status `ambiguous` only to flag the dual placement; produce once.

### ALT-01 — prose mention, not an asset
- **Challenge:** Line 321 of `11-ryve-integration.md` contains the word "Screenshot".
- **Evidence:** The full line is `از Screenshotهای غیرضروری خودداری کنید.` ("avoid unnecessary screenshots") — editorial guidance, with no filename or `تصویر/دیاگرام مورد نیاز` placeholder.
- **Recommendation:** **Not** an asset; excluded from the asset counts. Recorded as `ambiguous`/`Unknown visual placeholder` for traceability only.

### Label nuance — `تصویر مورد نیاز` (“image needed”) vs Screenshot
- **Challenge:** PNG placeholders in ch03/05/07 are labelled `تصویر مورد نیاز` (literally "image needed"), not strictly "screenshot".
- **Evidence:** Their filenames/subjects are UI captures (`raspberry-pi-imager-main-window.png`, `cloudflare-a-record.png`, `login-page.png`, …).
- **Recommendation:** Classified as **Screenshot** based on subject. No `Educational image` items were found.

### Naming
- All filenames are clean, consistent kebab-case; **no `recommended-rename`** is proposed.
