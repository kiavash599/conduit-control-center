# DGM-07 — DHCP reservation

## Source
- Referenced filename: `network-dhcp-reservation.svg`
- Source chapter: `docs/fa/user-guide/04a-understanding-home-internet-equipment.md`
- Source section/context: §4.7 «DHCP Reservation چیست؟» — source sketch: MAC B8:27:EB:12:34:56 → Router DHCP → "always assign" → 192.168.1.50.

## Purpose
Teach that a DHCP reservation binds a device's MAC address to a fixed IP, so the Raspberry Pi always receives the same address.

## Audience
Beginner operator / non-network specialist.

## Required elements
- Nodes: `Device MAC B8:27:EB:12:34:56`, `Router · DHCP reservation`, `192.168.1.50`.
- Flow: MAC → Router DHCP reservation —(always assigns)→ 192.168.1.50.

## Visual direction
Single left→right flow. Minimal; emphasise the fixed binding.

## Text labels
English, short. Edge label: "Always assigns".

## Security/privacy constraints
Example MAC and private IP only (both generic teaching values). No real domains, public IPs, tokens, QR codes, usernames, or credentials.

## Validation checklist
- [x] Matches chapter context (§4.7)
- [x] Uses generic examples (MAC B8:27:EB:12:34:56, 192.168.1.50)
- [x] No secret or personal data
- [x] File name matches manifest (`network-dhcp-reservation.svg`)
