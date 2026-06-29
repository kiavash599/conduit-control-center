# ADR-0001: Trusted Update Engine

**Status:** Proposed
**Date:** 2026-06-29
**Deciders:** CCC maintainers
**Supersedes:** —   **Superseded by:** —

## Core Principle

> **Policy authorizes. Engine executes. Payload describes. Payload never commands privileged control flow.**

This sentence is the **conceptual summary** of the update architecture. It is a
memory aid and an orientation, **not** a substitute for the Architectural
Invariants below. Where the summary and the invariants appear to differ, the
invariants govern — they are the enforceable contract.

### Terminology

**Privileged Control Flow** — the ordered sequence of privileged operations,
**including but not limited to** deployment, migration, rollback, restart, and
recovery, and any other system modification requiring elevated privilege. This
term is intentionally **non-exhaustive**: any elevated-privilege system
modification is privileged control flow whether or not it is named here.

## Context

CCC lets operators update the system from the dashboard with one action, on
devices that are frequently small, remote, and unattended. Performing an update
requires privilege elevation, and the elevation mechanism is expected to change
over the life of the project. Because CCC is a censorship-circumvention tool, its
threat model explicitly includes **targeted supply-chain attacks**: a malicious or
compromised release must not be able to obtain privileged execution on the device.

These forces pull in different directions — convenient remote updates, dependable
**rollback and recovery** on constrained hardware, a **stable privilege boundary**
that survives changes to the elevation mechanism, and **auditability** of every
privileged action. On-device validation has also shown that update behaviour must
be **explicit** (driven by a declared contract, not by ambient environment
characteristics) and **observable** (a privileged run that fails silently cannot
be operated safely).

One structural fact frames the whole decision: the component that performs a
deployment is the **already-installed** updater, not the updater contained inside
the release being deployed. This ADR settles how that fact is to be treated.

## Architectural Roles

The update subsystem is understood as **three roles**. They are roles, not a
prescription of components — in particular, *Policy is a distributed concern,
enforced at more than one layer*.

- **Policy — authorizes.** Decides *whether* an update is permitted at all.
  A distributed concern spanning the privilege-elevation mechanism (today `sudo`,
  in future Polkit), backend authorization of the request, payload integrity
  decisions, and compatibility/capability decisions. Policy is not a single box.

- **Engine — executes.** The trusted, installed update engine. It exclusively
  owns **sequencing, ordering, privilege, restart, rollback, recovery, and
  execution** of an authorized update.

- **Payload — describes.** The downloaded release artifact: application code and
  declarative metadata describing the desired release state. The payload **never
  owns privileged workflow**; it is read and applied by the engine, never run by
  it as a controller.

```
              ┌───────────────────────────────┐
   TRUSTED    │  POLICY   — authorizes        │  elevation (sudo / Polkit) ·
   (installed │           (distributed)       │  backend authz · integrity ·
    / OS)     │                               │  compatibility · capability
              └───────────────┬───────────────┘
                              │  authorize? — else FAIL CLOSED
              ┌───────────────▼───────────────┐
   TRUSTED    │  ENGINE   — executes          │  sequence · order · privilege ·
   (installed)│           (trusted)           │  migrate · restart · rollback ·
              │                               │  recover
              └───────────────┬───────────────┘
                              │  engine READS payload as data
   ═══════════════════════════╪═══════════════════════  TRUST BOUNDARY
        data may cross  ▲      │      ▼  privileged control must NOT cross
              ┌───────────────▼───────────────┐
  UNTRUSTED   │  PAYLOAD  — describes          │  downloaded release: application
  (downloaded)│                               │  code + declarative metadata
              │                               │  (owns no privileged workflow)
              └───────────────┬───────────────┘
                              ▼
                        Installed CCC
```

## Decision

CCC adopts **Architecture A: the installed, trusted engine deploys the downloaded
release as data.**

- The installed engine performs the deployment. The release is **payload/data**.
- The payload may carry metadata and declarative migrations that the engine
  **interprets**; the payload **never controls privileged flow** and the engine
  **never executes installer or hook scripts from the payload as a privileged
  controller**.
- Release-specific behaviour moves over time toward **declarative, versioned
  payload data interpreted by the engine** from a closed, audited operation set.
- The engine **installs and verifies its successor**, accepting a bounded
  one-cycle lag for changes to engine behaviour.

**Architecture B — running the updater contained in the downloaded release — is
explicitly rejected.** Under B the downloaded artifact would control the
privileged deployment flow. That is rejected because it:

- **Security / supply-chain:** turns every release into a privileged execution
  surface; a compromised or mis-built release becomes privileged code execution.
  Architecture A keeps the privileged actor to code already installed under
  operator control.
- **Trusted update engine:** the logic that performs the most dangerous operation
  is the proven, on-device-validated engine, not unproven code running for the
  first time during the critical window.
- **Polkit compatibility:** authorization frameworks allowlist *known*
  executables. A pins a fixed, trusted action; B would require authorizing the
  execution of downloaded scripts, which is contrary to that model.
