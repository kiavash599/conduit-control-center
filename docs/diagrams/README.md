# Diagrams

Diagram specifications and Mermaid sources for the CCC user guide, derived from [`../visual-assets-manifest.md`](../visual-assets-manifest.md) and the Persian chapters (source of truth). Generic teaching values only — no real domains, IPs, tokens, QR codes, or credentials.

> [!NOTE]
> **Rendering status: pending.** No Mermaid renderer (`mmdc` / mermaid-cli) is available in this environment, so `svg/` and `png/` are empty. Each `.mmd` renders on GitHub directly, and SVG/PNG can be generated later (target SVG filename = the manifest name in the last column, e.g. `svg/network-ip-address.svg`).

## Layout

- `specifications/` — one spec per diagram (`dgm-NN-*.md`).
- `source/` — Mermaid source per diagram (`dgm-NN-*.mmd`).
- `svg/`, `png/` — rendered output (pending).

## Index

| ID | Diagram | Spec | Source (.mmd) | Manifest asset | Render |
|----|---------|------|---------------|----------------|--------|
| DGM-01 | IP address on home network | [spec](specifications/dgm-01-network-ip-address.md) | [mmd](source/dgm-01-network-ip-address.mmd) | `network-ip-address.svg` | pending |
| DGM-02 | Public IP | [spec](specifications/dgm-02-network-public-ip.md) | [mmd](source/dgm-02-network-public-ip.mmd) | `network-public-ip.svg` | pending |
| DGM-03 | Private IP | [spec](specifications/dgm-03-network-private-ip.md) | [mmd](source/dgm-03-network-private-ip.mmd) | `network-private-ip.svg` | pending |
| DGM-04 | Public IP & Conduit reachability | [spec](specifications/dgm-04-network-public-ip-conduit.md) | [mmd](source/dgm-04-network-public-ip-conduit.mmd) | `network-public-ip-conduit.svg` | pending |
| DGM-05 | NAT | [spec](specifications/dgm-05-network-nat.md) | [mmd](source/dgm-05-network-nat.mmd) | `network-nat.svg` | pending |
| DGM-06 | DHCP | [spec](specifications/dgm-06-network-dhcp.md) | [mmd](source/dgm-06-network-dhcp.mmd) | `network-dhcp.svg` | pending |
| DGM-07 | DHCP reservation | [spec](specifications/dgm-07-network-dhcp-reservation.md) | [mmd](source/dgm-07-network-dhcp-reservation.mmd) | `network-dhcp-reservation.svg` | pending |
| DGM-08 | Port forwarding | [spec](specifications/dgm-08-network-port-forwarding.md) | [mmd](source/dgm-08-network-port-forwarding.mmd) | `network-port-forwarding.svg` | pending |
| DGM-09 | Domain name concept | [spec](specifications/dgm-09-domain-name-concept.md) | [mmd](source/dgm-09-domain-name-concept.mmd) | `domain-name-concept.svg` | pending |
| DGM-10 | DNS resolution | [spec](specifications/dgm-10-dns-resolution.md) | [mmd](source/dgm-10-dns-resolution.mmd) | `dns-resolution.svg` | pending |
| DGM-11 | Subdomain concept | [spec](specifications/dgm-11-subdomain-concept.md) | [mmd](source/dgm-11-subdomain-concept.mmd) | `subdomain-concept.svg` | pending |
| DGM-12 | Cloudflare DDNS flow | [spec](specifications/dgm-12-cloudflare-ddns-flow.md) | [mmd](source/dgm-12-cloudflare-ddns-flow.mmd) | `cloudflare-ddns-flow.svg` | pending |
| DGM-13 | Personal Mode states | [spec](specifications/dgm-13-personal-mode-states.md) | [mmd](source/dgm-13-personal-mode-states.mmd) | `personal-mode-states.svg` | pending |
| DGM-15 | Ryve claim flow | [spec](specifications/dgm-15-ryve-claim-flow.md) | [mmd](source/dgm-15-ryve-claim-flow.mmd) | `ryve-claim-flow.svg` | pending |
| DGM-17 | Restore and automatic rollback | [spec](specifications/dgm-17-restore-rollback-flow.md) | [mmd](source/dgm-17-restore-rollback-flow.mmd) | `restore-rollback-flow.svg` | pending |
| DGM-18 | Security trust boundaries | [spec](specifications/dgm-18-security-trust-boundaries.md) | [mmd](source/dgm-18-security-trust-boundaries.mmd) | `security-trust-boundaries.svg` | pending |

16 diagrams. DGM-01–12 are the networking batch (DGM-12 is reused across two sections). DGM-13/15/17/18 are the K.3B-1 high-priority core-feature batch; DGM-14 (personal-mode-pairing), DGM-16 (backup-create-flow), and DGM-19 (csrf-double-submit) are deferred to the next batch.
