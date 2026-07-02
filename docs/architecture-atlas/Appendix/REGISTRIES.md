# CCC Architecture Atlas — Frozen Registries

**Release:** v1.0.0-rc1 · **Verified Revision:** v0.3.12 (all entities)
These are the frozen source-of-truth registries. Revised only to correct objective
factual errors or under an approved ADR / repository change.

---

## Capability Registry

Fields: ID · Name · Class · Scope · Status · Confidence · Primary Implementer (SUB).

| ID | Name | Class | Scope | Status | Conf | SUB |
|---|---|---|---|---|---|---|
| CAP-001 | Control Conduit service lifecycle | Runtime | Mixed | Current | Verified | SUB-001 |
| CAP-002 | Read Conduit node status | Runtime | Mixed | Current | Verified | SUB-001 |
| CAP-003 | Manage Conduit configuration | Runtime | Mixed | Current | Verified | SUB-001 |
| CAP-004 | Pair Conduit node | Runtime | Mixed | Not-Implemented | Verified | SUB-001 |
| CAP-005 | Manage Personal Mode compartment | Runtime | Mixed | Current | Verified | SUB-002 |
| CAP-006 | Provide Ryve claim QR | Runtime | Mixed | Current | Verified | SUB-003 |
| CAP-007 | Report Conduit traffic | Runtime | Mixed | Current | Verified | SUB-004 |
| CAP-008 | Report system metrics | Runtime | Mixed | Current | Verified | SUB-009 |
| CAP-009 | Provide contribution advice | Runtime | Internal | Current | Verified | SUB-005 |
| CAP-010 | Tail Conduit service log | Runtime | Mixed | Current | Verified | SUB-001 |
| CAP-011 | Report DDNS status | Runtime | Internal | Current | Verified | SUB-011 |
| CAP-012 | Maintain public DNS record (Cloudflare DDNS) | Background | External | Current | Verified | SUB-011 |
| CAP-013 | Create & inspect encrypted backup | Runtime | Internal | Current | Verified | SUB-006 |
| CAP-014 | Restore CCC state from backup | Runtime | Mixed | Current | Verified | SUB-006 |
| CAP-015 | Self-update CCC (One-Click Update) | Runtime | Mixed | Current · Maintenance-Only | Verified | SUB-007 |
| CAP-016 | Select Cloudflare-compatible HTTPS port | Deployment | Mixed | Current | Verified | Deployment View (excluded) |
| CAP-017 | Authenticate & protect admin access | Runtime | Internal | Current | Verified | SUB-008 |
| CAP-018 | Manage application settings | Runtime | Internal | Current | Verified | SUB-009 |
| CAP-019 | Provide operator web interface | Runtime | Internal | Current | Verified | SUB-009 |
| CAP-020 | Report health / liveness | Runtime | Internal | Current | Verified | SUB-009 |
| CAP-021 | Evaluate required capabilities | N/A | Internal | Current · Pure · Unwired | Verified | SUB-010 |

---

## Subsystem Registry

Fields: ID · Name · Architectural Responsibility · Primary Capabilities · Status.

| ID | Name | Responsibility | Primary CAP | Status |
|---|---|---|---|---|
| SUB-001 | Conduit Control | Manage, configure, and observe the external Conduit node | 001,002,003,004,010 | Current |
| SUB-002 | Personal Mode | Own the personal-compartment identity lifecycle | 005 | Current |
| SUB-003 | Ryve Claim | Produce/serve the key-grade Ryve station claim | 006 | Current |
| SUB-004 | Traffic | Account, persist, and report Conduit traffic | 007 | Current |
| SUB-005 | Contribution Advisor | Compute read-only contribution guidance | 009 | Current |
| SUB-006 | Backup & Restore | Protect and recover CCC state via encrypted backups | 013,014 | Current |
| SUB-007 | Trusted Update Engine | Dashboard-driven CCC self-update, end to end | 015 | Current · Maintenance-Only |
| SUB-008 | Authentication & Access Control | Authenticate and protect admin access | 017 | Current |
| SUB-009 | Application Runtime Platform | Compose/host the app runtime + cross-cutting services | 008,018,019,020 | Current |
| SUB-010 | Capability Evaluation | Pure required-capability sufficiency evaluation | 021 | Current · Pure · Unwired |
| SUB-011 | Dynamic DNS | Keep the public DNS record current and report state | 011,012 | Current |