- **Rollback reliability:** rollback is performed by trusted, known-good logic
  rather than by unproven, self-referential rollback shipped in the same payload.

## Architectural Invariants

These are binding. A change that violates a **Hard Invariant** requires an explicit
architecture review (and, normally, a superseding ADR). Each is tagged with the
role it primarily serves.

### Hard Invariants

1. **Payload-as-Data** *(Payload).* The release is data, not a control program.
   The engine reads payload metadata and applies declarative migrations; the
   payload is never an executable that drives privileged flow. *Scope:* payload
   application code runs only in its normal least-privilege runtime context, and
   dependency installation is constrained — this invariant governs **privileged
   control flow**, not the mere existence of downloaded code.

2. **Engine Owns Privileged Workflow** *(Engine).* The engine exclusively owns
   sequencing, ordering, privilege decisions, restart, rollback, and recovery.
   The payload may only **request** capabilities from a closed, engine-defined
   set; the engine decides **if, when, and how** they execute. The payload cannot
   reorder phases, widen privilege, or disable/redirect rollback.
   **The Engine is the sole authority that translates Policy decisions into
   privileged actions.** Policy *authorizes*; Policy *never performs* the
   privileged workflow. Backend authorization, sudo/Polkit, or any future Policy
   implementation may **authorize the Engine**, but they never become workflow
   owners. **There is exactly one path from an authorized decision to privileged
   execution, and that path is the Engine.**

3. **Defense in Depth** *(Engine).* The engine self-enforces every policy decision
   within its scope. It never assumes an upstream layer — elevation mechanism,
   backend, or operator interface — has already authorized or verified; it
   re-checks independently.

4. **Fail Closed** *(Policy / Engine).* An unknown payload format, an unknown or
   declined required capability, an incompatible or too-old engine, or any
   unverifiable precondition results in a clean refusal with an actionable
   message. Never best-effort; never a silent proceed.

5. **Artifact Integrity** *(Policy, engine-enforced).* The engine deploys only a
   payload whose provenance and integrity it can establish. An unverifiable
   payload is not deployed.

6. **Least Privilege** *(Policy).* Privilege elevation grants only the single,
   narrow trusted action — no broad privileged shell, no dependence on inherited
   environment. This holds across any elevation mechanism.

7. **Rollback Always Exists** *(Engine).* Operator data is never destroyed; a
   recoverable prior state always exists; any state-changing step is reversible or
   checkpointed before it runs.

8. **Observability** *(Engine).* Every privileged update run records enough detail
   to diagnose a failure after the fact. Silent failure is a defect.

9. **Engine Self-Update Semantics** *(Engine).* The engine installs and verifies
   its successor and never deletes or replaces the running engine mid-flight in a
   way that breaks recovery. A bounded one-cycle lag for engine-behaviour changes
   is accepted by design.

### Guiding Principles

These shape quality and longevity. Bending them warrants review but is not, by
itself, a safety breach.

- **Keep the engine small and stable.** Business logic does not accumulate in it.
- **Make the engine independently testable.** It is infrastructure, not
  application logic, and is tested as such.
- **Prefer declarative migrations over engine churn.** Volatile, release-specific
  behaviour belongs in versioned payload data, not in engine code.

## Consequences

**Positive**

- A strong supply-chain boundary: downloaded artifacts never drive privileged flow.
- A privilege boundary that is stable across elevation mechanisms (sudo → Polkit
  changes Policy only; Engine and Payload roles and invariants are unchanged).
- A dependable rollback story grounded in trusted, validated logic.
- Privileged actions that are narrow, auditable, and observable.

**Negative**

- A bounded one-cycle lag before a change to engine behaviour can take effect.
- A defective installed engine may require a manual recovery deployment.

**Tradeoffs**

- The declarative-migration mechanism must be designed deliberately (a closed,
  reversible operation set), or it risks re-introducing arbitrary control.
- Payload integrity requires dedicated mechanism to be fully realized; until then
  the integrity invariant is satisfied by weaker provenance and must be hardened.

## Scope and ADR Split

This ADR defines the **architecture, roles, and invariants** of the Trusted Update
Engine. It deliberately **excludes the payload format and payload specification** —
the concrete manifest, schema, capability and migration declarations, integrity
(signature/checksum) scheme, and compatibility fields. Those evolve on their own
lifecycle and are recorded separately:

> **ADR-0002 — Update Payload Specification** *(future).*

ADR-0001 owns the *behavioural requirements and invariants* the engine must uphold
(e.g., integrity shall be verified; incompatibility shall fail closed). ADR-0002
will own *how* those are expressed in the payload.

## Future Work

High-level only; **not designed here** and not binding as architecture:

- Engine capability/compatibility handshake.
- Payload manifest.
- Payload signatures / checksums.
- Declarative migration framework.
- Independent engine test suite.
- Backend-visible compatibility warning before an update is offered.

## Notes

This ADR records a decision and its invariants; it implements none of the Future
Work. ADRs are immutable — revisions are made by a superseding ADR, not by editing
this record.
