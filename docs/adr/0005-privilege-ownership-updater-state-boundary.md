# ADR-0005 — Privilege, runtime, and transactional updater boundaries (v0.3.19 Epics 1+2)

- Status: Accepted (implemented; NOT yet hardware-qualified, tagged, signed, or released)
- Date: 2026-07-21
- Supersedes: none. Scoped correction of ownership/state assumptions in
  `install.sh`, `update.sh`, `deployment/bin/ccc-update-apply`,
  `deployment/bin/ccc-restore-apply`, and the systemd unit.

## Context

An Epic-0 security audit found that the privileged trust boundary was not
transitively root-owned: `install.sh`/`update.sh` ran
`chown -R conduit-cc:conduit-cc /opt/conduit-cc`, so the interpreter, imported
modules, and shell scripts that ROOT helpers (`ccc-restore-apply`,
`ccc-update-apply`) execute were Unix-writable by the service account.
`ProtectSystem=strict` constrains the service *process*, not the ownership
model, and the same account has other host activity. Separately, the root
updater wrote status through a fixed-name temp file with a plain `open(.., "w")`
inside the service-writable StateDirectory (a symlink/file-clobber vector),
cleanup deleted by filename prefix, `.env` had two conflicting mode contracts
(install 0600 vs restore 0640) and a non-atomic password writer, and the
verifier's trust anchor (`/opt/conduit-cc/trust/allowed_signers`) had no
provisioning path.

## Decision

**Trust closure is transitively root-owned.** Everything root executes — code,
helpers, the interpreter, the `venv` — is `root:root`, dirs `0755` / files
`0644` (helpers `0755`), never writable by `conduit-cc`. Deploy/rollback use
`rsync --chown=root:root --chmod=D0755,F0644` (rsync `-a` alone preserves source
uid/gid, so ownership is normalized explicitly, not assumed from root execution)
plus a fail-closed ownership-verification pass. The broad recursive service-account
`chown` of `APP_DIR` is removed and regression-gated. The service account writes
ONLY paths outside the executable closure: `/etc/conduit-cc` (db, config, `.env`),
`/var/log/...`, and the published status file.

**State boundary is two directories.** Private updater state (locks, work trees,
worker log, per-attempt ownership records) lives in `root:root 0700`
`/var/lib/ccc-update`; the minimal status document is PUBLISHED into
`root:root 0755` `/var/lib/ccc-status` (files `root:conduit-cc 0640`). The
service reads the public status and can touch nothing on either side.
`backend/priv_state.py` is the single symlink-safe atomic publisher and the
record-based deletion-authority mechanism (no filename-prefix authority). The
systemd unit grants `ReadWritePaths` for both dirs only so root-in-namespace can
write them; Unix ownership is the boundary, `ProtectSystem=strict` is
defense-in-depth.

**`.env` has one contract.** `conduit-cc:conduit-cc`, `0600`, regular-file-only,
atomic replacement (`backend/env_file.py`). Restore no longer widens to `0640`;
the password endpoint writes atomically. Because the DDNS job consumes this file
with Bash `source`, installer scalars are single-quoted and line/quote breakout
is rejected before publication; per-key updates enforce exact username/bcrypt
grammars and never place values in argv or the environment.

**Trust anchor is provisioned out-of-band.** `ccc-provision-trust-anchor`
validates and atomically installs the anchor and refuses any candidate resolving
inside the application or updater-state tree (circular-trust prevention). The
anchor is never shipped in, downloaded from, or derived from a release.

**The Python runtime is immutable and selector-based.** Dependencies are built
and fully validated in an attempt-owned staging directory, atomically published
under `/opt/conduit-cc/.venvs/<runtime-id>`, and activated only by an atomic
`venv -> .venvs/<runtime-id>` selector flip. Pip never mutates the active or
previous runtime. Fresh installs use the same candidate/finalize gate and only
publish their first selector after validation.

For the one-time transition, v0.3.18's service-owned application root is
tightened to `root:root 0755` before the immutable store is created; the staged
bootstrap runner imports no old application code. The former diagnostic
`pip freeze` of the legacy runtime is removed because it was not a rollback
input and would execute service-controlled code as root. While the old service
is live, a non-mutating recursive shape gate rejects hardlinks, foreign object
types and escaping symlinks. After the service stops and conversion intent is
durable, the venv is recursively secured, fully revalidated and converted. Full
source-tree ownership is normalized by deploy and by rollback restore.

