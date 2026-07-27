# Closure — BL-0001: Signed Release Toolkit (OOT Capability 1)

**Status:** Closed — v1 delivered and dogfooded (2026-07-25).
**Backlog item:** BL-0001 (Medium/P2), accepted per DR-006 from the CCC-CAMP-0001 retrospective;
moved out of `docs/BACKLOG-REGISTER.md` on completion per that register's convention.
**Design record:** `ccc/pkp/knowledge/signed-release-toolkit-idd.md` (Draft IDD + §15 amendment).

---

## 1. What the item asked for

Reduce the manual toil and error surface of the ADR-0003 signed-release workflow with an **Owner
Release Guide**, a **local build/sign/verify helper**, a **post-publish verification helper**, and a
**manual GitHub publish checklist** — automating only local/read-only steps, with every irreversible
public action (push, tag push, GitHub Release asset changes) remaining a manual, Owner-controlled
checklist.

## 2. Why it was picked up now

The signed-release ceremony was executed manually **three times in one cycle** — v0.3.19, v0.3.20 and
v0.3.21. Each run required hand-recomputing both artifact digests, checking the armv7l Logical Tree
Digest, confirming the manifest binding, verifying the SSHSIG signature, and — after publishing —
re-downloading the assets and repeating the comparison. That repetition is exactly the toil BL-0001
was accepted to remove, and it produced the first real runtime evidence for the IDD.

## 3. What was delivered

**a. Toolkit brought onto the current contract.**
`ccc/owner-tools/signed-release-toolkit/signed_release_toolkit.py` existed as a Draft but had drifted
behind the platform-artifact work: it called the pre-V2 `produce_release(...)` shape and expected a
single `ccc-X.Y.Z.tar.gz`, while its tests mocked a `format_version: 1` producer (a false green). It
now conforms to IDD §4–§6: it passes the five external reuse inputs
(`--wheelhouse-armv7`, `--provenance-armv7`, `--armv7-runtime-lock`, `--image-manifest`,
`--transfer-manifest`), validates exactly the four publisher files, and performs full format-3
verification — both artifact digests, exactly `aarch64` + `armv7l`, aarch64 carrying no wheelhouse,
the armv7l `tree_digest` under scheme `ccc-logical-tree-v1`, and the manifest's
version/tag/commit binding — plus the no-NUL gate over **both** artifacts.

**b. Post-publish verification mode (IDD §15.2).**
A new read-only `verify-published` subcommand re-verifies **already-published** assets the Owner has
downloaded: signature, both digests, tree digest, manifest binding, no-NUL, and — with
`--packet-dir` — **byte-identity** between the published assets and the locally signed packet. It
performs no network mutation, upload, tag, or publish, so it stays inside the BL-0001 boundary.

**c. Owner Release Guide.**
`docs/runbooks/owner-release-guide.md` documents the ordered ceremony as actually performed:
prerequisites, milestone closure, the **wheelhouse reuse-or-rebuild criterion**, bundle integrity
verification, tagging, build+sign, local qualification, publishing, post-publish verification, and
records reconciliation — with a quick-reference table marking which steps are irreversible.
`docs/release-checklist.md` was corrected in the same pass (its standing "never publish anything
other than the **three** `ccc-X.Y.Z.*` files" rule predated the V2 model, which publishes **four**).

## 4. Validation

- **Toolkit test suite: 42 tests, all passing** (`python -m unittest`, stdlib only — no pytest, no
  network, no real keys). Coverage includes fail-closed behaviour for every IDD §9 condition, the
  producer receiving all five reuse inputs (and *not* the retired `platform` kwarg), a missing reuse
  input aborting before the producer runs, missing platform artifact, wrong `format_version`, a bad
  `tree_digest` scheme, and eight post-publish cases (pass, byte-identity pass, byte-identity
  tamper-fail, bad signature, digest mismatch, commit-binding mismatch, missing manifest, and a
  no-publish-commands assertion).
- **Defect found by those tests:** `verify_published` initially required the *producer* to be
  importable, though post-publish verification needs only `verify_signed_manifest`. Fixed by adding a
  verifier-only loader (`_load_verifier`).
- **Dogfood against a real published release (IDD §15.3):** `verify-published` was run against the
  published **v0.3.21** assets and returned `RELEASE_VERIFY=PASS`, reproducing the manual §9 result
  recorded during that release — aarch64 `b2305fc9…`, armv7l `18611b7c…`, tree digest `9d1f39e7…`,
  signature good, and all four assets byte-identical to the signed originals.

## 5. Boundary held

No tool added by this item creates or pushes a tag, uploads an asset, creates a GitHub Release,
publishes, or deploys. Signing still requires the Owner's key path and passphrase, entered manually.
The evidence report and release packet remain **local and non-authoritative**.

## 6. Outcome

The verification that previously took a sequence of manual digest/signature commands is now a single
read-only command, run once before publishing and once after. Future releases follow
`docs/runbooks/owner-release-guide.md`.

**Out of scope / not delivered (deliberate):** the broader OOT framework (design or implementation),
any automation of irreversible public actions, and device-side verification (ADR-0003 Epic B).
