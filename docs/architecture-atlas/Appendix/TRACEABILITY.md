# CCC Architecture Atlas — Traceability, Cross-References, Queries, Views

**Release:** v1.0.0-rc1 · **Verified Revision:** v0.3.12
Derived exclusively from the frozen registries (`REGISTRIES.md`).

---

## Architecture Traceability Matrix

`CAP · Primary SUB · Supporting SUB · Components · Runtime Flows · Data Flows · Trust
Boundaries · External Systems`. Evidence = frozen Component Registry Owned Files.

| CAP | Prim | Supp | CMP | RF | DF | TB | EXT |
|---|---|---|---|---|---|---|---|
| CAP-001 | SUB-001 | — | 001,002,004,007 | 002,023,031,032 | — | 003,005 | 001,002 |
| CAP-002 | SUB-001 | — | 002,004,007 | 001,023,031 | — | 005 | 001,002 |
| CAP-003 | SUB-001 | — | 001,005,006,008 | 003,024,033 | 013 | 003,005 | 001,002 |
| CAP-004 | SUB-001 | — | 001 | 501 terminal | — | — | — |
| CAP-005 | SUB-002 | — | 011,012,013,014 | 006,025,034 | 018 | 003,005,006 | 001 |
| CAP-006 | SUB-003 | — | 015,016,017,018 | 007,026,035 | 009 | 003,005,006 | 001 |
| CAP-007 | SUB-004 | SUB-009 | 019,020,021,022,023,024,025,026 | 008,019,039 | 003 | 009 | 001 |
| CAP-008 | SUB-009 | — | 057,062 | 014,043 | 015 | 009 | 006 |
| CAP-009 | SUB-005 | SUB-001,004 | 027,028,029 | 009,027,028 | — | — | 001,006 |
| CAP-010 | SUB-001 | — | 003,009 | 005,042 | 016 | 005 | 005 |
| CAP-011 | SUB-011 | — | 068,070 | 016 | 008 | — | — |
| CAP-012 | SUB-011 | — | 069 | 040 | 007,020 | 008 | 003,007 |
| CAP-013 | SUB-006 | — | 030,031,032,033,034,035,037,039 | 010,029 | 010,011 | 006 | — |
| CAP-014 | SUB-006 | — | 030,036,038,039 | 010,029,036 | 011,006 | 003,004,006 | 002 |
| CAP-015 | SUB-007 | SUB-009 | 040,041,042,043 | 011,030,037,038,041 | 004,005,012 | 003,004,007 | 004,002 |
| CAP-016 | Deployment View (excluded) | — | — | — | — | — | 003,008 |
| CAP-017 | SUB-008 | SUB-009 | 044,045,046,047,048,049,050 | 012,013,021 | 001,002 | 002 | — |
| CAP-018 | SUB-009 | — | 058,063 | 015 | 019 | 002 | — |
| CAP-019 | SUB-009 | — | 051,056,059,060,061 | 017,018 | 017 | 001,002 | 008 |
| CAP-020 | SUB-009 | — | 055 | health poll | 017 | — | — |
| CAP-021 | SUB-010 | — | 064,065,066,067 | — (Unwired) | — | — | — |

By-design terminals: CAP-004 (Not-Implemented), CAP-016 (Deployment-excluded),
CAP-021 (Unwired). No orphans; all other chains complete.

---

## Cross Reference Index

**CAP → SUB:** 001-003,004,010→SUB-001 · 005→SUB-002 · 006→SUB-003 · 007→SUB-004 ·
009→SUB-005 · 013,014→SUB-006 · 015→SUB-007 · 017→SUB-008 · 008,018,019,020→SUB-009 ·
021→SUB-010 · 011,012→SUB-011 · 016→Deployment(excl).

**SUB → CMP:** SUB-001→001-010 · SUB-002→011-014 · SUB-003→015-018 · SUB-004→019-026 ·
SUB-005→027-029 · SUB-006→030-039 · SUB-007→040-043 · SUB-008→044-050 ·
SUB-009→051-063 · SUB-010→064-067 · SUB-011→068-070.

**CMP → RF (representative):** 004→023,031,032,039 · 041→030,037,038 · 020→019,039 ·
044→012,013,021 · 054→021,022 · 069→040 · 003→042 · 057→043 · 068→016.

**RF → DF:** 030/037/038→004,005,012 · 039→003 · 040→007,020 · 025→018 · 026→009 ·
010/029→010,011 · 036→006,011 · 012/021→001,002 · 015→019 · 043→015 · 042→016.

**DF → TB:** 001,002→002 · 003,004,006→009 · 007,020→008 · 009,010,018→006 · 012→004,007
· 013→003 · 019→002.

**TB → EXT:** 001→008 · 003→002 · 004→002 · 005→001 · 007→004 · 008→003,007 · 009→(local).

