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

## SSHSIG namespace

Signing and verification use the fixed namespace `ccc-update-manifest`.

## Usage (publisher, with a private signing key by path)

```
python3 -m release.ccc_release \
    --version X.Y.Z \
    --sign-key <path-to-ed25519-private-key> \
    --source <release-source-tree>            # or: --artifact <prebuilt.tar.gz>
    --recommended-core <ver> --platform <target> \
    --out dist/
```

The tool never contacts the network. The digest algorithm and manifest layout
above are the interface contract for the Epic B device-side verifier.
