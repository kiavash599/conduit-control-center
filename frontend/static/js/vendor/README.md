# Vendored third-party libraries

Files in this directory are third-party code vendored **verbatim** — no edits,
no build step — and pinned by SHA-256. Do not edit these files. To update, drop
in the new file from the upstream release and update both the SHA recorded here
and the pinned constant in `tests/unit/test_vendor_qrcodegen.py`.

## qrcodegen.js

- Library:  Nayuki "QR Code generator" (TypeScript/JavaScript port)
- Flavor:   precompiled **ES6** release asset `qrcodegen-v1.8.0-es6.js`, vendored here as `qrcodegen.js`
- Version:  v1.8.0
- License:  MIT
- Source:   https://github.com/nayuki/QR-Code-generator/releases/tag/v1.8.0
- Project:  https://www.nayuki.io/page/qr-code-generator-library
- SHA-256:  `6a1116192ed1dd67fa1bf31e77f5817103d71c23bbac24c382e698b7668bdd01`

### Why vendored (not a CDN)

CCC enforces a strict Content-Security-Policy (`script-src 'self'`, no
`unsafe-eval` / `unsafe-inline`) and performs no runtime downloads. The file is
served same-origin from `/static/js/vendor/` and loaded as a classic script that
exposes the global `qrcodegen` namespace. It is pure computation — no DOM access
and no `eval` / `Function` / `document.write` — so CCC renders the QR onto its
own `<canvas>`. The precompiled JavaScript is published only as a GitHub Release
asset (it is not in the project's source tree), so this is the official
no-build-step artifact.

### Integrity

Enforced in CI by `tests/unit/test_vendor_qrcodegen.py`: SHA-256 match
(tamper-evident, SRI-equivalent without a CDN) plus a CSP-safety scan for
dynamic-code / DOM-injection constructs.
