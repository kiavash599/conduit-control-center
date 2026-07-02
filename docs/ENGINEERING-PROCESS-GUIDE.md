# Engineering Process Guide

**Status:** Adopted
**Applies to:** All Conduit Control Center (CCC) implementation work
**Type:** Engineering process (not an ADR, not an IDD, not architecture)

---

## 1. Purpose

This guide captures the engineering workflow and discipline that proved
successful during Implementation Phase 1 of the CCC capability subsystem. It
exists so that every subsequent implementation phase follows the same method,
producing changes that are safe, reviewable, reversible, and genuinely valuable.

It is a **process** document. It does not define architecture, does not record
architectural decisions, and does not describe implementation mechanisms. Those
belong to architecture notes, ADRs, and IDDs respectively. This guide describes
*how* engineering work is conducted, not *what* is built.

The guide is normative: where it uses "must," compliance is expected; where it
uses "should," deviation requires a stated, reviewed reason.

---

## 2. Core Engineering Principles

These principles govern all implementation work. They are listed as peers; in
practice they reinforce one another.

- **Evidence over opinion.** Every technical claim is grounded in verifiable
  evidence — frozen architecture and design documents, source code, tests, or
  observed behavior. Assertions, authority, and "it is obviously true" carry no
  weight. Facts, inferences, and hypotheses are labeled distinctly.

- **Architecture before implementation.** Architecture and design are settled and
  frozen before code is written. Implementation transcribes accepted decisions;
  it does not invent them. Discovering an architectural question during
  implementation is a signal to stop, not to improvise.

- **No hidden assumptions.** Assumptions are made explicit and checked against
  evidence. An unstated assumption is treated as a defect waiting to surface.

- **No unnecessary abstractions.** An abstraction is introduced only when it earns
  its place through a concrete, present need. A fixed set of outcomes is expressed
  with explicit control flow, not with a general mechanism that anticipates cases
  that do not exist.

- **No placeholder infrastructure.** Code is not written to "reserve a place" for
  future work. Infrastructure is built when a real consumer or capability requires
  it, not before.

- **Independent engineering value.** Every increment must add a concrete capability
  the project did not previously have. Smallness is not value (see §4).

- **Independent rollback.** Every increment can be reverted on its own, in
  dependency order, without forcing changes to unrelated work and without
  disturbing the running system.

- **Independent validation.** Every increment can be validated on its own, with its
  own tests, without depending on later work.

- **Small additive increments.** Work advances in the smallest additive steps that
  each deliver value. New behavior is added; existing behavior is not silently
  altered.

- **Protect completion over endless design.** Once a body of work is complete and
  correct, it is closed. Re-litigating settled decisions, or continuing to design
  past the point of a sound answer, is waste and risk.

- **Fail closed.** When anything cannot be verified, the safe outcome is refusal,
  never an optimistic proceed. This applies to code behavior and to process
  decisions alike.

- **Scope creep prevention.** Each increment does exactly what is approved and
  nothing more. Adjacent "while we are here" work is rejected and, if valuable,
  captured as a separate item.

---

## 3. Standard Engineering Workflow

Every implementation increment follows this workflow. Each stage has a distinct
purpose and a distinct exit condition; stages are not skipped or merged.

```
Challenge → Evidence → Alternatives → Recommendation → Value Review
          → Implementation Plan → Approval → Implementation
          → Validation → Commit → Stop
```

- **Challenge.** State the problem or decision precisely, and actively question its
  premise. Ask whether the increment is even the right next step. The goal is to
  disprove weak proposals cheaply before any effort is spent.

- **Evidence.** Gather the verifiable evidence the decision rests on — frozen
  documents, source, tests, observed behavior. Separate what is proven from what
  is inferred. Speculative, zero-instance concerns are labeled and deferred, not
  built for.

- **Alternatives.** Enumerate the realistic candidate approaches and compare them on
  explicit axes (value, risk, dependencies, reversibility, duplication, scope).
  A single-option proposal is incomplete.

- **Recommendation.** Select the **smallest correct** option, with reasoning tied to
  the evidence and the comparison. The recommendation names what is chosen and why
  the alternatives were rejected.

- **Value Review.** Apply the Value Gate (§4). If the increment provides no
  independent engineering value, it is not implemented, regardless of how small or
  clean it is.

- **Implementation Plan.** Produce a plan — scope, responsibilities, components
  affected, validation strategy, rollback strategy, exit criteria — with no code.
  The plan is what is reviewed and approved.

- **Approval.** A human authorizes the plan. Nothing proceeds to implementation
  without explicit approval. Approval is per-increment and is not generalized to
  later work.

