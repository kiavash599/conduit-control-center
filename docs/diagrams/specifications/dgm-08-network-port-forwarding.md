# DGM-08 — Port forwarding

## Source
- Referenced filename: `network-port-forwarding.svg`
- Source chapter: `docs/fa/user-guide/04a-understanding-home-internet-equipment.md`
- Source section/context: §4.9 «Port Forwarding چیست؟» — source sketch: Internet → Router (Port 443) → 192.168.1.50; analogy "a building's front desk routing mail to the right unit".

## Purpose
Teach that port forwarding tells the router to send inbound traffic on a given port to the Raspberry Pi inside the network.

## Audience
Beginner operator / non-network specialist.

## Required elements
- Nodes: `Internet`, `Home Router (port forwarding)`, `Raspberry Pi 192.168.1.50`.
- Flow: Internet —(TCP 443)→ Router (port forwarding) → Raspberry Pi.

## Visual direction
Single left→right flow with the port on the inbound arrow. Minimal.

## Text labels
English, short. Edge label: "TCP 443".

## Security/privacy constraints
Generic private IP (192.168.1.50) only. No real domains, public IPs, tokens, QR codes, usernames, or credentials.

## Validation checklist
- [x] Matches chapter context (§4.9)
- [x] Uses generic examples (192.168.1.50, TCP 443)
- [x] No secret or personal data
- [x] File name matches manifest (`network-port-forwarding.svg`)
