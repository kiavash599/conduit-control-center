# SPDX-License-Identifier: MIT
"""Architecture-aware, installed-state APT binding (finding 2):
release/ccc_release._validate_apt_environment + _parse_apt_token + the extended
apt-packages.list grammar. Proves authorized pins must be present at the exact version
in the recorded installed set, with strict Debian architecture semantics."""
from __future__ import annotations

import pytest

from release import ccc_release as R


def _env(apt, arch="armhf"):
    return {"apt_architecture": arch, "apt": apt}


def test_native_installed_package_passes():
    R._validate_apt_environment("libssl-dev=3.0.2-0ubuntu1.15\n",
                                _env({"libssl-dev": "3.0.2-0ubuntu1.15",
                                      "build-essential": "12.9ubuntu3"}))


def test_missing_authorized_package_rejected():
    with pytest.raises(R.ReleaseError):
        R._validate_apt_environment("libssl-dev=3.0.2-0ubuntu1.15\n",
                                    _env({"other-pkg": "9.9"}))


def test_exact_armhf_qualified_match_passes():
    R._validate_apt_environment("libssl-dev:armhf=3.0.2\n",
                                _env({"libssl-dev:armhf": "3.0.2"}))


def test_unqualified_matches_native_bare_entry():
    # native bare entry is the native arch -> unqualified authorization resolves to it
    R._validate_apt_environment("libssl-dev=3.0.2\n", _env({"libssl-dev": "3.0.2"}))


def test_foreign_only_rejected_for_unqualified():
    with pytest.raises(R.ReleaseError):
        R._validate_apt_environment("libssl-dev=3.0.2\n", _env({"libssl-dev:arm64": "3.0.2"}))


def test_qualified_foreign_not_installed_rejected():
    with pytest.raises(R.ReleaseError):
        R._validate_apt_environment("libssl-dev:arm64=3.0.2\n", _env({"libssl-dev": "3.0.2"}))


def test_ambiguous_multiarch_variants_rejected():
    with pytest.raises(R.ReleaseError):
        R._validate_apt_environment(
            "libssl-dev=3.0.2\n",
            _env({"libssl-dev": "3.0.2", "libssl-dev:arm64": "3.0.2"}))


def test_epoch_version_mismatch_rejected():
    with pytest.raises(R.ReleaseError):
        R._validate_apt_environment("libssl-dev=3.0.2\n", _env({"libssl-dev": "1:3.0.2"}))


def test_exact_epoch_and_revision_match_passes():
    R._validate_apt_environment("libssl-dev=1:3.0.2-0ubuntu1.15\n",
                                _env({"libssl-dev": "1:3.0.2-0ubuntu1.15"}))


def test_wrong_apt_architecture_rejected():
    with pytest.raises(R.ReleaseError):
        R._validate_apt_environment("build-essential=12.9\n",
                                    _env({"build-essential": "12.9"}, arch="arm64"))


def test_malformed_arch_qualified_input_rejected():
    for bad in ("libssl-dev:armhf:extra=3.0.2\n", "libssl-dev:=3.0.2\n", ":armhf=3.0.2\n"):
        with pytest.raises(R.ReleaseError):
            R._validate_apt_environment(bad, _env({"libssl-dev": "3.0.2"}))


def test_parse_apt_token_rejects_multiple_colons():
    with pytest.raises(R.ReleaseError):
        R._parse_apt_token("foo:armhf:bar")


def test_apt_packages_list_grammar_accepts_arch_qualifier():
    R.validate_apt_packages_list("libssl-dev:armhf=3.0.2-0ubuntu1.15\nbuild-essential=12.9ubuntu3\n")
    with pytest.raises(R.ReleaseError):     # unpinned
        R.validate_apt_packages_list("libssl-dev\n")
    with pytest.raises(R.ReleaseError):     # malformed qualifier
        R.validate_apt_packages_list("libssl-dev:armhf:x=3.0.2\n")