- **Implementation.** Build exactly the approved plan — additive, minimal, nothing
  extra. If a better idea appears during implementation, stop and record it as a
  backlog proposal; do not silently expand the increment.

- **Validation.** Execute the defined validation: tests, static checks, and
  confirmation that the change is additive, introduces no drift, and adds no
  unintended wiring. The authoritative environment (CI or the canonical developer
  machine) is the source of truth for results.

- **Commit.** Record the increment as an isolated, reviewable commit — one logical
  increment per commit — with a clear message. Commits remain in dependency order.

- **Stop.** End the increment and await review. Work does not continue automatically
  to the next increment.

---

## 4. Value Gate

A discipline that emerged and proved decisive during Phase 1: **an increment must
justify its own existence by the value it adds, not by its size or cleanliness.**

The gate is applied with one question:

> **"If this implementation increment disappeared today, what concrete engineering
> capability would the project lose?"**

- If the answer names a concrete capability that would be lost, the increment has
  independent value and may proceed.
- If the answer is **"None"**, the increment **should normally not be
  implemented**. It is either duplicative, premature, or placeholder
  infrastructure.

**Why this matters.** Small, clean, additive, reversible code can still be
worthless — a component that reserves a place for future work, or that merely
re-expresses behavior already reachable, passes every structural check yet adds
nothing. The Value Gate blocks exactly this class of premature or placeholder
infrastructure. It forces each increment to be driven by a real, present need
rather than by anticipation, keeping the codebase free of hollow surfaces that
imply capability the system does not have.

Corollary questions that sharpen the gate:

- Which later work becomes *impossible* or *significantly harder* without this
  increment? If none, the increment is not a prerequisite.
- Does this increment introduce a new capability, or reserve a place for one? If
  the latter, defer it until the capability itself exists.

---

## 5. Deferred Decision Gates

Not all questions can be answered when they arise. A **deferred decision** is a
question intentionally left open, to be resolved when it becomes both necessary
and possible.

**When implementation must stop.** Implementation stops when the next valuable
increment is blocked by a decision whose inputs are not yet settled, or when
proceeding would require inventing an answer that belongs to a deferred decision.
Building "around" an unresolved decision is prohibited.

**When a deferred decision should be opened.** A deferred decision is opened as a
narrow **gate** the moment it blocks a genuinely valuable increment. The gate
resolves the **smallest sufficient decision** — only what is needed to unblock —
and defers everything else. A gate is reviewed and recorded like any other
decision, but it changes no code and reopens no frozen architecture; it fills a
slot the architecture deliberately left open.

**When implementation may resume.** Implementation resumes when the gate is
resolved *and* a real driver for the next increment exists (see §8 and §10).
Resolving a gate does not by itself authorize new code; it only removes a
blocker. If, after resolution, no independently valuable increment exists,
implementation remains paused.

---

## 6. Architecture Challenger

The **Architecture Challenger** is a dedicated adversarial role whose purpose is
to *disprove* proposals, not to improve or defend them.

**Responsibilities.**

- Attack candidate designs, plans, and increments with the explicit goal of
  finding a flaw, grounded only in evidence.
- Refuse to protect prior conclusions merely because they already exist; any
  conclusion that has become load-bearing is re-checked against evidence.
- Apply the same scrutiny to challenges themselves (counter-challenge), so that a
  critique cannot silently replace a sound design with a flawed objection.
- Distinguish a genuine contradiction from a legitimate disagreement or an
  underspecified interaction.

**When implementation must stop immediately.** If the Challenger finds a genuine
contradiction — between two accepted decisions, or between a proposed increment
and a frozen architectural or design contract — implementation stops at once. The
contradiction is **surfaced, not resolved unilaterally**: it is stated plainly,
with its evidence, and referred to the decision owner. Work does not continue
past an unresolved contradiction, even under pressure to proceed. Resolution is
the decision owner's responsibility; the Challenger's responsibility is to make
the contradiction impossible to miss.

---

## 7. Batch Design Rules

Every implementation batch is designed to have all of the following
characteristics. A candidate that fails any of them is reworked or rejected.

- **Independently valuable** — adds a concrete capability the project lacked (§4).
- **Independently testable** — validated by its own tests, without later work.
- **Independently reversible** — revertible in dependency order with zero impact on
  the running system and no forced changes to unrelated work.
- **Additive** — adds behavior; does not silently change existing behavior.
- **Minimal** — the smallest step that delivers the value.
- **Non-duplicative** — does not reimplement logic that already exists; shared logic
  is reused, not copied.
