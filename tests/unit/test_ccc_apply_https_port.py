# SPDX-License-Identifier: MIT
"""
tests/unit/test_ccc_apply_https_port.py
---------------------------------------
Tests for the bounded ``--skip-ufw`` mode added to
``deployment/bin/ccc-apply-https-port`` (ADR-0004).

Contract:
  * default behaviour is UNCHANGED (helper reconciles the CCC HTTPS UFW rule);
    update.sh keeps using the default and is therefore unaffected;
  * install.sh passes ``--skip-ufw``; in that mode the helper still renders /
    validates / reloads nginx but performs NO ufw_reconcile / ufw allow / delete;
  * the argv contract is argv-only (no shell), unchanged.
"""
from __future__ import annotations

import importlib.util
import pathlib
import types
import unittest
from importlib.machinery import SourceFileLoader

_HELPER = (pathlib.Path(__file__).resolve().parents[2]
           / "deployment" / "bin" / "ccc-apply-https-port")


def _load():
    loader = SourceFileLoader("ccc_apply_https_port", str(_HELPER))
    spec = importlib.util.spec_from_loader("ccc_apply_https_port", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class SkipUfwModeTests(unittest.TestCase):
    def setUp(self):
        self.m = _load()
        self.calls = []
        self.m.render = lambda port, hostname: "SITE"
        self.m.atomic_write = lambda path, content: self.calls.append(("write", path))
        self.m.ensure_symlink = lambda: self.calls.append(("symlink",))
        self.m.nginx_test = lambda: True
        self.m.nginx_reload = lambda: self.calls.append(("reload",))
        self.m.ufw_reconcile = lambda port: self.calls.append(("ufw_reconcile", port))
        _os = self.m.os
        self.m.os = types.SimpleNamespace(
            path=types.SimpleNamespace(exists=lambda p: False, dirname=_os.path.dirname))

    def _apply(self, **kw):
        args = types.SimpleNamespace(port="443", hostname="ccc.example.com", **kw)
        self.m.cmd_apply(args)

    def test_skip_ufw_does_not_reconcile(self):
        self._apply(skip_ufw=True)
        self.assertFalse(any(c[0] == "ufw_reconcile" for c in self.calls),
                         f"ufw_reconcile must NOT run under --skip-ufw; calls={self.calls}")
        self.assertTrue(any(c[0] == "reload" for c in self.calls))
        self.assertTrue(any(c[0] == "write" for c in self.calls))

    def test_default_still_reconciles(self):
        self._apply(skip_ufw=False)
        self.assertTrue(any(c[0] == "ufw_reconcile" for c in self.calls),
                        "default behaviour must reconcile the CCC HTTPS UFW rule")

    def test_missing_flag_defaults_to_reconcile(self):
        args = types.SimpleNamespace(port="443", hostname="ccc.example.com")
        self.m.cmd_apply(args)
        self.assertTrue(any(c[0] == "ufw_reconcile" for c in self.calls))


class ArgparseContractTests(unittest.TestCase):
    def test_apply_accepts_skip_ufw_flag(self):
        src = _HELPER.read_text(encoding="utf-8")
        self.assertIn('p_apply.add_argument("--skip-ufw", action="store_true")', src)
        self.assertIn(
            "subprocess.run(argv, capture_output=True, text=True, check=False)", src)

    def test_update_sh_uses_default_helper_invocation(self):
        upd = (pathlib.Path(__file__).resolve().parents[2] / "update.sh").read_text(
            encoding="utf-8")
        if "ccc-apply-https-port" in upd:
            self.assertNotIn("--skip-ufw", upd,
                             "update.sh must keep the default helper behaviour")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
