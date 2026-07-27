# Owner Release Guide — signed release, end to end

**Audience:** the Project Owner (the only person who can sign and publish).
**Scope:** the complete ceremony for a normal CCC release, in the order it is actually performed.

This guide is the **practical walkthrough**. It does not replace the governing documents — it
threads them together:

| Document | Owns |
|---|---|
| `docs/release-checklist.md` | the mandatory closure ritual + the V2 platform-artifact rules |
| `docs/adr/0003-signed-release-artifacts.md` | the signed-release architecture |
| `release/README.md` | producer reference |
| this guide | the ordered ceremony, with the commands |

> **The boundary that never moves.** Everything local and read-only may be automated. Every
> **irreversible or public** action — creating/pushing a tag, entering the signing passphrase,
> creating the GitHub Release, uploading assets — is **manual and Owner-performed**. No tool in
> this repository pushes, tags, uploads, or publishes.

---

## 0. Before you start

You need, on the Owner PC:

- a clean working tree on `main`, fully pushed, with **CI green** on the exact commit you intend to tag;
- the publisher **signing key path** (e.g. `~/.ssh/ccc_release_signing_ed25519`) and its passphrase;
- the trust file `trusted_publishers` (repo root) for verification;
- the armv7 **Phase-B bundle** (wheelhouse + provenance + runtime lock + transfer manifest) and the
  Phase-A **image manifest** — see §2 for whether you reuse an existing bundle or build a new one.

---

## 1. Close the milestone (CHANGELOG + version + docs)

Per `docs/release-checklist.md` steps 1–4:

1. Stamp `CHANGELOG.md`: rename `## [Unreleased]` to `## [X.Y.Z] — YYYY-MM-DD`, open a fresh
   `## [Unreleased]` above it.
2. Set `APP_VERSION = "X.Y.Z"` in `backend/_version.py` — the single source of truth.
3. Prove they agree and that the tree is healthy:

   ```
   python tests\invariant_suite.py --platform windows
   ruff check .
   ```

   and, in WSL (activate the venv that has the test dependencies):

   ```
   source venv/bin/activate
   python tests/invariant_suite.py --platform linux
   ```

   Both must print `INVARIANT_SUITE=PASS` with `failed=0`.
4. Update the roadmap revision/date/status and add a closure record if the milestone warrants one.

Commit these on a feature branch, open a PR, and merge only when **PR CI is green**. Then sync
`main` and confirm `git rev-parse HEAD` equals `origin/main`.

---

## 2. Decide: reuse the armv7 wheelhouse, or rebuild Phase B?

The armv7l artifact embeds a wheelhouse of third-party wheels. That closure is determined **only**
by the dependency locks — not by CCC source. So:

> **Reuse the existing Phase-B bundle if — and only if — the dependency and builder inputs are
> byte-identical to the release the bundle was built for.** Check with:

```
git diff --stat <last-release-tag> HEAD -- requirements.txt requirements-armv7.lock requirements-aarch64.lock requirements-armv7-build.lock requirements-armv7-solution.lock release/ deployment/**/Containerfile
```

**Empty output ⇒ reuse is valid.** Any output ⇒ rerun Phase A/B per
`docs/release-checklist.md` (V2 section) and `release/builder/README.md`.

Always verify the bundle before using it (this is a read-only integrity check):

```
python -m release.transfer_manifest verify --bundle <bundle>\bundle --manifest <bundle>\phase-b-bundle-transfer-manifest.json
```

Expect `TRANSFER_MANIFEST=VERIFIED file_count=34 tree_sha256=<digest>`. **Record that digest** — the
signed manifest must carry the same value. Anything else: stop.

---

## 3. Tag (manual, Owner-only)

Only on a **CI-green** commit, with a clean tree and `APP_VERSION` matching the intended version:

```
git tag -a vX.Y.Z -m "vX.Y.Z — <one-line summary>" HEAD
git push origin vX.Y.Z
```

Confirm the tag peels to the intended commit:

```
git rev-list -n1 vX.Y.Z
```

> A public tag must keep meaning exactly one object. If a tag was pushed in error **before any
> assets were published**, it may be recreated; once assets exist, never move it — cut a new version.

---

## 4. Build and sign (passphrase is manual)

Run the producer from the tag. Substitute your key path and the bundle/image-manifest paths:

```
python release\ccc_release.py --version X.Y.Z --sign-key <key-path> --git-ref vX.Y.Z ^
  --wheelhouse-armv7 <bundle>\bundle\wheelhouse-armhf ^
  --provenance-armv7 <bundle>\bundle\wheelhouse-armv7.json ^
  --armv7-runtime-lock <bundle>\bundle\requirements-armv7.lock ^
  --image-manifest <phase-a>\image-manifest.json ^
  --transfer-manifest <bundle>\phase-b-bundle-transfer-manifest.json ^
  --recommended-core 2.0.0 --out dist
```

