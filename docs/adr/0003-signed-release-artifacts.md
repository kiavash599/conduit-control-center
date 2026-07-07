# ADR-0003: Signed Release Artifacts and On-Device Verification

**Status:** Accepted
**Date:** 2026-07-07
**Deciders:** CCC maintainers
**Supersedes:** —   **Superseded by:** —
**Relates to:** ADR-0001 (Trusted Update Engine — realizes its artifact-integrity expectation), ADR-0002 (Update Payload Specification — *planned*)

## Core Principle

> **A device installs only what a trusted publisher signed, verified fail-closed before any privileged action. The artifact is content-defined; every consumer trusts content, not timestamps.**

This is the orientation for the signing layer. Where it and the Architectural Invariants below appear to differ, the **invariants govern** — they are the enforceable contract.

## Context

ADR-0001 established that *policy authorizes, the engine executes, and the payload never commands privileged control flow.* It deliberately left **how a release proves its authenticity** to a later decision (artifact signing was deferred). CCC is a censorship-circumvention tool whose threat model includes **targeted supply-chain attacks**: a malicious or compromised release must never obtain privileged execution on a device. One-Click Update, being remote and unattended on constrained hardware, makes this concrete — the device must be able to reject an unauthentic update **before** any privileged step, and must do so identically every time regardless of the delivery channel (GitHub, mirror, or manual copy).

Forces: authenticity and integrity of releases; reproducibility (so a digest is a stable identity); a trust anchor that lives on the device and is not shippable with the payload; fail-closed behaviour; and non-authorizing auditability of the verification and deployment.

## Decision

1. **Canonical Release Artifact.** Releases are packed into a deterministic, content-fixed `.tar.gz`: members sorted, `mtime=0`, fixed mode/uid/gid and empty owner names, gzip header `mtime=0`. Identical content yields identical bytes, so the **content digest (SHA-256)** is a stable identity. Artifacts are built **only from a committed, tagged source** (`commit → tag → --git-ref`).
2. **Signed Object.** A manifest (`format_version = 1`) binds `{product, version, artifact name, digest{algorithm, value}, compatibility{platform, recommended_conduit_core}}` and is signed with an **SSH Ed25519** key using SSHSIG, namespace **`ccc-update-manifest`**, publisher identity **`conduit-control-center-publisher`**.
3. **On-Device Trust Store (M2).** The device holds an `allowed_signers` trust anchor at `/opt/conduit-cc/trust/allowed_signers` (root-owned, service-readable, `root:conduit-cc 0750`). It is **provisioned out-of-band** and is **never** shipped inside a release artifact.
4. **Fail-Closed Verification.** The privileged update helper verifies the manifest signature against the on-device trust store **before extraction and before the version gate**. Any verification, product-scope, or integrity failure aborts with **no** privileged action.
5. **Non-Authorizing Observability (Phase-B).** The helper writes append-only audit records for update outcomes (`accepted`, `applied`, `reverted`, and the reject/failure taxonomy) with **allowlist redaction** (no trust material) to a root-owned audit log (`/var/log/conduit-cc-audit/update-audit.jsonl`, `root:conduit-cc 0640`). Audit is best-effort and **never** alters the verifier result, exit code, status, deployment, or rollback.
6. **Deterministic-Artifact Consumer Invariant.** Because artifacts are `mtime=0`, every consumer that deploys or compares artifact content **must decide by content (hash), never by size+mtime** (realized by `rsync --checksum`; see CCC-CAMP-0001 / CP-001).

## Architectural Invariants (enforceable)

- **I1** — No privileged action occurs before a passing fail-closed signature verification against the on-device trust store.
- **I2** — Trust material (private keys, `allowed_signers`) is never embedded in a release artifact and never appears in audit output.
- **I3** — The artifact is deterministic and content-addressed; its SHA-256 digest is its identity.
- **I4** — Releases are built only from a committed, tagged source (provenance chain intact).
- **I5** — Observability is non-authorizing: audit or logging never alters a trust or control decision.
- **I6** — Consumers of the deterministic artifact compare by content, not timestamps.

## Normative constants

`PRODUCT = "conduit-control-center"` · `DIGEST_ALGORITHM = "sha256"` · `SSHSIG_NAMESPACE = "ccc-update-manifest"` · `PUBLISHER_IDENTITY = "conduit-control-center-publisher"` · manifest `format_version = 1`.

## Consequences

Positive: releases are authenticated end-to-end; a compromised channel cannot obtain privileged execution; digests give reproducible identity; deployment and rollback are auditable without trusting the audit for control. Costs: more release ceremony (build → sign → verify → publish → verify-published) — mitigated by the registered **Owner Operations Toolkit** backlog item; and the determinism/`mtime=0` property requires content-based consumers (**I6**), a cross-layer discipline surfaced during v0.3.14 (the same `mtime` tie affected both CPython `.pyc` validation and rsync's quick-check).

## Relationship to other ADRs

- **ADR-0001 (Trusted Update Engine):** owns the engine invariants (policy authorizes, engine executes). ADR-0003 **realizes** ADR-0001's artifact-integrity expectation, which ADR-0001 deferred.
- **ADR-0002 (Update Payload Specification, planned):** will formalize the full payload/manifest schema, capability and migration declarations, and compatibility fields. ADR-0003 fixes the **signing, verification, and trust** decision and the current manifest fields; the two are complementary.
