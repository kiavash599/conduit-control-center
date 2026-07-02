# Project Lifecycle

**Status:** Adopted
**Applies to:** All Conduit Control Center (CCC) work after Implementation Phase 1
**Type:** Project process (not an ADR, not an IDD, not architecture)
**Companion:** `ENGINEERING-PROCESS-GUIDE.md` (defines *how* an increment is built; this document defines *when* and *in which state* the project moves).

---

## 1. Purpose

This document defines how the CCC project progresses through its lifecycle after
Phase 1. It names the states the project can be in, the legitimate reasons work
moves between them, and — critically — what may and may not start a new
implementation phase.

The Engineering Process Guide governs the discipline *inside* an implementation
increment. This document governs the transitions *between* states of the project
as a whole. Together they ensure the project advances only for real reasons, in
safe steps, and stops cleanly when there is nothing valuable left to build.

This is a process document. It defines no architecture, records no architectural
decision, and specifies no implementation. It is normative: "must" is mandatory;
"should" permits reasoned, reviewed deviation.

---

## 2. Project States

The project is always in one of the following states. A state describes what kind
of work is legitimate at that moment.

- **Architecture.** Architectural questions are explored and decided. Outputs are
  recorded as ADRs and frozen. No implementation occurs. The project leaves this
  state only when the relevant architecture is settled and frozen.

- **Design.** Implementation-design decisions are made *within* frozen
  architecture — contracts, encodings, boundaries, and similar. Outputs are
  recorded as IDDs (or design notes) and frozen when settled. No implementation
  occurs. Filling a deliberately deferred design slot happens here; reopening a
  frozen decision does not.

- **Implementation.** Approved increments are built following the standard
  workflow. Only work that passes the Value Gate and the Batch Design Rules is
  admitted. This is the only state in which code is written.

- **Deferred Decision Gate.** Implementation is paused to resolve a specific
  decision that blocks the next valuable increment. The gate resolves the smallest
  sufficient decision and nothing more. It changes no code and reopens no frozen
  architecture.

- **Backlog.** A holding state, not a work state. It records known but non-actionable
  items: unresolved deferred decisions, blocked work, future refinements, and ideas
  that do not currently justify implementation. Items rest here until they acquire
  a legitimate driver.

- **Phase Closure.** The formal conclusion of an implementation phase. The phase's
  achievements, remaining deferred work, next-phase entry criteria, and risks are
  recorded, and a phase-closing statement is issued. After closure the project is
  idle with respect to implementation until a driver opens the next phase.

The project may rest **between phases** (idle) after a Phase Closure. Idle is a
legitimate, healthy state — not a gap to be filled with manufactured work.

---

## 3. Implementation Drivers

A new implementation phase (or increment) begins **only** in response to a real
driver. A driver is a concrete, present need that yields at least one
independently valuable increment.

- **First real capability.** A concrete ability the system must actually possess or
  gate. It is a valid driver because it produces increments with real behavior and
  real tests, and because supporting infrastructure can be introduced *with* it
  rather than ahead of it.

- **Runtime integration.** An approved decision to wire completed, validated
  components into the running system, with the prerequisites for that integration
  resolved. It is valid because integration delivers observable end-user behavior
  that the pure components alone cannot.

- **A previously deferred decision becoming ready.** A deferred decision whose
  inputs are now settled and which unblocks a valuable increment. It is valid
  because the decision can now be made soundly (on evidence, not assumption) and
  its resolution enables real work.

Each of these is valid for the same underlying reason: it corresponds to a
genuine need, so the resulting work passes the Value Gate — something concrete
would be lost if the work did not exist.

---

## 4. Non-Drivers

The following must **never** start implementation. Each fails the Value Gate: if
the work vanished, the project would lose no concrete capability.

- **Placeholder infrastructure.** Building a component to "reserve a place" for
  future work. Rejected because it adds a hollow surface that implies capability
  the system does not have, and because the real work will define that component
  when it arrives anyway — usually more cheaply.

- **Empty wrappers.** A layer that only forwards to, or trivially re-expresses,
  existing behavior. Rejected because it adds indirection without capability and
  dilutes the meaning of the components it wraps.

- **Code symmetry.** "One side has this, so the other side should too." Rejected
  because symmetry is an aesthetic, not a need; the other side is built when it has
  a real driver, and building it early is often duplicative.

- **"Preparing for the future."** Anticipatory work for problems with no present
  instance. Rejected because it commits to assumptions before they are validated
  and accumulates unused, unmaintained surface area.

- **Artificial batches.** Manufacturing an increment to appear productive or to
  keep momentum. Rejected because it violates the discipline that work is driven by
  value, not by the desire to be busy; idle between phases is preferable.

- **Schedule or momentum pressure alone.** The wish to "keep going" is not a
  technical driver. Rejected for the same reasons as artificial batches.

When a non-driver produces a genuinely good idea, that idea is captured in the
**Backlog** (§7), not implemented.

---

## 5. Transition Rules

The project moves between states only under these rules. Every transition into
Design or Implementation, and every gate, is human-authorized.