(In PowerShell run it as a **single line** — `^` is a `cmd` continuation; PowerShell uses a backtick.)

It re-verifies the transfer manifest, prompts once for your passphrase, and writes exactly four
files into `dist\`:

```
ccc-X.Y.Z-aarch64.tar.gz
ccc-X.Y.Z-armv7l.tar.gz
ccc-X.Y.Z.manifest.json
ccc-X.Y.Z.manifest.json.sig
```

The producer **fails closed before writing any bytes** if an input is missing or mismatched.

---

## 5. Qualify locally (before publishing)

Verify the four assets against the signed manifest. The Signed Release Toolkit does the whole
check in one read-only command (see `ccc/owner-tools/signed-release-toolkit/`):

```
python signed_release_toolkit.py verify-published --version X.Y.Z --published-dir dist --allowed-signers trusted_publishers --repo . --git-ref vX.Y.Z
```

Expect `RELEASE_VERIFY=PASS`. It asserts every gate:

- each artifact's recomputed SHA-256 equals its signed manifest entry;
- exactly `aarch64` + `armv7l`, with the aarch64 artifact carrying **no** wheelhouse;
- the armv7l wheelhouse `tree_digest` under scheme `ccc-logical-tree-v1` (compare it to §2);
- the manifest binds `version`, `source.tag`, `source.commit`, and `format_version: 3`;
- the SSHSIG signature verifies against `conduit-control-center-publisher`;
- no NUL bytes in text/script members of either artifact.

**Record both artifact SHA-256 values** — you will re-check them after publishing.

---

## 6. Publish (irreversible — manual)

Only when §5 is green. Upload **exactly the four** assets, nothing else:

```
gh release create vX.Y.Z "dist\ccc-X.Y.Z-aarch64.tar.gz" "dist\ccc-X.Y.Z-armv7l.tar.gz" "dist\ccc-X.Y.Z.manifest.json" "dist\ccc-X.Y.Z.manifest.json.sig" --verify-tag --title "vX.Y.Z" --notes "<release notes>"
```

`--verify-tag` refuses if the local tag does not match the remote.

**Never publish:** `trusted_publishers` / `allowed_signers`, the signing private key, the Phase-B
bundle, locks, or any file beyond the four `ccc-X.Y.Z.*` assets.

---

## 7. Verify what is actually published

Download the published assets back and re-verify — this proves the live bytes are the signed bytes:

```
gh release download vX.Y.Z --dir dist\published-verify --pattern "ccc-X.Y.Z*"

python signed_release_toolkit.py verify-published --version X.Y.Z --published-dir dist\published-verify --packet-dir dist --allowed-signers trusted_publishers --repo . --git-ref vX.Y.Z
```

`--packet-dir` adds the decisive check: the four published files must be **byte-identical** to the
locally signed originals. Expect `RELEASE_VERIFY=PASS` with `byte-identity ... PASS`.

If anything fails here, treat the release as suspect: delete the release assets and investigate
before announcing.

---

## 8. Reconcile the records

After a successful publish:

- `docs/PROJECT-STATUS.md` — Current Release row, Release Timeline row, Next Recommended Action;
- `docs/roadmap/CCC_Product_Roadmap_v1.md` — Release History row + a Revision History entry
  (bump the header revision/date/status);
- close or update any incident/backlog item the release resolves.

Commit these as a normal `docs:` commit on `main`. The tag stays where it is.

---

## 9. Deploy to devices (optional but recommended)

The dashboard's **Software Updates → Install Update** path consumes the signed release: it verifies
the signature and digest **on the device** before installing, and auto-rolls back on failure.
Updating a Raspberry Pi through it is the best end-to-end proof the release is consumable.

Expect a short outage (~1–2 minutes) while the service restarts; the version shown in an
already-open page only refreshes after you reload it.

---

## Quick reference

| Step | Command | Irreversible? |
|---|---|---|
| Bundle integrity | `python -m release.transfer_manifest verify …` | no |
| Tag | `git tag -a vX.Y.Z …` / `git push origin vX.Y.Z` | **yes** |
| Build + sign | `python release\ccc_release.py --version …` | no (local) |
| Qualify | `signed_release_toolkit.py verify-published --published-dir dist …` | no |
| Publish | `gh release create vX.Y.Z …` | **yes** |
| Post-publish verify | `signed_release_toolkit.py verify-published --packet-dir dist …` | no |