---

## Component Registry

Fields: ID · Name · Parent SUB · Owned Files (Repository Evidence). Status = Current
unless noted. Verified Revision = v0.3.12.

| ID | Name | SUB | Owned Files |
|---|---|---|---|
| CMP-001 | Conduit Control API | SUB-001 | `backend/api/conduit.py` |
| CMP-002 | Conduit Status API | SUB-001 | `backend/api/status.py` |
| CMP-003 | Conduit Log API | SUB-001 | `backend/api/logs.py` |
| CMP-004 | Conduit Adapter | SUB-001 | `backend/conduit/adapter.py` |
| CMP-005 | Conduit Config Validator | SUB-001 | `backend/conduit/config_validation.py` |
| CMP-006 | Conduit Config Helper | SUB-001 | `deployment/bin/ccc-apply-conduit-config` |
| CMP-007 | Status UI | SUB-001 | `frontend/static/js/status.js` |
| CMP-008 | Conduit Config UI | SUB-001 | `frontend/static/js/conduit_config.js` |
| CMP-009 | Logs UI | SUB-001 | `frontend/static/js/logs.js` |
| CMP-010 | Regions UI | SUB-001 | `frontend/static/js/regions.js` |
| CMP-011 | Personal API | SUB-002 | `backend/api/personal.py` |
| CMP-012 | Personal Adapter | SUB-002 | `backend/conduit/personal.py` |
| CMP-013 | Personal Compartment Helper | SUB-002 | `deployment/bin/ccc-personal-compartment` |
| CMP-014 | Personal UI | SUB-002 | `frontend/static/js/personal.js` |
| CMP-015 | Ryve API | SUB-003 | `backend/api/ryve.py` |
| CMP-016 | Ryve Adapter | SUB-003 | `backend/conduit/ryve.py` |
| CMP-017 | Ryve Claim Helper | SUB-003 | `deployment/bin/ccc-ryve-claim` |
| CMP-018 | Ryve UI | SUB-003 | `frontend/static/js/ryve.js` |
| CMP-019 | Traffic Read API | SUB-004 | `backend/api/traffic.py`; `backend/api/metrics.py` *(traffic-counters facet)* |
| CMP-020 | Traffic Collector | SUB-004 | `backend/traffic/collector.py` |
| CMP-021 | Traffic Accounting (Pure) | SUB-004 | `backend/traffic/accounting.py` |
| CMP-022 | Traffic Retention | SUB-004 | `backend/traffic/retention.py` |
| CMP-023 | Traffic Reads | SUB-004 | `backend/traffic/reads.py` |
| CMP-024 | Traffic Repository | SUB-004 | `backend/traffic/repository.py` |
| CMP-025 | Traffic UI | SUB-004 | `frontend/static/js/traffic.js` |
| CMP-026 | Traffic History UI | SUB-004 | `frontend/static/js/traffic_history.js` |
| CMP-027 | Advisor API | SUB-005 | `backend/api/advisor.py` |
| CMP-028 | Advisor Engine (Pure) | SUB-005 | `backend/advisor/engine.py` |
| CMP-029 | Advisor UI | SUB-005 | `frontend/static/js/advisor.js` |
| CMP-030 | Backup API | SUB-006 | `backend/api/backup.py` |
| CMP-031 | Backup Archiver | SUB-006 | `backend/backup/archiver.py` |
| CMP-032 | Backup Collector | SUB-006 | `backend/backup/collector.py` |
| CMP-033 | Backup Crypto | SUB-006 | `backend/backup/crypto.py` |
| CMP-034 | Key-Exclusion Guard | SUB-006 | `backend/backup/exclusion.py` |
| CMP-035 | Backup Manifest | SUB-006 | `backend/backup/manifest.py` |
| CMP-036 | Restore Primitive | SUB-006 | `backend/backup/restore.py` |
| CMP-037 | Archive Codec | SUB-006 | `backend/backup/archive.py` |
| CMP-038 | Restore Helper | SUB-006 | `deployment/bin/ccc-restore-apply` |
| CMP-039 | Backup UI | SUB-006 | `frontend/static/js/backup.js` |
| CMP-040 | Update API | SUB-007 | `backend/api/update.py` |
| CMP-041 | Update Helper | SUB-007 | `deployment/bin/ccc-update-apply` |
| CMP-042 | Updater Script | SUB-007 | `update.sh` |
| CMP-043 | Update UI | SUB-007 | `frontend/static/js/updates.js` |
| CMP-044 | Auth API | SUB-008 | `backend/api/auth.py`; `backend/api/settings.py` *(password facet)* |
| CMP-045 | Login/Credential | SUB-008 | `backend/auth/login.py` |
| CMP-046 | Session Store | SUB-008 | `backend/auth/sessions.py` |
| CMP-047 | Lockout | SUB-008 | `backend/auth/lockout.py` |
| CMP-048 | Cookie Helper | SUB-008 | `backend/auth/cookies.py` |
| CMP-049 | Unlock CLI (Administration) | SUB-008 | `scripts/ccc-unlock` |
| CMP-050 | Login UI | SUB-008 | `frontend/static/js/login.js` |
| CMP-051 | Composition Root | SUB-009 | `backend/main.py` |
| CMP-052 | Configuration | SUB-009 | `backend/config.py` |
| CMP-053 | Database | SUB-009 | `backend/database.py` |
| CMP-054 | Shared Dependencies | SUB-009 | `backend/dependencies.py` |
| CMP-055 | Health Endpoint | SUB-009 | `backend/api/health.py` |
| CMP-056 | Web Shell Server | SUB-009 | `backend/pages.py` |
| CMP-057 | System Metrics Endpoint | SUB-009 | `backend/api/metrics.py` *(system-metrics facet)* |
| CMP-058 | Settings Endpoint (Theme/Config) | SUB-009 | `backend/api/settings.py` *(theme + config-info facets)* |
| CMP-059 | Client Runtime | SUB-009 | `frontend/static/js/api.js` |
| CMP-060 | Client Bootstrap | SUB-009 | `frontend/static/js/app.js` |
| CMP-061 | Dashboard Shell | SUB-009 | `frontend/static/js/dashboard.js` |
| CMP-062 | System Metrics UI | SUB-009 | `frontend/static/js/metrics.js` |
| CMP-063 | Settings UI | SUB-009 | `frontend/static/js/settings.js` |
| CMP-064 | Required Decoder (Pure) | SUB-010 | `backend/capability_required_decoder.py` |
| CMP-065 | Extraction (Pure) | SUB-010 | `backend/capability_extraction.py` |
| CMP-066 | Sufficiency (Pure) | SUB-010 | `backend/capability_sufficiency.py` |
| CMP-067 | Decision (Pure) | SUB-010 | `backend/capability_decision.py` |
| CMP-068 | DDNS Status API | SUB-011 | `backend/api/ddns.py` |
| CMP-069 | DDNS Updater | SUB-011 | `scripts/cloudflare-ddns.sh` |
| CMP-070 | DDNS UI | SUB-011 | `frontend/static/js/ddns.js` |

