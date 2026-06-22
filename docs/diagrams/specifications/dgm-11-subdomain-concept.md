# DGM-11 — Subdomain concept

## Source
- Referenced filename: `subdomain-concept.svg`
- Source chapter: `docs/fa/user-guide/05-domains-dns-and-cloudflare.md`
- Source section/context: §5.3 «Subdomain چیست؟» — analogy: if a domain is a building, subdomains are its units: mail.example.com, support.example.com, conduit.example.com.

## Purpose
Teach that subdomains are named units under a single parent domain.

## Audience
Beginner operator / non-network specialist.

## Required elements
- Parent node: `example.com`.
- Child nodes: `mail.example.com`, `support.example.com`, `conduit.example.com`.
- Arrows: parent → each subdomain.

## Visual direction
Top-down tree, one parent to three children. Minimal.

## Text labels
English, short.

## Security/privacy constraints
Generic example domain/subdomains only. No real domains, IPs, tokens, QR codes, usernames, or credentials.

## Validation checklist
- [x] Matches chapter context (§5.3)
- [x] Uses generic examples (example.com and subdomains)
- [x] No secret or personal data
- [x] File name matches manifest (`subdomain-concept.svg`)