**Every update is a write-ahead transaction.** A root-only mode-0600 record
binds attempt id, signed source commit/tag, ordered phase history and immutable
facts. Every shared-state mutation has an intent/completion boundary. Startup
reconciles an unfinished transaction before beginning another: pre-downtime
candidate publication is reconciled against the transaction-bound candidate id
and attempt id, retaining a fully live-validated final candidate for reuse while
discarding only exact attempt-owned staging/partial output; post-downtime state
is rolled back using both the durable record and observed selector/service state.
The candidate manifest is durably `validated` before the atomic directory rename,
and every regular candidate file plus its directories is flushed first, so no
crash can expose a final runtime with an intermediate manifest state or a durable
selector pointing at dependency bytes that existed only in writeback cache.
Rollback restores the selector before code, requires the exact previous version
to become healthy, and is itself checkpointed and resumable.
The durable terminal-success record outranks process-local flags, backup
retention mutates history only after success, and rollback recognizes already-
restored disk state rather than replaying a completed selector mutation.
Backup creation itself has an attempt-bound intent and collision-free path;
partial cleanup and retention accept only transaction-record-authorized,
revalidated directories. Both managed systemd units have one atomic writer
inside `deploy_intent`; their exact bytes/presence and the Conduit drop-in
directory presence are recorded before mutation and restored before the
rollback service-start checkpoint. The former M2 writer is verification-only.

**Update and restore are one serialized lifecycle boundary.** Both public
helpers acquire the same root-only `lifecycle.lock` and reject either fixed
transient unit while it is active or transitioning. Restore secrets cross from
the short-lived public helper to `ccc-restore.service` only through
attempt-recorded root-owned mode-0600 FIFOs; ciphertext/passphrase never enter
argv, environment variables or regular files. The worker revalidates its exact
attempt record, FIFO inode identity and frame before acknowledgement. Restore
therefore no longer relies on a double-forked descendant surviving service
shutdown, and `conduit-cc.service` uses `KillMode=control-group` so all
service-created descendants are terminated before root mutates executable
state.

**The v0.3.19 first-transition reserve has explicit success and failure
lifecycles.**
The Owner bootstrap writes a durable reserve record before creating staging,
retains that root-only staging tree after a successful update, and marks it
`ready` only after the bound update transaction reaches `success`. Qualification
ends with an explicit source-identity-bound `reserve-accept`; it writes
`acceptance_intent` before deleting exactly the recorded directory and is safe
to resume after interruption. A pre-downtime `diagnostic_failure` may instead
use `reserve-discard-failed`: it additionally proves that `downtime_started`
was never reached, binds the same source identity and legacy baseline, writes
`failure_discard_intent`, and deletes only the exact recorded tree. Successful,
rolled-back, post-downtime, foreign, and substituted reserves are refused. No
filename/prefix sweep authorizes deletion.

**Runtime smoke checks follow the signed dependency contract.** Native modules
that are required by the platform closure are imported explicitly. PyYAML is
the documented exception: its pure-Python implementation is supported, so the
probe exercises public `safe_load`/`safe_dump` behavior without requiring the
optional `yaml._yaml` accelerator. Candidate publication and collision reuse
run the same committed probe set.

## Consequences

- `ccc-restore-apply` and `ccc-update-apply` now execute a root-owned interpreter
  and root-owned code; a service-account compromise cannot rewrite what root runs.
- The v0.3.18 real-directory `/opt/conduit-cc/venv` is converted once, inside
  the stopped-service transaction, into the immutable store and remains locally
  reversible until the new release is accepted.
- Status/outcome consumers (`backend/api/update.py`, `backend/api/backup.py`) read
  the new public path; the private path is invisible to the service.
- Restore/update cannot overlap; systemd, the shared mutex and record-authorized
  attempt state provide independent containment and recovery boundaries.

## Scope / non-goals

This ADR closes the combined Epic 1+2 implementation unit, including immutable
runtimes, fresh-install candidate activation, write-ahead recovery, and the
first-transition reserve lifecycle. It does not claim device qualification,
tagging, signing, upload, or release. Those actions remain blocked on the single
authoritative invariant gate plus the RPi2/RPi4 acceptance matrix in the
v0.3.19 runbook. The immutable v0.3.18 tag and its failed-release evidence are
unchanged; v0.3.19 remains unreleased.
