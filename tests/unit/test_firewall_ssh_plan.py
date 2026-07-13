# SPDX-License-Identifier: MIT
"""
tests/unit/test_firewall_ssh_plan.py
------------------------------------
Contract tests for the purpose-aware installer firewall / SSH-port discovery
(ADR-0004). The bash implementation lives in ``install.sh`` between the markers
``# >>> CCC-FIREWALL-PLAN >>>`` and ``# <<< CCC-FIREWALL-PLAN <<<``; this module
extracts that block verbatim and drives it with a synthetic ``/proc`` tree
(``CCC_PROC_ROOT``) and stubbed ``ss``/``sshd``/``systemctl``/``ufw`` on PATH.

Invariants pinned here (Owner-approved policy):
  * evidence, not authorization: only evidenced local SSH port(s) are opened;
  * no conventional fallback (never a bare 22);
  * no automatic union of conflicting evidence -> fail closed;
  * disabled/inactive ssh.socket Listen is ignored; sshd -T governs;
  * override CCC_SSH_PORTS must include the active session port; invalid is fatal;
  * UFW transaction: dry-run before add, add-only order, verify, enable last;
  * fatal before enable proves `ufw --force enable` is never reached;
  * no `/udp` rule is ever produced.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_INSTALL = (_ROOT / "install.sh").read_text(encoding="utf-8")
_BLOCK = _INSTALL.split("# >>> CCC-FIREWALL-PLAN >>>", 1)[1].split(
    "# <<< CCC-FIREWALL-PLAN <<<", 1)[0]
_BASH = shutil.which("bash")

# Stubbed install.sh helpers so the block can run standalone.
_PREAMBLE = (
    'info(){ :; }\n'
    'warn(){ echo "WARN:$*" >&2; }\n'
    'step(){ :; }\n'
    'die(){ echo "DIE:$*" >&2; exit 1; }\n'
    'HTTPS_PORT="${HTTPS_PORT:-2053}"\n'
)


@unittest.skipUnless(_BASH, "bash is required")
class _Base(unittest.TestCase):
    def run_body(self, body, env=None, proc=None, path_prepend=None):
        script = _PREAMBLE + _BLOCK + "\n" + body
        e = dict(os.environ)
        e.pop("SSH_CONNECTION", None)
        e.pop("CCC_SSH_PORTS", None)
        if env:
            e.update(env)
        if proc:
            e["CCC_PROC_ROOT"] = proc
        if path_prepend:
            e["PATH"] = path_prepend + os.pathsep + e["PATH"]
        if "CCC_UFW_DEFAULTS" not in e:
            e["CCC_UFW_DEFAULTS"] = self._ufwdef("no")  # deterministic (ignore host /etc/default/ufw)
        return subprocess.run([_BASH, "-c", script], capture_output=True,
                              text=True, env=e)

    def _ufwdef(self, ipv6="no"):
        path = os.path.join(self._stubdir(), "ufwdef")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"IPV6={ipv6}\n")
        return path

    def _stubdir(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def _write(self, path, content):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(path, 0o755)

    def _proc(self, chain):
        """chain: list of (pid, comm, ppid). Returns proc root path."""
        d = self._stubdir()
        for pid, comm, ppid in chain:
            os.makedirs(os.path.join(d, str(pid), "fd"), exist_ok=True)
            self._write(os.path.join(d, str(pid), "comm"), comm)
            self._write(os.path.join(d, str(pid), "stat"),
                        f"{pid} ({comm}) S {ppid} 0 0 0 0 0 0 0 0 0 0 0\n")
        return d


class PureLogicTests(_Base):
    def test_port_validation(self):
        r = self.run_body(
            '_fw_valid_port 22 && echo v22ok; _fw_valid_port 70000 || echo bigbad; '
            '_fw_valid_port 0 || echo zerobad; _fw_valid_port abc || echo abcbad')
        self.assertIn("v22ok", r.stdout)
        self.assertIn("bigbad", r.stdout)
        self.assertIn("zerobad", r.stdout)
        self.assertIn("abcbad", r.stdout)

    def test_listen_parse_stream_only(self):
        r = self.run_body(
            "printf '0.0.0.0:22 (Stream)\\n[::]:22 (Stream)\\n"
            "/run/x.sock (Stream)\\n0.0.0.0:1222 (Stream)\\n' "
            "| _fw_parse_listen_stream | sort -un | tr '\\n' ' '")
        self.assertEqual(r.stdout.strip(), "22 1222")

    def test_override_dedupe_and_fatal(self):
        # trims per-element whitespace, dedupes, sorts
        r = self.run_body("_ssh_parse_override '1222, 22 ,1222' | tr '\\n' ' '")
        self.assertEqual(r.stdout.strip(), "22 1222")
        # embedded whitespace must NOT be concatenated -> fatal
        self.assertEqual(self.run_body("_ssh_parse_override '12 22'").returncode, 2)
        self.assertEqual(self.run_body("_ssh_parse_override '22 33'").returncode, 2)
        self.assertEqual(self.run_body("_ssh_parse_override '22,abc'").returncode, 2)
        self.assertEqual(self.run_body("_ssh_parse_override '70000'").returncode, 2)
        self.assertEqual(self.run_body("_ssh_parse_override '22,'").returncode, 2)
        self.assertEqual(self.run_body("_ssh_parse_override ',22'").returncode, 2)
        self.assertEqual(self.run_body("_ssh_parse_override '22,,33'").returncode, 2)
        # empty / whitespace-only is fatal at the function level
        self.assertEqual(self.run_body("_ssh_parse_override ''").returncode, 2)
        self.assertEqual(self.run_body("_ssh_parse_override '   '").returncode, 2)

    def test_resolve_rpi4_agreement_only_22(self):
        r = self.run_body('_resolve_ssh_plan 1 22 22 NONE')
        self.assertEqual(r.stdout.strip(), "PLAN: 22")

    def test_resolve_rpi2_agreement_only_1222(self):
        r = self.run_body('_resolve_ssh_plan 1 1222 1222 NONE')
        self.assertEqual(r.stdout.strip(), "PLAN: 1222")

    def test_resolve_conflict_no_union_fatal(self):
        r = self.run_body('_resolve_ssh_plan 1 1222 22 NONE')
        self.assertTrue(r.stdout.strip().startswith("FATAL:"))
        self.assertNotIn("22 1222", r.stdout)  # no union
        self.assertEqual(r.returncode, 1)

    def test_resolve_multiple_configured(self):
        r = self.run_body('_resolve_ssh_plan 1 1222 22,1222 NONE')
        self.assertEqual(r.stdout.strip(), "PLAN: 22 1222")  # sorted numerically

    def test_resolve_valid_override_resolves_conflict(self):
        r = self.run_body('_resolve_ssh_plan 1 1222 22 1222')
        self.assertEqual(r.stdout.strip(), "PLAN: 1222")

    def test_resolve_override_omits_anchor_fatal(self):
        r = self.run_body('_resolve_ssh_plan 1 1222 22 22')
        self.assertTrue(r.stdout.strip().startswith("FATAL:"))
        self.assertEqual(r.returncode, 1)

    def test_resolve_local_console_readable(self):
        r = self.run_body('_resolve_ssh_plan 0 "" 1222 NONE')
        self.assertEqual(r.stdout.strip(), "PLAN: 1222")

    def test_resolve_local_console_unreadable_fatal(self):
        r = self.run_body('_resolve_ssh_plan 0 "" UNREADABLE NONE')
        self.assertTrue(r.stdout.strip().startswith("FATAL:"))
        self.assertEqual(r.returncode, 1)

    def test_resolve_no_sshd_empty_plan_ok(self):
        r = self.run_body('_resolve_ssh_plan 0 "" EMPTY NONE')
        self.assertEqual(r.stdout.strip(), "PLAN:")
        self.assertEqual(r.returncode, 0)


class PersistentPortTests(_Base):
    def _sys(self, active="inactive", enabled="disabled", listen="", sshd_port=None):
        d = self._stubdir()
        self._write(os.path.join(d, "systemctl"),
                    '#!/usr/bin/env bash\n'
                    f'case "$*" in\n'
                    f'  *"is-active ssh.socket"*) echo {active};;\n'
                    f'  *"is-enabled ssh.socket"*) echo {enabled};;\n'
                    f'  *"--property=Listen"*) printf \'%s\' "{listen}";;\n'
                    '  *) echo "";;\nesac\n')
        if sshd_port is None:
            self._write(os.path.join(d, "sshd"), '#!/usr/bin/env bash\nexit 1\n')
        else:
            self._write(os.path.join(d, "sshd"),
                        '#!/usr/bin/env bash\n'
                        f'[[ "$1" == "-T" ]] && printf \'{sshd_port}\\n\'\nexit 0\n')
        self._write(os.path.join(d, "ss"), '#!/usr/bin/env bash\nexit 0\n')
        return d

    def test_socket_disabled_stale_listen_ignored_uses_sshd(self):
        # RPi2 real case: ssh.socket disabled with stale Listen=22, sshd -T=1222.
        d = self._sys(active="inactive", enabled="disabled",
                      listen="[::]:22 (Stream)\n", sshd_port="port 1222")
        r = self.run_body('_ssh_persistent_ports | tr "\\n" " "', path_prepend=d)
        self.assertEqual(r.stdout.strip(), "1222")

    def test_socket_active_governs(self):
        d = self._sys(active="active", enabled="enabled",
                      listen="0.0.0.0:1222 (Stream)\n[::]:1222 (Stream)\n",
                      sshd_port="port 22")
        r = self.run_body('_ssh_persistent_ports | tr "\\n" " "', path_prepend=d)
        self.assertEqual(r.stdout.strip(), "1222")  # socket wins, sshd -T ignored

    def test_multiple_sshd_ports(self):
        d = self._sys(sshd_port="port 22\\nport 2200")
        r = self.run_body('_ssh_persistent_ports | tr "\\n" " "', path_prepend=d)
        self.assertEqual(r.stdout.strip(), "22 2200")

    def test_sshd_unreadable(self):
        d = self._sys(sshd_port=None)  # sshd -T exits 1
        r = self.run_body('_ssh_persistent_ports', path_prepend=d)
        self.assertEqual(r.stdout.strip(), "UNREADABLE")

    def test_socket_active_unparseable_listen_fatal_marker(self):
        d = self._sys(active="active", listen="/run/only-unix.sock (Stream)\n")
        r = self.run_body('_ssh_persistent_ports', path_prepend=d)
        self.assertEqual(r.stdout.strip(), "UNREADABLE")


class SessionAncestryTests(_Base):
    def _ss(self, lines):
        d = self._stubdir()
        body = '#!/usr/bin/env bash\ncase "$*" in\n  *established*)\n'
        for ln in lines:
            body += f"    echo '{ln}'\n"
        body += '  ;;\n  *) : ;;\nesac\n'
        self._write(os.path.join(d, "ss"), body)
        return d

    _CHAIN = [(100, "bash", 90), (90, "sudo", 80), (80, "bash", 70),
              (70, "sshd", 60), (60, "sshd", 1)]

    def _run_session(self, ss_lines, start=100, env=None):
        proc = self._proc(self._CHAIN + [(200, "bash", 180), (180, "bash", 1)])
        ssdir = self._ss(ss_lines)
        body = (f'if A="$(_ssh_session_port {start})"; then rc=0; else rc=$?; fi; '
                'over=1; [[ $rc -eq 1 ]] && over=0; echo "${A}/${rc}/${over}"')
        return self.run_body(body, env=env, proc=proc, path_prepend=ssdir)

    def test_monitor_ancestor_owns_socket(self):
        r = self._run_session(
            ['ESTAB 0 0 10.0.0.5:1222 1.2.3.4:5 users:(("sshd",pid=60,fd=9))'])
        self.assertEqual(r.stdout.strip(), "1222/0/1")

    def test_child_ancestor_owns_socket(self):
        r = self._run_session(
            ['ESTAB 0 0 10.0.0.5:1222 1.2.3.4:5 users:(("sshd",pid=70,fd=9))'])
        self.assertEqual(r.stdout.strip(), "1222/0/1")

    def test_two_chain_sshd_share_port(self):
        r = self._run_session([
            'ESTAB 0 0 10.0.0.5:1222 1.2.3.4:5 users:(("sshd",pid=70,fd=9))',
            'ESTAB 0 0 10.0.0.5:1222 1.2.3.4:6 users:(("sshd",pid=60,fd=8))'])
        self.assertEqual(r.stdout.strip(), "1222/0/1")

    def test_chain_different_ports_ambiguous(self):
        r = self._run_session([
            'ESTAB 0 0 10.0.0.5:1222 1.2.3.4:5 users:(("sshd",pid=70,fd=9))',
            'ESTAB 0 0 10.0.0.5:22 1.2.3.4:6 users:(("sshd",pid=60,fd=8))'])
        self.assertEqual(r.stdout.strip(), "/2/1")  # fatal (ambiguous), over SSH

    def test_sibling_same_remote_ip_ignored(self):
        r = self._run_session(
            ['ESTAB 0 0 10.0.0.5:22 1.2.3.4:5 users:(("sshd",pid=999,fd=9))'])
        self.assertEqual(r.stdout.strip(), "/2/1")  # pid 999 not on chain -> no anchor

    def test_env_fallback_when_no_socket(self):
        r = self._run_session(['ESTAB 0 0 novalid'],
                              env={"SSH_CONNECTION": "1.2.3.4 5 10.0.0.5 1222"})
        self.assertEqual(r.stdout.strip(), "1222/0/1")

    def test_socket_env_disagreement_fatal(self):
        r = self._run_session(
            ['ESTAB 0 0 10.0.0.5:22 1.2.3.4:5 users:(("sshd",pid=70,fd=9))'],
            env={"SSH_CONNECTION": "1.2.3.4 5 10.0.0.5 1222"})
        self.assertEqual(r.stdout.strip(), "/2/1")

    def test_local_console_no_sshd_ancestor(self):
        r = self._run_session(['ESTAB 0 0 x'], start=200)
        self.assertEqual(r.stdout.strip(), "/1/0")

    def test_local_endpoint_used_not_wan(self):
        # SSH_CONNECTION carries a WAN-side client port (55000); the LOCAL server
        # port (1222) is what must be used. Socket local endpoint = 1222.
        r = self._run_session(
            ['ESTAB 0 0 10.0.0.5:1222 203.0.113.9:55000 users:(("sshd",pid=70,fd=9))'])
        self.assertEqual(r.stdout.strip(), "1222/0/1")


@unittest.skipUnless(_BASH, "bash is required")
class TransactionTests(_Base):
    """Drive _firewall_preflight + _firewall_apply with a call-logging fake ufw."""

    def _env_rpi2(self):
        proc = self._proc([(100, "bash", 90), (90, "sudo", 80),
                           (80, "bash", 70), (70, "sshd", 1)])
        d = self._stubdir()
        self._write(os.path.join(d, "ss"),
                    '#!/usr/bin/env bash\n'
                    'case "$*" in *established*) echo \'ESTAB 0 0 10.0.0.5:1222 '
                    '1.2.3.4:5 users:(("sshd",pid=70,fd=9))\';; *) : ;; esac\n')
        self._write(os.path.join(d, "systemctl"),
                    '#!/usr/bin/env bash\ncase "$*" in '
                    '*"is-active ssh.socket"*) echo inactive;; '
                    '*"is-enabled ssh.socket"*) echo disabled;; *) echo "";; esac\n')
        self._write(os.path.join(d, "sshd"),
                    '#!/usr/bin/env bash\n[[ "$1" == "-T" ]] && echo "port 1222"\nexit 0\n')
        return proc, d

    def _ufw(self, d, log, mode="happy"):
        self._write(os.path.join(d, "ufw"),
                    '#!/usr/bin/env bash\n'
                    f'echo "$*" >> "{log}"\n'
                    'case "$*" in\n'
                    '  "--dry-run allow"*) '
                    f'[[ "{mode}" == dryfail && "$*" == *"80/tcp"* ]] && exit 1; exit 0;;\n'
                    '  "allow"*) '
                    f'[[ "{mode}" == addfail && "$*" == *"80/tcp"* ]] && exit 1; exit 0;;\n'
                    '  "show added") echo "ufw allow 1222/tcp comment CCC SSH"; '
                    'echo "ufw allow 80/tcp comment HTTP"; '
                    'echo "ufw allow 2053/tcp comment CCC HTTPS";;\n'
                    '  "status") echo "1222/tcp ALLOW"; echo "80/tcp ALLOW"; '
                    'echo "2053/tcp ALLOW";;\n'
                    '  "--force enable") exit 0;;\nesac\nexit 0\n')

    def _run_tx(self, mode="happy", env=None):
        proc, d = self._env_rpi2()
        log = os.path.join(d, "ufw.log")
        open(log, "w").close()
        self._ufw(d, log, mode)
        body = "_firewall_preflight >/dev/null; _firewall_apply >/dev/null"
        e = {"CCC_FW_START_PID": "100"}
        if env:
            e.update(env)
        r = self.run_body(body, env=e, proc=proc, path_prepend=d)
        calls = pathlib.Path(log).read_text().splitlines()
        return r, calls

    def test_happy_path_rpi2_order_and_enable_last_write(self):
        r, calls = self._run_tx("happy")
        self.assertEqual(r.returncode, 0, r.stderr)
        # all dry-runs precede any live allow
        first_allow = next(i for i, c in enumerate(calls) if c.startswith("allow "))
        last_dry = max(i for i, c in enumerate(calls) if c.startswith("--dry-run"))
        self.assertLess(last_dry, first_allow)
        # SSH port opened is 1222 (never 22), HTTP 80, HTTPS 2053; no /udp anywhere
        self.assertTrue(any(c.startswith("allow 1222/tcp") for c in calls))
        self.assertFalse(any("22/tcp" in c and "1222" not in c for c in calls))
        self.assertFalse(any("/udp" in c for c in calls))
        # enable occurs, after all allows, before only the read-only status
        ei = calls.index("--force enable")
        self.assertTrue(all(calls.index(c) < ei
                            for c in calls if c.startswith("allow ")))
        self.assertEqual(calls[ei + 1], "status")

    def test_dry_run_failure_no_live_write_no_enable(self):
        r, calls = self._run_tx("dryfail")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(any(c.startswith("allow ") for c in calls))  # no live allow
        self.assertNotIn("--force enable", calls)

    def test_allow_partial_failure_no_enable_no_delete(self):
        r, calls = self._run_tx("addfail")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("allow 1222/tcp comment CCC SSH", calls)  # applied before failure
        self.assertNotIn("--force enable", calls)
        self.assertFalse(any(c.startswith("delete") for c in calls))  # never delete

    def test_override_omitting_anchor_fatal_before_any_ufw(self):
        r, calls = self._run_tx("happy", env={"CCC_SSH_PORTS": "22"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(calls, [])  # UFW never touched
        self.assertIn("omits the active SSH session port", r.stderr)

    def test_valid_override_resolves_and_applies_1222(self):
        r, calls = self._run_tx("happy", env={"CCC_SSH_PORTS": "1222"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any(c.startswith("allow 1222/tcp") for c in calls))


    def test_pre_enable_verify_failure_no_enable(self):
        # 'ufw show added' omits the SSH rule -> pre-enable verify dies before enable.
        proc, d = self._env_rpi2()
        log = os.path.join(d, "ufw.log")
        open(log, "w").close()
        self._write(os.path.join(d, "ufw"),
                    '#!/usr/bin/env bash\n'
                    f'echo "$*" >> "{log}"\n'
                    'case "$*" in\n'
                    '  "--dry-run allow"*) exit 0;;\n'
                    '  "allow"*) exit 0;;\n'
                    '  "show added") echo "ufw allow 80/tcp comment HTTP"; '
                    'echo "ufw allow 2053/tcp comment CCC HTTPS";;\n'  # 1222 MISSING
                    '  "status") echo "";;\n'
                    '  "--force enable") exit 0;;\nesac\nexit 0\n')
        body = "_firewall_preflight >/dev/null; _firewall_apply >/dev/null"
        r = self.run_body(body, env={"CCC_FW_START_PID": "100"},
                          proc=proc, path_prepend=d)
        calls = pathlib.Path(log).read_text().splitlines()
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("--force enable", calls)

    def test_evidence_changed_between_preflight_and_apply_no_write(self):
        # Stateful ss: session port 1222 at preflight, 22 at apply -> re-resolve is
        # fatal (22 not in configured {1222}) -> no UFW write, no enable.
        proc, d = self._env_rpi2()
        counter = os.path.join(d, "n")
        self._write(os.path.join(d, "ss"),
                    '#!/usr/bin/env bash\n'
                    f'n=$(cat "{counter}" 2>/dev/null || echo 0); echo $((n+1)) > "{counter}"\n'
                    'case "$*" in *established*)\n'
                    '  if [[ "$n" == "0" ]]; then '
                    'echo \'ESTAB 0 0 10.0.0.5:1222 1.2.3.4:5 users:(("sshd",pid=70,fd=9))\'; '
                    'else '
                    'echo \'ESTAB 0 0 10.0.0.5:22 1.2.3.4:5 users:(("sshd",pid=70,fd=9))\'; fi;;'
                    '\n  *) : ;; esac\n')
        log = os.path.join(d, "ufw.log")
        open(log, "w").close()
        self._ufw(d, log, "happy")
        body = "_firewall_preflight >/dev/null; _firewall_apply >/dev/null"
        r = self.run_body(body, env={"CCC_FW_START_PID": "100"},
                          proc=proc, path_prepend=d)
        calls = pathlib.Path(log).read_text().splitlines()
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("--force enable", calls)

class SessionAncestryEnvTests(_Base):
    def test_ancestor_environ_recovery_when_no_socket(self):
        # sudo stripped SSH_CONNECTION from our env and there is no correlated
        # socket; recover it from the ancestor login shell's NUL-delimited environ.
        d = self._stubdir()
        # chain: 100 bash -> 90 sudo -> 80 bash(login shell) -> 70 sshd -> 1
        for pid, comm, ppid in [(100, "bash", 90), (90, "sudo", 80),
                                (80, "bash", 70), (70, "sshd", 1)]:
            os.makedirs(os.path.join(d, str(pid), "fd"), exist_ok=True)
            self._write(os.path.join(d, str(pid), "comm"), comm)
            self._write(os.path.join(d, str(pid), "stat"),
                        f"{pid} ({comm}) S {ppid} 0 0 0 0 0 0 0 0 0 0 0\n")
        # environ on the login shell (80), NUL-delimited, carries SSH_CONNECTION.
        env = "PATH=/usr/bin\x00SSH_CONNECTION=203.0.113.9 55000 10.0.0.5 1222\x00TERM=xterm\x00"
        with open(os.path.join(d, "80", "environ"), "wb") as fh:
            fh.write(env.encode())
        ssdir = self._stubdir()
        self._write(os.path.join(ssdir, "ss"),
                    '#!/usr/bin/env bash\ncase "$*" in *established*) : ;; *) : ;; esac\n')
        body = ('if A="$(_ssh_session_port 100)"; then rc=0; else rc=$?; fi; '
                'echo "${A}/${rc}"')
        r = self.run_body(body, proc=d, path_prepend=ssdir)  # no SSH_CONNECTION in env
        self.assertEqual(r.stdout.strip(), "1222/0")


class IntegrationOrderingTests(_Base):
    """Phase-2 ordering: preflight -> HTTPS helper (--skip-ufw) -> apply.
    A fatal preflight must invoke NEITHER the helper NOR any ufw command."""

    def _phase2(self, sshd_port="port 1222", ss_estab_port="1222",
               env=None, ufw_active=False):
        proc = self._proc([(100, "bash", 90), (90, "sudo", 80),
                           (80, "bash", 70), (70, "sshd", 1)])
        d = self._stubdir()
        helog = os.path.join(d, "helper.log")
        uflog = os.path.join(d, "ufw.log")
        open(helog, "w").close()
        open(uflog, "w").close()
        self._write(os.path.join(d, "ss"),
                    '#!/usr/bin/env bash\n'
                    'case "$*" in *established*) echo \'ESTAB 0 0 10.0.0.5:'
                    + ss_estab_port + ' 1.2.3.4:5 users:(("sshd",pid=70,fd=9))\';; '
                    '*) : ;; esac\n')
        self._write(os.path.join(d, "systemctl"),
                    '#!/usr/bin/env bash\ncase "$*" in '
                    '*"is-active ssh.socket"*) echo inactive;; '
                    '*"is-enabled ssh.socket"*) echo disabled;; *) echo "";; esac\n')
        self._write(os.path.join(d, "sshd"),
                    '#!/usr/bin/env bash\n[[ "$1" == "-T" ]] && printf \''
                    + sshd_port + '\\n\'\nexit 0\n')
        # fake HTTPS helper: logs every invocation
        self._write(os.path.join(d, "ccc-apply-https-port"),
                    f'#!/usr/bin/env bash\necho "$*" >> "{helog}"\nexit 0\n')
        st = "active" if ufw_active else "inactive"
        self._write(os.path.join(d, "ufw"),
                    '#!/usr/bin/env bash\n'
                    f'echo "$*" >> "{uflog}"\n'
                    'case "$*" in\n'
                    f'  "status") echo "Status: {st}"; echo "1222/tcp ALLOW"; '
                    'echo "80/tcp ALLOW"; echo "2053/tcp ALLOW";;\n'
                    '  "--dry-run allow"*) exit 0;;\n'
                    '  "allow"*) exit 0;;\n'
                    '  "show added") echo "ufw allow 1222/tcp comment CCC SSH"; '
                    'echo "ufw allow 80/tcp comment HTTP"; '
                    'echo "ufw allow 2053/tcp comment CCC HTTPS";;\n'
                    '  "--force enable") exit 0;;\nesac\nexit 0\n')
        body = ('_firewall_preflight >/dev/null; '
                'ccc-apply-https-port apply --skip-ufw --port 2053 --hostname x >/dev/null 2>&1; '
                '_firewall_apply >/dev/null')
        e = {"CCC_FW_START_PID": "100"}
        if env:
            e.update(env)
        r = self.run_body(body, env=e, proc=proc, path_prepend=d)
        return (r,
                pathlib.Path(helog).read_text().splitlines(),
                pathlib.Path(uflog).read_text().splitlines())

    def test_happy_integration_helper_then_ufw(self):
        r, helper, ufw = self._phase2()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any("apply --skip-ufw" in h for h in helper))
        self.assertTrue(any(c.startswith("allow 1222/tcp") for c in ufw))

    def test_fatal_conflict_preflight_no_helper_no_ufw(self):
        # session on 1222 but effective config says 22 -> A not in C -> fatal preflight.
        r, helper, ufw = self._phase2(sshd_port="port 22", ss_estab_port="1222")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(helper, [], f"helper must NOT run on fatal preflight: {helper}")
        self.assertEqual(ufw, [], f"UFW must NOT be touched on fatal preflight: {ufw}")

    def test_bad_override_no_helper_no_ufw(self):
        for bad in ["", "   ", "12 22", "22 33", "22,", ",22", "22,,33", "abc", "70000"]:
            with self.subTest(override=repr(bad)):
                r, helper, ufw = self._phase2(env={"CCC_SSH_PORTS": bad})
                self.assertNotEqual(r.returncode, 0, f"{bad!r} should be fatal")
                self.assertEqual(helper, [], f"{bad!r}: helper ran")
                self.assertEqual(ufw, [], f"{bad!r}: UFW touched")


class TransactionStateTests(TransactionTests):
    def _ufw_active(self, d, log, mode="happy"):
        # like _ufw but 'status' reports active AND lists the added rules.
        self._write(os.path.join(d, "ufw"),
                    '#!/usr/bin/env bash\n'
                    f'echo "$*" >> "{log}"\n'
                    'case "$*" in\n'
                    '  "--dry-run allow"*) exit 0;;\n'
                    f'  "allow"*) [[ "{mode}" == addfail && "$*" == *"80/tcp"* ]] && exit 1; exit 0;;\n'
                    '  "status") echo "Status: active"; '
                    f'{"" if mode==("missing") else ""}'
                    'echo "1222/tcp                   ALLOW       Anywhere"; '
                    + ('' if mode == "missing" else 'echo "80/tcp                     ALLOW       Anywhere"; ')
                    + 'echo "2053/tcp                   ALLOW       Anywhere";;\n'
                    '  "show added") echo "ufw allow 1222/tcp"; echo "ufw allow 80/tcp"; '
                    'echo "ufw allow 2053/tcp";;\n'
                    '  "--force enable") exit 0;;\nesac\nexit 0\n')

    def _run_active(self, mode="happy", env=None):
        proc, d = self._env_rpi2()
        log = os.path.join(d, "ufw.log")
        open(log, "w").close()
        self._ufw_active(d, log, mode)
        body = "_firewall_preflight >/dev/null; _firewall_apply >/dev/null"
        e = {"CCC_FW_START_PID": "100"}
        if env:
            e.update(env)
        r = self.run_body(body, env=e, proc=proc, path_prepend=d)
        return r, pathlib.Path(log).read_text().splitlines()

    def test_active_partial_failure_reports_active_no_delete(self):
        r, calls = self._run_active("addfail")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("allow 1222/tcp comment CCC SSH", calls)
        self.assertNotIn("--force enable", calls)
        self.assertFalse(any(c.startswith("delete") for c in calls))
        self.assertIn("UFW remains ACTIVE", r.stderr)

    def test_active_happy_uses_status_for_verify(self):
        r, calls = self._run_active("happy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("show added", calls)  # active path uses 'status', not 'show added'
        self.assertIn("--force enable", calls)

    def test_post_enable_missing_rule_errors_no_success(self):
        # UFW active; after enable the SSH rule is absent from status -> loud error.
        proc, d = self._env_rpi2()
        log = os.path.join(d, "ufw.log")
        open(log, "w").close()
        self._write(os.path.join(d, "ufw"),
                    '#!/usr/bin/env bash\n'
                    f'echo "$*" >> "{log}"\n'
                    'case "$*" in\n'
                    '  "--dry-run allow"*) exit 0;;\n'
                    '  "allow"*) exit 0;;\n'
                    # status lists 1222 BEFORE enable (pre-enable verify passes) but
                    # OMITS it after enable is a stronger test; here omit 1222 always
                    # so both checks see it missing except pre-enable which we satisfy
                    # via show added. Use inactive pre-enable path:
                    '  "status") echo "Status: inactive"; echo "80/tcp ALLOW"; '
                    'echo "2053/tcp ALLOW";;\n'
                    '  "show added") echo "ufw allow 1222/tcp"; echo "ufw allow 80/tcp"; '
                    'echo "ufw allow 2053/tcp";;\n'
                    '  "--force enable") exit 0;;\nesac\nexit 0\n')
        body = "_firewall_preflight >/dev/null; _firewall_apply >/dev/null"
        r = self.run_body(body, env={"CCC_FW_START_PID": "100"},
                          proc=proc, path_prepend=d)
        calls = pathlib.Path(log).read_text().splitlines()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--force enable", calls)          # enable happened
        self.assertIn("MISSING", r.stderr)              # loud error
        self.assertIn("sudo ufw allow 1222/tcp", r.stderr)  # recovery guidance
        self.assertNotIn("UFW enabled — added", r.stderr)   # no success summary

    def test_l_only_change_between_preflight_and_apply_no_write(self):
        # A and C stay 1222; the runtime listener set L changes -> FW_EVID_SIG
        # differs -> apply fails closed before any UFW write.
        proc, d = self._env_rpi2()
        counter = os.path.join(d, "ln")
        self._write(os.path.join(d, "ss"),
                    '#!/usr/bin/env bash\n'
                    'case "$*" in\n'
                    '  *established*) echo \'ESTAB 0 0 10.0.0.5:1222 1.2.3.4:5 '
                    'users:(("sshd",pid=70,fd=9))\';;\n'
                    '  *)\n'
                    f'    n=$(cat "{counter}" 2>/dev/null || echo 0); echo $((n+1)) > "{counter}"\n'
                    '    echo \'LISTEN 0 128 0.0.0.0:1222 0.0.0.0:* users:(("sshd",pid=70,fd=3))\'\n'
                    '    if [[ "$n" != "0" ]]; then echo \'LISTEN 0 128 0.0.0.0:2200 '
                    '0.0.0.0:* users:(("sshd",pid=70,fd=4))\'; fi;;\n'
                    'esac\n')
        log = os.path.join(d, "ufw.log")
        open(log, "w").close()
        self._ufw(d, log, "happy")
        body = "_firewall_preflight >/dev/null; _firewall_apply >/dev/null"
        r = self.run_body(body, env={"CCC_FW_START_PID": "100"},
                          proc=proc, path_prepend=d)
        calls = pathlib.Path(log).read_text().splitlines()
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("--force enable", calls)
        self.assertIn("evidence changed", r.stderr)


class StaticTextRegressionTests(unittest.TestCase):
    """User-facing current-state text must not hardcode 'only ... 22'."""

    _ROOT = pathlib.Path(__file__).resolve().parents[2]

    def test_install_sh_reminder_summary_no_only_22(self):
        src = (self._ROOT / "install.sh").read_text(encoding="utf-8")
        for bad in ["only TCP 22", "only 22, 80", "with only 22",
                    "runs with only TCP 22", "only 22/80"]:
            self.assertNotIn(bad, src, f"stale claim in install.sh: {bad!r}")
        # the corrected phrasing is present
        self.assertIn("evidenced SSH administration port(s)", src)

    def test_pre_install_no_only_22_and_no_delete_on_sight(self):
        src = (self._ROOT / "docs" / "pre-install.md").read_text(encoding="utf-8")
        self.assertNotIn("misconfiguration to remove", src)
        self.assertNotIn("You should see **only** your SSH port", src)
        self.assertIn("alongside any pre-existing UFW", src)


class OverrideGlobSafetyTests(_Base):
    def test_glob_metacharacters_are_fatal_regardless_of_cwd(self):
        # A directory whose filenames look like ports; unquoted globbing would
        # expand '*'/'?'/'[0-9]' to these names. read -ra must NOT glob -> fatal.
        gd = self._stubdir()
        for name in ("22", "80", "2053", "1", "9"):
            open(os.path.join(gd, name), "w").close()
        for bad in ("*", "?", "[0-9]", "2*", "?2", "[1-9]22"):
            with self.subTest(override=bad):
                r = self.run_body(f'cd "{gd}" && _ssh_parse_override {bad!r}',
                                  env={"CCC_UFW_DEFAULTS": self._ufwdef("no")})
                self.assertEqual(r.returncode, 2, f"{bad!r} must be fatal, got {r.returncode}")
        # sanity: a real port still parses from the same cwd
        r = self.run_body(f'cd "{gd}" && _ssh_parse_override "1222"',
                          env={"CCC_UFW_DEFAULTS": self._ufwdef("no")})
        self.assertEqual(r.stdout.strip(), "1222")


class Ipv6AndRecoveryTests(_Base):
    def _run(self, status_lines, ipv6="no"):
        proc = self._proc([(100, "bash", 90), (90, "sudo", 80),
                           (80, "bash", 70), (70, "sshd", 1)])
        d = self._stubdir()
        self._write(os.path.join(d, "ss"),
                    '#!/usr/bin/env bash\ncase "$*" in *established*) echo '
                    '\'ESTAB 0 0 10.0.0.5:1222 1.2.3.4:5 users:(("sshd",pid=70,fd=9))\';; '
                    '*) : ;; esac\n')
        self._write(os.path.join(d, "systemctl"),
                    '#!/usr/bin/env bash\ncase "$*" in '
                    '*"is-active ssh.socket"*) echo inactive;; '
                    '*"is-enabled ssh.socket"*) echo disabled;; *) echo "";; esac\n')
        self._write(os.path.join(d, "sshd"),
                    '#!/usr/bin/env bash\n[[ "$1" == "-T" ]] && echo "port 1222"\nexit 0\n')
        log = os.path.join(d, "ufw.log")
        open(log, "w").close()
        status_echo = "".join(f'echo {ln!r}; ' for ln in status_lines)
        self._write(os.path.join(d, "ufw"),
                    '#!/usr/bin/env bash\n'
                    f'echo "$*" >> "{log}"\n'
                    'case "$*" in\n'
                    '  "--dry-run allow"*) exit 0;;\n'
                    '  "allow"*) exit 0;;\n'
                    '  "show added") echo "ufw allow 1222/tcp"; echo "ufw allow 80/tcp"; '
                    'echo "ufw allow 2053/tcp";;\n'
                    f'  "status") {status_echo}:;;\n'
                    '  "--force enable") exit 0;;\nesac\nexit 0\n')
        body = "_firewall_preflight >/dev/null; _firewall_apply >/dev/null"
        r = self.run_body(body, env={"CCC_FW_START_PID": "100",
                                     "CCC_UFW_DEFAULTS": self._ufwdef(ipv6)},
                          proc=proc, path_prepend=d)
        return r, pathlib.Path(log).read_text().splitlines()

    _V4 = ["1222/tcp                   ALLOW       Anywhere",
           "80/tcp                     ALLOW       Anywhere",
           "2053/tcp                   ALLOW       Anywhere"]
    _V6 = ["1222/tcp (v6)              ALLOW       Anywhere (v6)",
           "80/tcp (v6)                ALLOW       Anywhere (v6)",
           "2053/tcp (v6)              ALLOW       Anywhere (v6)"]

    def test_ipv6_yes_both_families_present_pass(self):
        r, _ = self._run(self._V4 + self._V6, ipv6="yes")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ipv6_yes_v6_missing_fail(self):
        r, calls = self._run(self._V4, ipv6="yes")   # only v4 present
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--force enable", calls)
        self.assertIn("(v6)", r.stderr)
        self.assertNotIn("UFW enabled — added", r.stderr)

    def test_ipv6_no_v4_present_pass(self):
        r, _ = self._run(self._V4, ipv6="no")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_http_rule_uses_http_comment(self):
        # 80/tcp absent after enable -> recovery line must use comment 'HTTP'.
        r, _ = self._run([self._V4[0], self._V4[2]], ipv6="no")  # drop 80
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sudo ufw allow 80/tcp comment 'HTTP'", r.stderr)
        self.assertNotIn("sudo ufw allow 80/tcp comment 'CCC SSH'", r.stderr)

    def test_missing_https_rule_uses_https_comment(self):
        r, _ = self._run([self._V4[0], self._V4[1]], ipv6="no")  # drop 2053
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sudo ufw allow 2053/tcp comment 'CCC HTTPS'", r.stderr)

    def test_missing_ssh_rule_uses_ssh_comment(self):
        r, _ = self._run([self._V4[1], self._V4[2]], ipv6="no")  # drop 1222
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sudo ufw allow 1222/tcp comment 'CCC SSH'", r.stderr)


class LWarningsTests(_Base):
    """_fw_l_warnings: C (comma-sep) vs L (whitespace-sep) corroboration warnings.
    L is corroboration only and never authorizes a port; warnings never change the
    resolved plan. Regression for the ShellCheck SC2086 array rewrite."""

    _CFG = "SSH port {p}/tcp is configured but not currently listening"
    _RUN = "A runtime SSH listener on {p}/tcp is not in the effective configuration"

    def _lw(self, C, L):
        return self.run_body(f"_fw_l_warnings {C!r} {L!r}")

    def test_configured_not_listening_warns_only_missing(self):
        r = self._lw("22,1222", "1222")
        self.assertEqual(r.returncode, 0)
        self.assertIn(self._CFG.format(p=22), r.stderr)
        self.assertNotIn(self._CFG.format(p=1222), r.stderr)   # shared 1222: no warning
        self.assertNotIn(self._RUN.format(p=1222), r.stderr)

    def test_runtime_not_configured_warns_only_extra(self):
        r = self._lw("1222", "22 1222")
        self.assertEqual(r.returncode, 0)
        self.assertIn(self._RUN.format(p=22), r.stderr)
        self.assertNotIn(self._RUN.format(p=1222), r.stderr)   # shared 1222: no warning
        self.assertNotIn(self._CFG.format(p=1222), r.stderr)

    def test_agreement_no_warning(self):
        r = self._lw("1222", "1222")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("configured but not currently listening", r.stderr)
        self.assertNotIn("runtime SSH listener", r.stderr)

    def test_empty_L_configured_warning_no_read_failure(self):
        r = self._lw("1222", "")
        self.assertEqual(r.returncode, 0)  # no set-e/read failure on empty L
        self.assertIn(self._CFG.format(p=1222), r.stderr)
        self.assertNotIn("runtime SSH listener", r.stderr)

    def test_warnings_do_not_change_resolved_plan(self):
        # A=1222 (session), C=1222 (sshd -T), L={22,1222} (listeners): the runtime-22
        # mismatch warns, but the resolved plan stays exactly {1222}.
        proc = self._proc([(100, "bash", 90), (90, "sudo", 80),
                           (80, "bash", 70), (70, "sshd", 1)])
        d = self._stubdir()
        self._write(os.path.join(d, "ss"),
                    '#!/usr/bin/env bash\ncase "$*" in\n'
                    '  *established*) echo \'ESTAB 0 0 10.0.0.5:1222 1.2.3.4:5 '
                    'users:(("sshd",pid=70,fd=9))\';;\n'
                    '  *) echo \'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=70,fd=3))\'; '
                    'echo \'LISTEN 0 128 0.0.0.0:1222 0.0.0.0:* users:(("sshd",pid=70,fd=4))\';;\n'
                    'esac\n')
        self._write(os.path.join(d, "systemctl"),
                    '#!/usr/bin/env bash\ncase "$*" in '
                    '*"is-active ssh.socket"*) echo inactive;; '
                    '*"is-enabled ssh.socket"*) echo disabled;; *) echo "";; esac\n')
        self._write(os.path.join(d, "sshd"),
                    '#!/usr/bin/env bash\n[[ "$1" == "-T" ]] && echo "port 1222"\nexit 0\n')
        r = self.run_body('_firewall_preflight 1>/dev/null; echo "PLAN=${FW_SSH_PORTS}"',
                          env={"CCC_FW_START_PID": "100"}, proc=proc, path_prepend=d)
        self.assertEqual(r.stdout.strip(), "PLAN=1222")             # plan unchanged
        self.assertIn(self._RUN.format(p=22), r.stderr)            # mismatch did warn


class RealSsStateEstablishedTests(_Base):
    """Field-incident regression (ADR-0004): `ss -Htnp state established` OMITS the
    State column, so the LOCAL endpoint is field 3 and field 4 is the PEER. The
    parser must read the local port (1222), never the peer (50099)."""

    # Sanitized reproduction of the real field-observed `sudo ss -Htnp state
    # established` LAYOUT on the RPi2 (State column omitted; local=field 3,
    # peer=field 4). Three concurrent sshd sessions, all on local port 1222.
    # Addresses are RFC 5737 documentation-only ranges (TEST-NET-1/2/3); the
    # operator's real addresses are NOT stored. The regression's meaning
    # (peer 50099 vs local 1222, PID ancestry) is preserved.
    _FIELD = (
        '0 0 192.0.2.140:1222 198.51.100.10:60144 '
        'users:(("sshd",pid=3115,fd=4),("sshd",pid=3029,fd=4))',
        '0 0 192.0.2.140:1222 203.0.113.45:50099 '
        'users:(("sshd",pid=3290,fd=4),("sshd",pid=3233,fd=4))',
        '0 0 192.0.2.140:1222 198.51.100.10:60691 '
        'users:(("sshd",pid=3181,fd=4),("sshd",pid=3125,fd=4))',
    )

    def _ss(self, established_lines, listen_lines=()):
        d = self._stubdir()
        est = "".join(f"echo {ln!r}; " for ln in established_lines)
        lst = "".join(f"echo {ln!r}; " for ln in listen_lines)
        self._write(os.path.join(d, "ss"),
                    '#!/usr/bin/env bash\ncase "$*" in\n'
                    f'  *established*) {est}:;;\n'
                    f'  *) {lst}:;;\nesac\n')
        return d

    def _proc_env(self, sshd_pids, ssh_conn=None):
        # chain: 100 bash -> 90 sudo -> 80 bash(login) -> <sshd_pids...> -> 1
        chain = [(100, "bash", 90), (90, "sudo", 80),
                 (80, "bash", sshd_pids[0])]
        for i, pid in enumerate(sshd_pids):
            ppid = sshd_pids[i + 1] if i + 1 < len(sshd_pids) else 1
            chain.append((pid, "sshd", ppid))
        d = self._proc(chain)
        if ssh_conn is not None:  # ancestor login shell (80) carries SSH_CONNECTION
            with open(os.path.join(d, "80", "environ"), "wb") as fh:
                fh.write(("PATH=/usr/bin\x00SSH_CONNECTION=" + ssh_conn + "\x00").encode())
        return d

    def _run(self, ssdir, proc, env=None):
        e = {}
        if env:
            e.update(env)
        body = ('if A="$(_ssh_session_port 100)"; then rc=0; else rc=$?; fi; '
                'echo "${A}/${rc}"')
        return self.run_body(body, env=e or None, proc=proc, path_prepend=ssdir)

    def test_field_incident_reads_local_not_peer(self):
        # The active session's sshd ancestry is pids 3290/3233 (line 2, peer 50099).
        # sudo stripped SSH_CONNECTION from our env; the ancestor login shell has it.
        proc = self._proc_env([3290, 3233], ssh_conn="203.0.113.45 50099 192.0.2.140 1222")
        ssdir = self._ss(self._FIELD)
        r = self._run(ssdir, proc)  # no SSH_CONNECTION in our env (sudo)
        self.assertEqual(r.stdout.strip(), "1222/0")          # LOCAL 1222, resolved
        self.assertNotIn("50099", r.stdout)                   # never the peer port

    def test_field_incident_without_env_still_local(self):
        # Even with no SSH_CONNECTION anywhere, the socket-derived local is 1222.
        proc = self._proc_env([3290, 3233])
        ssdir = self._ss(self._FIELD)
        r = self._run(ssdir, proc)
        self.assertEqual(r.stdout.strip(), "1222/0")

    def test_ancestry_correlation_picks_current_session_local(self):
        # Prove correlation, not luck: the current session (3290/3233) is on local
        # 1222 while a sibling (3115/3029) is on local 22. Must return 1222, not 22.
        field = (
            '0 0 192.0.2.140:22 198.51.100.10:60144 '
            'users:(("sshd",pid=3115,fd=4),("sshd",pid=3029,fd=4))',
            '0 0 192.0.2.140:1222 203.0.113.45:50099 '
            'users:(("sshd",pid=3290,fd=4),("sshd",pid=3233,fd=4))',
        )
        proc = self._proc_env([3290, 3233])
        ssdir = self._ss(field)
        r = self._run(ssdir, proc)
        self.assertEqual(r.stdout.strip(), "1222/0")          # current session, not sibling 22

    def test_layout_independent_state_present_or_omitted(self):
        # Same local port whether ss emits the State column (field 4) or omits it
        # under a state filter (field 3).
        omitted = ('0 0 10.0.0.5:1222 1.2.3.4:5 users:(("sshd",pid=70,fd=9))',)
        present = ('ESTAB 0 0 10.0.0.5:1222 1.2.3.4:5 users:(("sshd",pid=70,fd=9))',)
        proc = self._proc_env([70])
        for layout in (omitted, present):
            with self.subTest(layout=layout[0][:12]):
                r = self._run(self._ss(layout), proc)
                self.assertEqual(r.stdout.strip(), "1222/0")

    def test_field_incident_full_preflight_resolves_1222(self):
        # End-to-end: with the real ss output and sshd -T=1222, the preflight now
        # resolves the plan to exactly {1222} instead of failing ambiguous.
        proc = self._proc_env([3290, 3233], ssh_conn="203.0.113.45 50099 192.0.2.140 1222")
        d = self._ss(self._FIELD,
                     listen_lines=('LISTEN 0 128 0.0.0.0:1222 0.0.0.0:* '
                                   'users:(("sshd",pid=900,fd=3))',))
        self._write(os.path.join(d, "systemctl"),
                    '#!/usr/bin/env bash\ncase "$*" in '
                    '*"is-active ssh.socket"*) echo inactive;; '
                    '*"is-enabled ssh.socket"*) echo disabled;; *) echo "";; esac\n')
        self._write(os.path.join(d, "sshd"),
                    '#!/usr/bin/env bash\n[[ "$1" == "-T" ]] && echo "port 1222"\nexit 0\n')
        r = self.run_body('_firewall_preflight 1>/dev/null 2>/dev/null; echo "PLAN=${FW_SSH_PORTS}"',
                          env={"CCC_FW_START_PID": "100"}, proc=proc, path_prepend=d)
        self.assertEqual(r.stdout.strip(), "PLAN=1222")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
