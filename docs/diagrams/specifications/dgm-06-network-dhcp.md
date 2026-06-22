# DGM-06 — DHCP

## Source
- Referenced filename: `network-dhcp.svg`
- Source chapter: `docs/fa/user-guide/04a-understanding-home-internet-equipment.md`
- Source section/context: §4.5 «DHCP چیست؟» — source sketch: Raspberry Pi → Request IP → Router DHCP server → 192.168.1.50.

## Purpose
Teach that the router's DHCP server automatically assigns an IP to a device when it joins the network.

## Audience
Beginner operator / non-network specialist.

## Required elements
- Nodes: `Raspberry Pi`, `Router · DHCP server`, `192.168.1.50`.
- Flow: Raspberry Pi —(requests an IP)→ Router DHCP —(assigns)→ 192.168.1.50.

## Visual direction
Single left→right flow with labelled request/assign arrows. Minimal.

## Text labels
English, short. Edge labels: "Requests an IP", "Assigns".

## Security/privacy constraints
Generic private IP (192.168.1.50) only. No real domains, public IPs, tokens, QR codes, usernames, or credentials.

## Validation checklist
- [x] Matches chapter context (§4.5)
- [x] Uses generic examples (192.168.1.50)
- [x] No secret or personal data
- [x] File name matches manifest (`network-dhcp.svg`)
