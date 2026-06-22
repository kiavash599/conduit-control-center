# DGM-01 — IP address on the home network

## Source
- Referenced filename: `network-ip-address.svg`
- Source chapter: `docs/fa/user-guide/04-networking-fundamentals.md`
- Source section/context: §4.1 «آدرس IP چیست؟» (what is an IP address) — source sketch lists Laptop/Phone/TV/Raspberry Pi each with a private IP; analogy "an IP is like a home address".

## Purpose
Teach that every device on a home network has its own IP address, used to deliver data to the correct device.

## Audience
Beginner operator / non-network specialist.

## Required elements
- Node: `Home Router`.
- Four device nodes, each labelled with an example private IP: Laptop · 192.168.1.10, Phone · 192.168.1.20, TV · 192.168.1.30, Raspberry Pi · 192.168.1.50.
- Arrows: router → each device (the network reaches each device by its address).

## Visual direction
Simple top-down tree, one router fanning out to four devices. Documentation style; no decoration.

## Text labels
English labels, short. Keep device name + IP on one line.

## Security/privacy constraints
Generic example IPs only (192.168.1.x private range). No real domains, IPs, tokens, QR codes, usernames, or credentials.

## Validation checklist
- [x] Matches chapter context (§4.1 addressing concept)
- [x] Uses generic examples (192.168.1.x)
- [x] No secret or personal data
- [x] File name matches manifest (`network-ip-address.svg`)
