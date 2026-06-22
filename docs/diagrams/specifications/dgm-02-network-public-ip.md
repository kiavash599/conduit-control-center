# DGM-02 — Public IP

## Source
- Referenced filename: `network-public-ip.svg`
- Source chapter: `docs/fa/user-guide/04-networking-fundamentals.md`
- Source section/context: §4.2 «Public IP چیست؟» — source sketch: Internet → 185.201.45.123 → Router; "the address the Internet sees, assigned by the ISP".

## Purpose
Teach that the ISP assigns a single public IP to your connection, and that is the address the rest of the Internet sees.

## Audience
Beginner operator / non-network specialist.

## Required elements
- Nodes: `Internet`, `Public IP 185.201.45.123`, `Home Router`.
- Flow (left→right): Internet → Public IP → Home Router.

## Visual direction
Single horizontal flow, three boxes. Clean, no decoration.

## Text labels
English, short. Public IP shown as the established teaching value.

## Security/privacy constraints
Use only the existing teaching IP `185.201.45.123`. No real domains, tokens, QR codes, usernames, or credentials.

## Validation checklist
- [x] Matches chapter context (§4.2)
- [x] Uses generic examples (185.201.45.123 already used as a teaching example)
- [x] No secret or personal data
- [x] File name matches manifest (`network-public-ip.svg`)
