# CCC Signed Release Production (ADR-0003, Epic A)

Publisher-side tooling that produces the **Signed Object** consumed by the
Trusted Update Engine. This is release-time, off-infrastructure tooling — it is
**not** device runtime and performs no verification (device verification is
Epic B). Normative model (frozen by ADR-0003): **S2** signed manifest + a
content-addressed artifact, signed with **SSH Ed25519** (Cluster A).

## Release asset set

For version `X.Y.Z`, `ccc_release.py` emits three assets into the output dir:

| Asset | Description |
|---|---|
| `ccc-X.Y.Z.tar.gz` | the content-fixed **Release Artifact** (deployed after verification) |
| `ccc-X.Y.Z.manifest.json` | the **canonical manifest** (its exact bytes are the signed bytes) |
| `ccc-X.Y.Z.manifest.json.sig` | the **SSHSIG** Ed25519 signature over the manifest |

These three are the canonical, publisher-produced release assets. GitHub
auto-generated source archives are **not** part of the update trust model.

## Canonicalization (what makes the artifact *canonical*)

The **Canonical Release Artifact** is defined by the artifact's bytes being
**deterministic, reproducible, and platform-independent** — it is a property of
the *release process*, not of any storage backend. Git is therefore **one valid
producer** of a source tree, not the definition of canonicality.

Every producer passes its collected source tree through a **canonicalization
layer** before packing:

```
producer (--git-ref | --source | --artifact)
        │
        ▼
collected {path → bytes} tree
        │   canonicalize_tree()  — .gitattributes-driven, fail-safe
        ▼
normalized canonical tree  →  pack_tree()  →  content-fixed .tar.gz
```

**Supported `.gitattributes` subset — not full Git compatibility.** The parser
intentionally understands only the attributes that affect canonicalization:
`text`, `-text`, `binary`, and `eol` (`eol=lf` / `eol=crlf`). All other
attributes (`diff`, `filter`, `merge`, `export-ignore`, macros, negations, etc.)
are ignored. This is a deliberately minimal subset, not a general `.gitattributes`
engine; do not rely on Git behaviours outside the four attributes above.

Canonicalization rules (explicit-first, fail-safe):

- The tree's **own `.gitattributes`** is the ruleset — for the supported subset
  above, the *same* declaration Git checkout and `git archive` honour (`text` /
  `-text` / `binary` / `eol=lf`). This avoids reinventing text/binary heuristics.
- Files with **no explicit rule** fall back to a conservative content sniff (a
  NUL byte in the first 8 KiB ⇒ binary).
- The **only** transformation is CRLF/CR → **LF** for text files. Binary and
  **uncertain** files are left **byte-exact** — a misclassification can never
  corrupt a binary. The canonical artifact is **LF-only** (Linux target).

This is what prevents a Windows/CRLF working-tree checkout from contaminating a
release (the 0.3.13 `deployment/conduit.service` failure): the tracked bytes are
normalised regardless of the OS or `core.autocrlf` that produced the checkout.

### Producers

| Mode | Canonical? | Use |
|---|---|---|
| `--git-ref <ref>` / `--commit <sha>` | **Yes — preferred** | Builds from the **Git object database** at a ref (`git ls-tree` + `git cat-file`), then canonicalizes. Reproducible from a committed ref, independent of the working tree. |
| `--source <dir>` | Yes, **after** canonicalization | Any non-Git producer (CI export, air-gapped snapshot, verified tarball). Canonicalized via the tree's `.gitattributes` + content detection. Emits an informational note recommending `--git-ref` for production. |
| `--artifact <file>` | N/A (opaque) | Consumes a **prebuilt** artifact byte-exact (expert use); not re-canonicalized. |

Defense-in-depth: `.gitattributes` pins deployment artifacts to LF
(`deployment/* text eol=lf`) so the working tree, `git archive`, and this tool
all agree — one ruleset, three consumers.

## Manifest schema (format_version 1)

Fields (canonical JSON: sorted keys, no insignificant whitespace, UTF-8; the
on-disk bytes are exactly the signed bytes):

- `format_version` — manifest schema version (integer; evolvable).
- `product` — product identity; fixed `"conduit-control-center"` (authoritative).
- `version` — release semver `X.Y.Z` (authoritative; the ordering authority).
- `compatibility` — advisory, non-authorizing:
  - `recommended_conduit_core` — recommended Conduit Core version (or `null`).
  - `platform` — advisory target platform (or `null`).
- `artifact` — content binding:
  - `name` — the artifact filename (transport convenience).
  - `digest` — `{ "algorithm": "sha256", "value": "<hex>" }` over the artifact bytes.

**Invariant (§8.1):** the manifest carries **no trust material** — no keys, no
trust anchor, no signature. Publisher identity is established by *who signed*
(the trust-store principal), never by a manifest field.

## Trust-store entry

The publisher publishes its **allowed-signers** line (principal + public key);
the on-device trust store (Epic B / bootstrap) is built from it. Derive it with
`public_allowed_signers_line(key_path, principal)` — this reads the public key
only; the private signing key is never embedded, generated, or logged by this
tool (key custody is off-infrastructure).

**Format — critical:** OpenSSH allowed-signers / `trusted_publishers` files must
be plain **UTF-8, no BOM, LF** line endings. Hand-authoring on Windows (e.g.
PowerShell `Set-Content -Encoding utf8`) injects a **BOM + CRLF**, which makes
`ssh-keygen -Y verify` fail. Generate the file safely with the built-in helper,
which writes bytes (`wb`, trailing `\n`) and guarantees UTF-8/no-BOM/LF:

```
python3 -m release.ccc_release \
    --sign-key <path-to-ed25519-private-key> \
    --emit-trusted-publishers trusted_publishers \
    --identity conduit-control-center-publisher
```

## SSHSIG namespace

Signing and verification use the fixed namespace `ccc-update-manifest`.

## Usage (publisher, with a private signing key by path)

```
python3 -m release.ccc_release \
    --version X.Y.Z \
    --sign-key <path-to-ed25519-private-key> \
    --git-ref HEAD \                          # preferred; or --source <tree> / --artifact <prebuilt.tar.gz>
    --recommended-core <ver> --platform <target> \
    --out dist/
```

`--git-ref HEAD` (or `--commit <sha>`) is the preferred production mode: it
builds from the Git object database at that ref, so the artifact is reproducible
and independent of the working-tree checkout.

The tool never contacts the network. The digest algorithm and manifest layout
above are the interface contract for the Epic B device-side verifier.