Status qualifiers: CMP-021/028/064-067 = Pure; CMP-064-067 = Unwired (SUB-010);
CMP-040-043 = Maintenance-Only (SUB-007); CMP-049 = Administration class.

Intentional multi-component files (Component ≠ File): `api/metrics.py` = {CMP-057,
CMP-019}; `api/settings.py` = {CMP-058, CMP-044}.

---

## Runtime Flow Registry

Fields: ID · caller → callee · purpose · trigger · sync/async · confidence (all High
unless noted).

| ID | caller→callee | purpose | trigger | S/A |
|---|---|---|---|---|
| RF-001 | CMP-007→CMP-002 | read node status | poll | sync |
| RF-002 | CMP-007→CMP-001 | start/stop/restart node | user | sync |
| RF-003 | CMP-008→CMP-001 | read/validate/apply config | user | sync |
| RF-004 | CMP-010→CMP-001 | top regions | user | sync |
| RF-005 | CMP-009→CMP-003 | fetch log tail | poll/user | sync |
| RF-006 | CMP-014→CMP-011 | compartment ops | user | sync |
| RF-007 | CMP-018→CMP-015 | generate/stream/discard claim | user | sync |
| RF-008 | CMP-025/026→CMP-019 | read traffic | poll | sync |
| RF-009 | CMP-029→CMP-027 | get advice | poll/user | sync |
| RF-010 | CMP-039→CMP-030 | create/inspect/restore backup | user | sync |
| RF-011 | CMP-043→CMP-040 | check/install/poll update | user/poll | sync(202+poll) |
| RF-012 | CMP-050→CMP-044 | login | user | sync |
| RF-013 | CMP-061→CMP-044 | logout | user | sync |
| RF-014 | CMP-062→CMP-057 | host metrics | poll | sync |
| RF-015 | CMP-063→CMP-058(+CMP-044) | theme/config/password | user | sync |
| RF-016 | CMP-070→CMP-068 | DDNS status | poll | sync |
| RF-017 | CMP-060→CMP-059 | drive pollers | client timer | async |
| RF-018 | CMP-051→all API/pages | router registration + dispatch | startup/request | sync |
| RF-019 | CMP-051(lifespan)→CMP-020 | start/stop collector | startup/shutdown | async |
| RF-020 | CMP-051(lifespan)→CMP-046 | purge sessions | startup + hourly | async |
| RF-021 | CMP-054→CMP-046 | validate session per request | request | sync |
| RF-022 | CMP-054→CMP-053 | yield DB connection | request | sync |
| RF-023 | CMP-001/002→CMP-004 | drive systemctl / read status | request | async subproc |
| RF-024 | CMP-001→CMP-005 | validate config | request | sync |
| RF-025 | CMP-011→CMP-012→CMP-013 | compartment op via helper | request | async subproc |
| RF-026 | CMP-015→CMP-016→CMP-017 | claim gen via helper | request | async subproc |
| RF-027 | CMP-027→CMP-028 | compute advice (pure) | request | sync |
| RF-028 | CMP-028→CMP-004+CMP-023 | gather advisor inputs | request | sync |
| RF-029 | CMP-030→CMP-031/036+CMP-038 | backup/restore orchestration | request | async(restore) |
| RF-030 | CMP-040→CMP-041 | stream tarball to helper (sudo, STDIN) | install | async |
| RF-031 | CMP-004→systemd is-active/show | read status (no sudo) | request | async subproc |
| RF-032 | CMP-004→sudo systemctl start/stop/restart | privileged state change | request | async subproc |
| RF-033 | CMP-006→drop-in + systemctl | apply config + restart (root) | invoked | sync |
| RF-034 | CMP-013→Conduit data (as conduit) | compartment write | invoked | sync |
| RF-035 | CMP-017→ryve-claim CLI (as conduit) | produce claim | invoked | sync |
| RF-036 | CMP-038→fs restore + systemctl restart (root) | restore + restart | invoked | async |
| RF-037 | CMP-041→systemd-run transient ccc-update.service | escape ProtectSystem | invoked | async |
| RF-038 | CMP-041(worker)→CMP-042 update.sh --ccc-only | deploy + restart | invoked | async |
| RF-039 | CMP-020→CMP-004 read_counters→Conduit | read counters | interval | async |
| RF-040 | CMP-069→Cloudflare API | update DNS record | cron | async |
| RF-041 | CMP-040→GitHub Releases API (urllib) | check latest | request | sync(net) |
| RF-042 | CMP-003→journald (journalctl) | read logs | request | async subproc |
| RF-043 | CMP-057→OS/psutil | read host metrics | request | sync |
| RF-044 | CMP-046(purge)→CMP-053 SQLite | delete expired sessions | hourly | async |

