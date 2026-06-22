# DGM-12 — Cloudflare DDNS flow

## Source
- Referenced filename: `cloudflare-ddns-flow.svg`
- Source chapter: `docs/fa/user-guide/05-domains-dns-and-cloudflare.md`
- Source section/context: §5.5 «Dynamic DNS چیست؟» **and** §5.16 «Dynamic DNS چگونه کار می‌کند؟» — "CCC periodically checks the public IP; if it changed, it updates Cloudflare so conduit.example.com always points to the right IP; result is logged as `updated`."
- Note: this single asset is referenced in two sections (see manifest ambiguity note). Produced once, reused in both.

## Purpose
Teach the dynamic-DNS loop: CCC keeps the Cloudflare DNS record pointed at the home's current public IP.

## Audience
Beginner operator / non-network specialist.

## Required elements
- Start: `CCC checks the public IP periodically`.
- Decision: `Public IP changed?`.
- Branch No → `No change`.
- Branch Yes → `Update Cloudflare DNS record` → `conduit.example.com points to the current IP`.

## Visual direction
Top-down flowchart with one decision diamond and two branches. This is the one diagram where a decision node (not a plain flow) is the better Mermaid shape.

## Text labels
English, short.

## Security/privacy constraints
Generic example hostname (`conduit.example.com`) only; no real public IP value shown. No tokens, QR codes, usernames, or credentials.

## Validation checklist
- [x] Matches chapter context (§5.5 / §5.16)
- [x] Uses generic examples (conduit.example.com)
- [x] No secret or personal data
- [x] File name matches manifest (`cloudflare-ddns-flow.svg`)
