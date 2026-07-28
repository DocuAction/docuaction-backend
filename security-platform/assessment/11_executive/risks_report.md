# Top Risks

> What needs immediate attention, derived across Parts 1–10, ranked by (severity × reachability × compliance exposure). Read-only. **Includes the correction to the stale unauthenticated-endpoint count.**

## ⚠️ Correction to a headline number from Part 2
Part 2 reported **"~106 unauthenticated endpoints"** (~32 GovCon CRUD + ~44 ATS + others) as the top access-control concern. **Direct verification of `app/main.py` shows the GovCon (`app/routers/*`) and ATS routers are NOT registered — they are dead/unwired code.** They are **not a live exposure** and must **not** be carried forward as a finding. The true count of live, truly-unauthenticated, state-changing endpoints is **12 — all in `app/case_management`** (wired at `main.py:321`). Any prior document citing ~106 / ~32 / ~44 unauthenticated endpoints is **superseded** by this correction.

## Top 5 risks

### 1. 🔴 CRITICAL — Case Management is unauthenticated and leaks PHI to AI (the epicenter)
`app/case_management/routes.py` — the entire router (12 endpoints) has **no auth**, and its engines (`ccm_engine.py`, `discharge_engine.py`) send **unmasked PHI** (names, MRN, DOB, diagnoses) **directly to Anthropic** with no BAA gate. This one module is simultaneously the **Part 8 Critical**, the **Part 8 High**, the **Part 10 HIPAA Access-Control gap**, and the **Part 10 PHI-egress gap**. *Nuance:* patient CRUD endpoints are currently non-persisting stubs, but the AI/upload endpoints ingest and forward real PHI unauthenticated. **Highest urgency — it is live and internet-reachable.**

### 2. 🟠 HIGH — Test coverage is ~1.4/10 (one test file in the whole repo)
The single largest quality lever (Part 2). With **no automated tests and no CI test gate**, every change risks silent regression, and the federal modules' correctness (verification, import, auth) is validated only manually. This amplifies every other risk — there is no safety net for the remediation work itself. **It gates confidence in everything else.**

### 3. 🟠 HIGH — No HA/DR and no CD pipeline (operational resilience)
Single App Service instance (no autoscale/zone-redundancy), **Burstable B1ms Postgres with HA Disabled and geo-backup Disabled**, single region, manual zip deploy with no slot rollback (Part 9). The documented **RTO≤4h / RPO≤15min are aspirational** — unachievable under a real regional/instance failure. For a healthcare production system this is a material availability + data-durability risk.

### 4. 🟠 MEDIUM-HIGH — Audit integrity + transmission-security gaps (HIPAA blockers)
The canonical `audit_logs` table is **mutable** (deleted by `compliance.py`, updated by `admin_users.py`) with no WORM/hash-chain; **PHI reads are not logged**; **DB TLS is not pinned in code**; and there is **no BAA enforcement** (Part 10). These are the §164.312 safeguard gaps that block a defensible HIPAA posture independent of the case_management fix.

### 5. 🟡 MEDIUM — Public data-plane + no caching/scale readiness
**Public Postgres + public Key Vault**, no App Service IP restrictions, private endpoints authored-but-not-deployed (Part 9); combined with **in-memory state everywhere** (lockout, rate-limit, scheduler, bulletin storage), the **hierarchy N+1**, and **no caching layer** (Part 7), the platform is exposed at the network edge and unprepared to scale beyond one instance. A single Redis layer + deploying the authored network hardening addresses most of this.

## Risk heat by axis

| Axis | Level | Driver |
|---|:--:|---|
| **PHI / compliance** | **Critical** | case_management unauth PHI egress; audit/transmission gaps; no BAA |
| **Quality / regression** | **High** | 1.4/10 tests, no CI gate |
| **Availability / durability** | **High** | no HA/DR, single Burstable instance, manual deploy |
| **Network exposure** | **Medium** | public PG + KV, no IP restrictions |
| **Scale** | **Medium** | in-memory state, N+1, no cache |
| **Injection / classic AppSec** | **Low** | strong (no SQLi/cmdi/traversal) |

## The concentrating insight
Four of the top five risks are **compliance/operations**, not classic vulnerabilities — and they cluster into a **small number of shared root causes** (see `../12_roadmap/`). The case_management module alone accounts for the entire Critical/High security-and-healthcare exposure. **This is a containable risk profile:** no sprawling vulnerability class, but a few concentrated, high-consequence gaps that must be closed before any PHI-bearing HHS/ONC production use.
