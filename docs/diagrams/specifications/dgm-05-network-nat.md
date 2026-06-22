# DGM-05 — NAT (Network Address Translation)

## Source
- Referenced filename: `network-nat.svg`
- Source chapter: `docs/fa/user-guide/04-networking-fundamentals.md`
- Source section/context: §4.4 «NAT چیست؟» — source sketch: Laptop/Phone/TV/Raspberry Pi → Router (NAT) → Public IP 185.201.45.123 → Internet; "many devices share one public IP".

## Purpose
Teach that NAT on the router lets many private devices share a single public IP when talking to the Internet.

## Audience
Beginner operator / non-network specialist.

## Required elements
- A "Home network" group: Laptop, Phone, TV, Raspberry Pi · 192.168.1.50.
- Nodes: `Router · NAT`, `Public IP 185.201.45.123`, `Internet`.
- Arrows: each device → Router (NAT) → Public IP → Internet.

## Visual direction
Several devices converging into one router, then a single line out to the public IP and the Internet. Clean, documentation style.

## Text labels
English, short.

## Security/privacy constraints
Generic private IP (192.168.1.50) and the teaching public IP (185.201.45.123) only. No real domains, tokens, QR codes, usernames, or credentials.

## Validation checklist
- [x] Matches chapter context (§4.4)
- [x] Uses generic examples (192.168.1.50, 185.201.45.123)
- [x] No secret or personal data
- [x] File name matches manifest (`network-nat.svg`)