**EXT → CMP:** 001→004,013,017,020 · 002→004,041 · 003→069 · 004→040 · 005→003 ·
006→057 · 007→069 · 008→(edge/nginx artifact).

---

## Engineering Query Index

| Query | Answer |
|---|---|
| What implements CAP-015? | SUB-007; CMP-040,041,042,043 |
| Which Components belong to SUB-007? | CMP-040,041,042,043 |
| What Runtime Flows cross TB-003 (sudo)? | RF-025,026,030,032,033,036,037 |
| Which Data Flows leave the system (egress)? | DF-002,009,010,019 (browser); DF-020 (Cloudflare) |
| Which Components communicate with Cloudflare? | CMP-069 |
| Which Capabilities require sudo? | CAP-001,003,005,006,014,015 |
| Which Runtime Flows enter ProtectSystem exceptions (TB-004)? | RF-037 |
| Which Components own SQLite? | CMP-053 (connectivity); CMP-046 (sessions), CMP-024/020 (traffic) |
| Which Capabilities remain unwired? | CAP-021 |
| Which External Systems participate in updates? | EXT-004 (GitHub), EXT-002 (systemd) |
| Which flows are Background class? | RF-019,020,040,044 |
| Which Components run as root / conduit? | root: CMP-006,038,041; conduit: CMP-013,017 |
| Which Capabilities are Maintenance-Only? | CAP-015 (SUB-007) |
| Which data is never persisted? | DF-009 (Ryve claim) |
| Which Capabilities are Not-Implemented / Deployment-excluded? | CAP-004 / CAP-016 |
| Which Trust Boundaries have deferred hardening? | TB-007 (artifact signing) |
| Which Components are Pure? | CMP-021,028,064,065,066,067 |

---

## Architecture View Registry

| VIEW | Name | Purpose | Primary entities |
|---|---|---|---|
| VIEW-01 | Capability | What CCC can do | CAP + Class/Status |
| VIEW-02 | Subsystem | Organizational units | SUB, CAP |
| VIEW-03 | Component | Implementation units | CMP + Owned Files |
| VIEW-04 | Runtime Flow | Execution interactions | RF, CMP |
| VIEW-05 | Data Flow | Data movement/storage | DF, CMP |
| VIEW-06 | Trust Boundary | Security boundaries | TB, RF, CMP |
| VIEW-07 | External Systems | External participants | EXT, CMP |
| VIEW-08 | Deployment (reference) | Provisioning topology | Deployment artifacts, CAP-016 |
| VIEW-09 | Security (composed) | End-to-end security posture | TB + auth + secret DF + sudo RF |
| VIEW-10 | Operational (composed) | Run/observe/maintain | Background RF, ops CAP |
| VIEW-11 | Traceability | Full chains | all |
| VIEW-12 | Capability-Class | How capabilities are exercised | CAP + Class |
| VIEW-13 | Status/Lifecycle | Implementation state | CAP/SUB/CMP + Status |

VIEW-09 and VIEW-10 are composed lenses over existing entities (no new data).

---

## Diagram Mapping Registry

Per view: Scope · Included · Excluded · Layout · Cross-refs · Entry · Metadata. No
diagrams are produced here; this maps how they will be generated (see MASTER.md).
Required metadata for every generated diagram: Diagram ID (DIAG-xxx), Version, Last
Updated, Sources, Verified Revision (v0.3.12), Related ADRs, Related Components,
Related Runtime Flows.

| View | Scope | Excluded | Layout | Entry |
|---|---|---|---|---|
| VIEW-01 | CAP + Class/Status | CMP/RF/DF/TB | grouped-by-class | CAP-015 |
| VIEW-02 | SUB + realized CAP | CMP internals | responsibility clusters | SUB-009 |
| VIEW-03 | CMP within one SUB (per-subsystem sheets) | other SUB CMP | subsystem-boxed | SUB-007 |
| VIEW-04 | RF between CMP | DF/TB detail | tiered UI→API→adapter→helper→external | RF-030 |
| VIEW-05 | DF producer→consumer | control flow | swimlanes by storage class | DF-012 |
| VIEW-06 | TB + crossing RF | non-crossing flows | nested trust zones | TB-003 |
| VIEW-07 | EXT + CMP touchpoints | internal-only CMP | hub-and-spoke (CCC center) | EXT-004 |
| VIEW-08 | provisioning artifacts | runtime flows | host-topology | install.sh |
| VIEW-09 | TB+auth+secrets+sudo | non-security flows | defense-in-depth layers | TB-002 |
| VIEW-10 | background+observability | user-triggered UI | timeline/lifecycle | RF-040 |
| VIEW-11 | full chains | none | matrix (tabular) | CAP-015 chain |
| VIEW-12 / VIEW-13 | CAP by class/status | — | grouped columns | CAP-021 / CAP-015 |
