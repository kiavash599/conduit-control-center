# DGM-03 — Private IP

## Source
- Referenced filename: `network-private-ip.svg`
- Source chapter: `docs/fa/user-guide/04-networking-fundamentals.md`
- Source section/context: §4.3 «Private IP چیست؟» — source sketch: Router → 192.168.1.10 / .20 / .50; "valid only inside the local network".

## Purpose
Teach that private IPs are assigned by the router and are only meaningful inside the home network (not routable on the Internet).

## Audience
Beginner operator / non-network specialist.

## Required elements
- Node: `Home Router`.
- A bounded group "Home network (private, not routable on the Internet)" containing: Laptop · 192.168.1.10, Phone · 192.168.1.20, Raspberry Pi · 192.168.1.50.
- Arrows: router → each device inside the boundary.

## Visual direction
A subgraph/box around the devices to emphasise the "local only" boundary. Simple, documentation style.

## Text labels
English, short.

## Security/privacy constraints
Generic private IPs only (192.168.1.x). No real domains, public IPs, tokens, QR codes, usernames, or credentials.

## Validation checklist
- [x] Matches chapter context (§4.3)
- [x] Uses generic examples (192.168.1.x)
- [x] No secret or personal data
- [x] File name matches manifest (`network-private-ip.svg`)