```
Architecture ──(frozen)──▶ Design ──(frozen)──▶ Implementation
       ▲                                    │        │
       │                                    │        ├──(driver + value + approval)──▶ Implementation (next increment)
       │                                    │        │
       │                          (blocking decision)│
       │                                    ▼        ▼
       │                         Deferred Decision Gate
       │                                    │ (smallest sufficient resolution)
       │                                    ▼
       │                              Implementation (resume, only if a driver exists)
       │                                    │
       │                                    ▼
       └───────────── (superseding ADR only) ──  Phase Closure ──▶ Idle ──(driver)──▶ Implementation (next phase)

Backlog ──(item acquires a driver + passes gates)──▶ Implementation
```

- **Architecture → Design → Implementation.** Each precedes the next and must be
  frozen before the next begins. Implementation never runs ahead of frozen
  architecture and design.
- **Implementation → Deferred Decision Gate.** Entered when the next valuable
  increment is blocked by an unresolved decision. Implementation pauses.
- **Deferred Decision Gate → Implementation.** Re-entered only when the gate is
  resolved *and* a real driver for the next increment exists.
- **Implementation → Phase Closure.** Entered when no remaining candidate passes
  the Value Gate and Batch Design Rules (see §8).
- **Phase Closure → Idle → Implementation.** After closure the project is idle;
  the next implementation phase opens only on a driver (§3).
- **Backlog → Implementation.** A backlog item is promoted only when it acquires a
  driver and passes the gates; it is never implemented merely because it exists.
- **Reopening frozen architecture** is not a normal transition; it requires an
  explicit superseding ADR, never a silent change.

No transition into Implementation is valid without (a) a driver, (b) an increment
that passes the Value Gate, and (c) explicit approval of its plan.

---

## 6. Deferred Decision Lifecycle

- **Created.** A deferred decision is recorded when a question is intentionally left
  open — because its inputs are not yet settled, or because it is not yet needed.
  Recording it as deferred is preferred over guessing and building around it.

- **Resolved.** A deferred decision is resolved through a **Deferred Decision Gate**
  when, and only when, two conditions hold: it blocks a genuinely valuable
  increment, and its inputs are now available to decide it soundly. The gate
  resolves the **smallest sufficient** form of the decision and defers everything
  not required to unblock. Resolution is recorded and reopens no frozen decision.

- **Implementation resumes.** After resolution, implementation resumes only if a
  real driver for the next increment now exists. Resolving a deferred decision
  removes a blocker; it does not by itself authorize new code. If no
  independently valuable increment exists after resolution, the project remains
  paused or proceeds to Phase Closure.

---

## 7. Backlog Rules

The Backlog is the boundary between "known" and "actionable."

**Belongs in the Backlog:**

- Unresolved deferred decisions and the work they block.
- Future refinements and optional hardening that no present driver requires.
- Ideas discovered during challenge, review, or implementation that are valuable
  but not now (including good ideas that surfaced from a non-driver).
- Any candidate that fails the Value Gate or a Batch Design Rule today but might
  qualify later.

**Belongs in Implementation (not the Backlog):**

- Only increments that have a real driver, pass the Value Gate and Batch Design
  Rules, and have an approved plan.

**Promotion.** A backlog item moves to Implementation when it acquires a driver and
passes the gates — not because it has waited, and not to reduce the backlog's
size. The backlog may grow; that is acceptable. Backlog items are never
implemented merely to "clear" them.

---

## 8. Phase Completion Rules

A phase is **formally closed** when no remaining candidate increment satisfies all
of the following simultaneously: independently valuable, independently testable,
independently reversible, additive, minimal, non-duplicative, free of runtime
wiring unless explicitly required, and free of architectural drift. In practice
this means every candidate is duplicative, blocked by a deferred decision,
premature runtime wiring, placeholder infrastructure, or otherwise without
independent value.

The **engineering-value criterion** that emerged during Phase 1 is decisive at
closure. For each candidate, ask:

> **"If this increment disappeared today, what concrete engineering capability
> would the project lose?"**

If the answer for every remaining candidate is "None," the phase is complete.

Closing a phase is deliberate and produces a recorded closure containing:

- a summary of what the phase delivered,
- the remaining deferred work,
- the entry criteria (drivers) for the next phase,
- the risks to address before the next phase,
- a formal phase-closing statement.

Closure is a success state. Reaching it means the phase delivered its value and
stopped before manufacturing hollow work.

---

## 9. Recommendations for Future Phases

- **Open a phase only on a driver.** Begin a new implementation phase only when a
  real driver from §3 exists. Absent a driver, remain idle; do not manufacture work
  (§4).

- **Carry deferred work and backlog forward.** Start each phase by reviewing the
  previous closure: confirm which drivers are now available, which deferred
  decisions are now ready, and which backlog items now qualify.

- **Resolve validation and housekeeping debt first.** Before building on prior work,
  confirm the previous phase's results in the authoritative environment and clear
  outstanding validation or repository debt.

- **Keep the gates and workflow intact.** Apply the standard workflow, the Value
  Gate, the deferred-decision discipline, and the Batch Design Rules to every
  increment in every phase. The process does not relax as the project matures.

- **Bundle enabling infrastructure with its first real use.** Introduce supporting
  components together with the concrete capability or integration that first needs
  them, so each increment is independently valuable rather than a placeholder.

- **Treat frozen decisions as stable contracts.** Filling a deliberately deferred
  slot is permitted; reopening a settled architectural or design decision requires
  an explicit, reviewed superseding decision.

---

*This document is a permanent part of the CCC project process. It is revised only
by explicit, reviewed change, and it governs how the project progresses through
its lifecycle in all phases following Phase 1.*
