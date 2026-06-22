# DGM-04 — Public IP and Conduit reachability

## Source
- Referenced filename: `network-public-ip-conduit.svg`
- Source chapter: `docs/fa/user-guide/04-networking-fundamentals.md`
- Source section/context: §4.3 (Conduit) — "a person on the Internet who wants to reach your Conduit node must first find your Public IP"; notes CGNAT can prevent this.

## Purpose
Teach that a remote user reaches your Conduit node through your public IP (and that without a usable public IP, e.g. behind CGNAT, this fails).

## Audience
Beginner operator / non-network specialist.

## Required elements
- Nodes: `Internet user`, `Your Public IP 185.201.45.123`, `Home Router`, `Raspberry Pi (Conduit node)`.
- Flow (left→right): Internet user → Public IP → Home Router → Raspberry Pi (Conduit node).

## Visual direction
Single horizontal flow, four boxes. Clean and minimal.

## Text labels
English, short.

## Security/privacy constraints
Use only the teaching IP `185.201.45.123`. No real domains, tokens, QR codes, usernames, or credentials.

## Validation checklist
- [x] Matches chapter context (§4.3 Conduit reachability)
- [x] Uses generic examples (185.201.45.123)
- [x] No secret or personal data
- [x] File name matches manifest (`network-public-ip-conduit.svg`)
