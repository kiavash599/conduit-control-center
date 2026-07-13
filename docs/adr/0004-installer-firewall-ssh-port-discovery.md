# ADR-0004 — Purpose-aware installer firewall & SSH-port discovery

**Status:** Accepted (2026-07-13; Project-Owner approved). Applies to the
unreleased **0.3.15** installer.
**Context owners:** Project Owner + Campaign Owner.
**Related:** ADR-0001 (Trusted Update Engine), ADR-0003 (Signed Releases),
BL-0002 (Raspberry Pi 2 / armhf support).

## Context

A fresh Raspberry Pi 2 clean-image validation (Ubuntu 22.04.5 armhf) found a
release-blocking installer safety defect. `install.sh` phase 2j hardcoded:

```
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw --force enable
```

The installer **equated "SSH administration" with the conventional port 22** and
then enabled a default-deny firewall. On the Owner's real deployment sshd listens
on a **non-22 port (1222)**: the router forwards WAN 1222 -> RPi2:1222, and the
board's local sshd port is 1222. With UFW enabling default-deny and no rule for
1222, administrative SSH would survive only via conntrack and be **locked out on
reconnect or reboot**. The clean-image test was stopped before execution; rollback
verification passed. Classified as a lockout near-miss, corrected before publication.

**Core principle:** a listening socket is *evidence*, not authorization to expose a
service. The router/WAN forwarding port is distinct from the board's local sshd
port; the installer manages only local board ports and never inspects/manages NAT.

## Decision

The installer builds a **purpose-aware firewall plan from local evidence only**,
resolved and printed **before any UFW-writing command**, and applies it as a single
add-before-enable transaction:

- **SSH administration -> the evidenced local sshd port(s):**
  - *Anchor A* (active session): procfs ancestry walk from the installer collects
    every `sshd` PID on that exact chain (privilege-separation aware), correlates
    their ESTABLISHED sockets, and reads the **local** endpoint port (post-NAT, not
    the WAN port). Inherited/validated `SSH_CONNECTION` is a fallback/cross-check.
    Ambiguity (zero or multiple differing chain-owned ports; socket/env
    disagreement) is fatal.
  - *Configured set C* (post-reboot): effective `ssh.socket` `Listen` **only when
    the socket is active/enabled** (a disabled/inactive socket's stale `Listen` is
    ignored), otherwise `sshd -T` `port` (Include/drop-ins resolved; OpenSSH does
    not allow `Port` in `Match`, so `sshd -T` is complete). Unreadable governing
    evidence is fatal.
  - *Runtime listeners L* are corroboration only; never opened merely because seen.
- **HTTP redirect -> fixed TCP 80.**
- **CCC HTTPS -> the installer-selected port** (via `ccc-apply-https-port`).
- **Conduit inbound UDP -> no rule** (dynamic ports; preserved from prior decision).

**Conflict policy:** no conventional fallback; no automatic union of conflicting
evidence. `A in C` -> plan `C`. `A not in C` without a valid override -> **fatal
before any UFW mutation**. Local console with readable `C` -> plan `C`; with sshd
present but unreadable/ambiguous `C` -> fatal; with no sshd -> empty SSH plan is
permitted. **RPi2 -> exactly `{1222}`; RPi4 -> exactly `{22}`.**

**Override:** the only override is `CCC_SSH_PORTS` (comma-separated ports),
documented sudo-safe as `sudo env CCC_SSH_PORTS=1222 bash install.sh`. It must
include the active session port when running over SSH; an override that omits it,
or any malformed/empty/out-of-range value, is **fatal**. No CLI parser; no
active-port-drop escape hatch in 0.3.15.

**Transaction ordering (single consolidated UFW write):** resolve+print (read-only)
-> `ccc-apply-https-port apply --skip-ufw` (nginx only) -> revalidate & recompute
(fail if evidence changed) -> `ufw --dry-run` each rule -> add SSH/HTTP/HTTPS rules
add-only, exit-checked, deterministic order -> locale-stable pre-enable verify ->
`ufw --force enable` -> locale-stable post-enable verify. Any failure before enable
means `ufw --force enable` is not reached. Existing rules are never reset/deleted;
on a partial add while UFW was already active, applied and failed rules are reported
and no rollback/atomicity is claimed. The HTTPS helper gains a backward-compatible
`--skip-ufw`; `update.sh` keeps the default and is unchanged.

## Alternatives rejected

Hardcode 22; open every `ss`/`netstat` socket; hand-parse `sshd_config` (misses
Include/drop-ins and socket activation); match the session by remote IP / `who -m`
(cannot separate two sessions from one IP); union conflicting evidence (widens
attack surface — a stale/conventional port can become externally reachable after a
router change); delete-then-re-add ("clean slate", risks dropping the admin's live
rule); bump to 0.3.16 (0.3.15 is unpublished, so it is corrected in place).

## Consequences

Minimal exposure and no lockout on custom SSH ports; multiple legitimate SSH ports
supported; a bounded override for rare ambiguous setups; a small procfs/ss/systemd
dependency at install time. Verified by `tests/unit/test_firewall_ssh_plan.py` and
`tests/unit/test_ccc_apply_https_port.py`; the real-hardware acceptance matrix
(RPi2/RPi4 x SSH 22/1222, fresh external SSH connection, post-reboot external
reconnect, health/metrics) is a release gate.
