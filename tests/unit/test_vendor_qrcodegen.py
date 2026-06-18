# SPDX-License-Identifier: MIT
"""C6d Slice 3: integrity + CSP-safety of the vendored Nayuki qrcodegen library.

Pure file-content assertions (no app import, no runtime). The vendored file is
pinned by SHA-256 (tamper-evident, SRI-equivalent without a CDN) and asserted to
be free of dynamic-code / DOM-injection constructs so it stays compatible with
the strict CSP (script-src 'self'; no unsafe-eval / unsafe-inline). Also checks
the provenance README and that the nginx CSP line is unchanged.
"""
from __future__ import annotations

import hashlib
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
VENDOR = ROOT / "frontend" / "static" / "js" / "vendor" / "qrcodegen.js"
README = ROOT / "frontend" / "static" / "js" / "vendor" / "README.md"
NGINX = ROOT / "deployment" / "conduit-cc.nginx"

EXPECTED_SHA256 = "6a1116192ed1dd67fa1bf31e77f5817103d71c23bbac24c382e698b7668bdd01"
EXPECTED_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; frame-ancestors 'none'"
)


def test_vendor_qrcodegen_sha256_pinned():
    assert VENDOR.exists(), "vendored qrcodegen.js missing"
    digest = hashlib.sha256(VENDOR.read_bytes()).hexdigest()
    assert digest == EXPECTED_SHA256, digest


def test_vendor_qrcodegen_is_csp_safe():
    src = VENDOR.read_text(encoding="utf-8")
    for bad in ("eval(", "Function(", "document.write", ".innerHTML",
                "import(", "require("):
        assert bad not in src, bad


def test_vendor_readme_records_provenance():
    assert README.exists(), "vendor/README.md missing"
    txt = README.read_text(encoding="utf-8")
    assert EXPECTED_SHA256 in txt
    assert "v1.8.0" in txt
    assert "nayuki" in txt.lower()
    assert "MIT" in txt


def test_csp_unchanged():
    nginx = NGINX.read_text(encoding="utf-8")
    assert EXPECTED_CSP in nginx