- **Free of runtime wiring unless explicitly required** — pure and unwired by
  default; integration into runtime flow happens only when the architecture
  explicitly calls for it and it is separately approved.
- **Free of architectural drift** — consistent with every frozen decision; it never
  quietly reinterprets or expands them.

---

## 8. Phase Closure

An implementation phase is **formally closed** when no remaining increment
satisfies all of the Batch Design Rules (§7) and the Value Gate (§4)
simultaneously — that is, when every candidate is duplicative, blocked by a
deferred decision, premature runtime wiring, placeholder infrastructure, or
otherwise without independent value.

Closing a phase is a deliberate act, not a drift into inactivity. It produces:

- a summary of what the phase delivered,
- the list of remaining deferred work,
- the entry criteria (real drivers) required to open the next phase,
- the risks to address before the next phase,
- a recorded, formal phase-closing statement.

**Criteria used to conclude Phase 1.** Phase 1 was declared complete when the pure
capability decision pipeline was finished and every possible next increment was
shown to fail at least one closure criterion. Specifically, at closure:

- The intended pipeline (decode → extract → evaluate → decide) was complete,
  pure, unwired, additive, independently testable, independently reversible, and
  free of architectural drift.
- The one gating deferred decision required for further progress had been resolved
  to its minimal sufficient form.
- No candidate increment was simultaneously independently valuable,
  non-duplicative, non-placeholder, unblocked by a deferred decision, and free of
  premature runtime wiring.

The next phase opens only when a real driver exists (§10).

---

## 9. Lessons Learned from Phase 1

These lessons were paid for during Phase 1 and are retained to avoid re-learning
them.

- **Representation ≠ naming policy.** An encoding layer answers "how is a value
  represented and recovered," not "which values are allowed to exist." Constraining
  which values are valid, based on their content, imports semantic policy into a
  layer that should be purely representational and couples it to concerns it does
  not own.

- **Trust boundary ≠ programming-contract boundary.** The trust boundary guards
  untrusted *content* crossing into the system; it is not the place to absorb
  misuse of an internal API. Untrusted content that fails validation is refused
  (fail closed); a caller supplying the wrong argument type is a programming defect
  and is surfaced as such, not silently reclassified as bad data. Conflating the
  two masks bugs and dilutes the meaning of "malformed."

- **Small ≠ valuable.** An increment can be minimal, clean, additive, and reversible
  and still add nothing. Size is not a proxy for worth; value is judged by the
  capability gained (§4).

- **Pure infrastructure is not automatically useful.** A well-formed component that
  no real consumer needs is premature. Infrastructure follows demand; it does not
  precede it.

- **A deferred decision is better than premature implementation.** When a decision's
  inputs are not settled, recording the decision as deferred and pausing is
  cheaper and safer than guessing and building. Premature implementation ships
  assumptions; a deferred decision ships nothing until it is right.

- **Stopping at the correct point is a success, not a failure.** Recognizing that no
  valuable increment remains, and closing the phase, is a disciplined outcome.
  Manufacturing work to appear productive is the failure mode to avoid.

---

## 10. Recommendations for Future Phases

- **Open a phase only on a real driver.** A future implementation phase begins when
  a concrete driver exists — for example, a first real capability to gate, an
  approved decision to begin runtime integration (with its prerequisite decisions
  resolved), or a previously deferred decision becoming ready with its inputs
  settled. Do not open a phase to keep momentum.

- **Carry deferred work forward explicitly.** Maintain the list of deferred
  decisions and blocked work from the previous phase's closure. Each new phase
  begins by confirming which drivers are now available and which items remain
  deferred.

- **Resolve validation and housekeeping debt first.** Before building on prior work,
  confirm the previous phase's results on the authoritative environment and clear
  any outstanding validation or repository debt.

- **Keep the workflow and gates intact.** Apply the full workflow (§3), the Value
  Gate (§4), the deferred-decision discipline (§5), and the Batch Design Rules (§7)
  to every increment. The process does not relax as the project grows; it is what
  keeps the project safe as it grows.

- **Treat frozen decisions as stable contracts.** Architecture and design remain
  frozen across phases. Filling a deliberately deferred slot is permitted;
  reopening a settled decision requires an explicit, reviewed superseding decision,
  never a silent change.

- **Bundle enabling infrastructure with its first real use.** When a future
  capability or integration finally requires supporting infrastructure, introduce
  the infrastructure together with that concrete use, so the increment is
  independently valuable rather than a placeholder.

---

*This document is a permanent part of the CCC engineering process. It is updated
by explicit, reviewed revision, and it governs implementation work in all phases
that follow Phase 1.*
