# Screenshot Integration Sync Ledger

Single source of truth for K.5 screenshot integration parity. A row is **Complete** only when `EN = done` **and** `FA ∈ {done, deferred:<ID>}`. README rows are English-only (`FA = n/a`) and are checked for existence/validity only, not EN↔FA parity.

Status values: `pending`, `done`, `deferred:<ID>`, `n/a`.
Change types: `swap` (placeholder→embed), `new-embed` (no placeholder existed), `multi-series` (1 placeholder → N images), `reuse` (re-reference an existing asset), `text+embed` (embed + new/edited prose), `caption-fix`.

## Guide & README integration rows

| ID | Area | Chapter/Section | Asset(s) | Change type | EN | FA | Caption | Notes |
|----|------|-----------------|----------|-------------|----|----|---------|-------|
| L01 | EN-guide / FA-guide | 03 §3.3 | raspberry-pi-imager-main-window.png | swap | done | done | done | Integrated Batch 1 (EN+FA) |
| L02 | EN-guide / FA-guide | 03 §3.4 | ubuntu-selection.png | swap | done | done | done | Integrated Batch 1 (EN+FA); shows 22.04 64-bit |
| L03 | EN-guide / FA-guide | 03 §3.5 | storage-selection.png | new-embed | done | done | done | Integrated Batch 1 (EN+FA); new embed, no prior placeholder |
| L04 | EN-guide / FA-guide | 03 §3.6 | advanced-settings-localisation.png, advanced-settings-user.png, advanced-settings-wifi.png, advanced-settings-ssh.png | multi-series + text+embed | done | done | done | Integrated Batch 1 (EN+FA); grouped 4-panel block + lead-in; supersedes `advanced-settings.png` |
| L05 | EN-guide / FA-guide | 03 §3.7 | write-complete.png | swap + caption-fix | done | done | done | Integrated Batch 1 (EN+FA); review-screen text added, "Write Successful" kept |
| L06 | EN-guide / FA-guide | 05 §5.9 | cloudflare-signup.png | swap | done | done | done | Integrated Batch 2 (EN+FA) |
| L07 | EN-guide / FA-guide | 05 §5.10 | cloudflare-domain-active.png | swap | done | done | done | Integrated Batch 2 (EN+FA); redacted (example.com) |
| L08 | EN-guide / FA-guide | 05 §5.12 | cloudflare-dns-records.png, cloudflare-add-dns-record.png | multi-series + text+embed | done | done | done | Integrated Batch 2 (EN+FA); DNS→Records / "subdomains are DNS records, not registrations" text added; supersedes `cloudflare-a-record.png` |
| L09 | EN-guide / FA-guide | 05 §5.13 | cloudflare-add-dns-record.png | reuse | done | done | done | Integrated Batch 2 (EN+FA); reuse with orange-cloud caption; supersedes `cloudflare-orange-cloud.png` |
| L10 | EN-guide / FA-guide | 05 §5.14 | cloudflare-token-list.png, cloudflare-token-create.png, cloudflare-token-permissions.png, cloudflare-token-summary.png | multi-series + text+embed | done | done | done | Integrated Batch 2 (EN+FA); 4-step token flow added; supersedes single placeholder |
| L11 | EN-guide / FA-guide | 07 §7.1 | login-page.png | swap | done | done | done | Integrated Batch 3 (EN+FA) |
| L12 | EN-guide / FA-guide | 07 §7.3 | dashboard-overview.png | swap | done | done | done | Integrated Batch 3; FA `dashboard-navigation` placeholder retired (→ dashboard-overview) |
| L13 | EN-guide / FA-guide | 08 §8.4 | advisor-card.png | swap | done | done | done | Integrated Batch 3; FA new embed (no prior placeholder) |
| L14 | EN-guide / FA-guide | 09 §9.2 | conduit-config.png | swap | done | done | done | Integrated Batch 3; FA new embed (no prior placeholder) |
| L15 | EN-guide / FA-guide | 12 §12.8 | backup-restore.png | swap | done | done | done | Integrated Batch 3; FA new embed (no prior placeholder) |
| R01 | README | Hero | dashboard-overview.png | reuse | done | n/a | done | Links to ch07 |
| R02 | README | Gallery | advisor-card.png | reuse | done | n/a | done | Links to ch08 |
| R03 | README | Gallery | conduit-config.png | reuse | done | n/a | done | Links to ch09 |
| R04 | README | Gallery | backup-restore.png | reuse | done | n/a | done | Links to ch12 |

> EN status above is "done" where the asset is already embedded in English (K.4D: L12–L15, R01–R04). L01–L11 EN are marked `done` only after Batch 1–3 embed them; until then treat their EN as the real chapter state (placeholders present). FA is `pending` for all guide rows until Batch 1–3.

## Deferrals

The **only** legal way for FA to lag EN. Empty at Batch 0.

| ID | Chapter | Asset | Reason | Tracking |
|----|---------|-------|--------|----------|
| — | — | — | — | — |

## Supersessions

| Superseded placeholder | Replaced by | Section |
|------------------------|-------------|---------|
| advanced-settings.png (single) | advanced-settings-{localisation,user,wifi,ssh}.png + storage-selection.png | 03 §3.5–§3.6 |
| cloudflare-a-record.png (single) | cloudflare-dns-records.png + cloudflare-add-dns-record.png | 05 §5.12 |
| cloudflare-orange-cloud.png (single) | cloudflare-add-dns-record.png (reused) | 05 §5.13 |
| cloudflare-token-permissions.png (single placeholder) | cloudflare-token-{list,create,permissions,summary}.png | 05 §5.14 |
| dashboard-navigation.png (fa placeholder) | dashboard-overview.png | 07 §7.3 |