SUB-010 (CMP-064-067): no runtime flows (Unwired). CAP-004: 501 terminal.

---

## Data Flow Registry

Fields: ID · producer→consumer · data object · storage class · lifetime (all High).

| ID | producer→consumer | data object | storage class | lifetime |
|---|---|---|---|---|
| DF-001 | CMP-046→store | session record | SQLite (sessions) | until expiry |
| DF-002 | CMP-048→browser | session + CSRF cookie | network→browser | session |
| DF-003 | Conduit→CMP-020→store | traffic counters | SQLite (traffic) | persistent (pruned) |
| DF-004 | CMP-041→file | update-status.json | filesystem `/var/lib/conduit-cc` | until next run |
| DF-005 | file→CMP-040→UI | update status | transient memory | poll |
| DF-006 | CMP-038→file | restore-status.json | filesystem `/var/lib/conduit-cc` | until next |
| DF-007 | CMP-069→file | DDNS result JSON lines | filesystem `ddns.log` | rotated weekly |
| DF-008 | file→CMP-068→UI | DDNS status | transient memory | request |
| DF-009 | CMP-017→CMP-015→browser | Ryve claim PNG | temporary (tmpfs+RAM) | never persisted |
| DF-010 | CMP-031→browser | encrypted backup archive | network (transient) | download |
| DF-011 | browser→CMP-030→CMP-036/038 | uploaded backup bytes | temporary state | restore |
| DF-012 | GitHub→CMP-040→STDIN→CMP-041→`/opt` | update payload tarball | network→temp→filesystem | temp discarded; /opt persistent |
| DF-013 | CMP-006→file | Conduit env drop-in (CCC_*) | filesystem conduit.service.d | persistent |
| DF-014 | files→CMP-052 | .env + config.json | runtime memory | process lifetime |
| DF-015 | OS/psutil→CMP-057→UI | host metrics | transient memory | request |
| DF-016 | journald→CMP-003→UI | log lines | journal→transient | request |
| DF-017 | _version→CMP-055→UI | app version | runtime memory | process lifetime |
| DF-018 | CMP-013→file (as conduit) | personal compartment identity | filesystem `/var/lib/conduit/data` 0600 | persistent |
| DF-019 | CMP-058→browser (via CMP-048) | theme preference | network→browser cookie | persistent (client) |
| DF-020 | CMP-069↔Cloudflare | DNS record + public IP | external service | persistent (DNS) |

