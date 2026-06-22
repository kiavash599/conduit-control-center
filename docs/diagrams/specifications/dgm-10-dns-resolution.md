# DGM-10 — DNS resolution

## Source
- Referenced filename: `dns-resolution.svg`
- Source chapter: `docs/fa/user-guide/05-domains-dns-and-cloudflare.md`
- Source section/context: §5.2 «DNS چیست؟» — "what is the IP of example.com? DNS answers 203.0.113.10, then the browser connects to that IP".

## Purpose
Teach that DNS translates a domain name into an IP address, which the browser then connects to.

## Audience
Beginner operator / non-network specialist.

## Required elements
- Nodes: `Browser`, `DNS`, `Web server 203.0.113.10`.
- Flow: Browser —(What is the IP of example.com?)→ DNS —(203.0.113.10)→ Browser —(connects to)→ Web server.

## Visual direction
Query → answer → connect, left→right. Keep the three steps clear and uncluttered.

## Text labels
English, short.

## Security/privacy constraints
Generic example domain (`example.com`) and documentation IP (`203.0.113.10`) only. Do NOT include the real resolver/host IPs that appear illustratively in the chapter text. No tokens, QR codes, usernames, or credentials.

## Validation checklist
- [x] Matches chapter context (§5.2)
- [x] Uses generic examples (example.com, 203.0.113.10)
- [x] No secret or personal data (real example IPs from prose intentionally excluded)
- [x] File name matches manifest (`dns-resolution.svg`)