---

## Trust Boundary Registry

Fields: ID · name · purpose · crossings · security implications · evidence (all High).

| ID | Name | Purpose | Crossings | Implications | Evidence |
|---|---|---|---|---|---|
| TB-001 | Edge (TLS/proxy) | Terminate HTTPS, front the app | browser→Cloudflare→nginx→backend | TLS everywhere; backend on 127.0.0.1 | `conduit-cc.nginx`, Cloudflare |
| TB-002 | Authentication | Separate unauth/auth | RF-012, RF-021, cookies | bcrypt, sessions, CSRF, lockout | CMP-044-048,054 |
| TB-003 | Privilege elevation (sudo) | Exact privileged actions | RF-025,026,030,032,033,036,037 | argv-only, no-shell, exact-path grants | `/etc/sudoers.d/conduit-cc` |
| TB-004 | Namespace (ProtectSystem=strict) | Hardened RO namespace | RF-037 (transient unit) | writes to /opt,/var only via transient unit | `conduit-cc.service`, CMP-041 |
| TB-005 | CCC↔Conduit daemon | Separate conduit-cc/conduit users | systemctl, helpers-as-conduit | least privilege | two units, CMP-013/017 |
| TB-006 | Secret perimeter | Keep secrets out of persistence/logs | DF-009,010,013,018 | keys never backed up; claim RAM-only | CMP-017,034,013 |
| TB-007 | External update fetch (GitHub) | Obtain release/tarball | RF-041 | TLS + structural + version-increase; signing deferred | CMP-040 |
| TB-008 | External DNS (Cloudflare API) | Update DNS record | RF-040 | API token out-of-band | CMP-069 |
| TB-009 | Local persistence | Bound local state | SQLite + StateDirectory | 0750 dir, UMask 0077 | CMP-053, `conduit-cc.service` |

---

## External Systems Registry

Fields: ID · name · direction · protocol · auth · trust · failure impact · evidence.

| ID | Name | Direction | Protocol | Auth | Failure impact |
|---|---|---|---|---|---|
| EXT-001 | Conduit Core | CCC→node | systemctl/CLI/counters | OS users + sudoers | control/status/traffic/personal/ryve degrade |
| EXT-002 | systemd | CCC→systemd | systemctl / systemd-run | sudoers | no lifecycle/updates |
| EXT-003 | Cloudflare | bidirectional | HTTPS | API token (updater) | stale DNS / edge outage (direct still works) |
| EXT-004 | GitHub Releases | CCC→GitHub | HTTPS | none (public) | update check/install unavailable |
| EXT-005 | journald | CCC→journald | journalctl | OS perms | log viewing unavailable |
| EXT-006 | OS / psutil | CCC→OS | syscalls | local | metrics/advisor inputs degrade |
| EXT-007 | cron | cron→updater | cron | OS | DNS stops refreshing |
| EXT-008 | nginx | browser↔backend | HTTPS→HTTP(127.0.0.1) | none (edge) | dashboard unreachable |

Trust assumptions: EXT-001/002/005/006/007/008 are local/OS-trusted; EXT-003/004 are
external, TLS-protected; EXT-004 has no artifact signature (deferred, ADR-0001 inv. 5).
