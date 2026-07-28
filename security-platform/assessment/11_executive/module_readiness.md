# Per-Module Readiness

> Readiness % by module across three axes: **Production** (deployable, resilient, tested), **Security** (auth/authz/data protection), **Compliance** (HIPAA/TEFCA/508 where applicable). Derived from the Part 2 module scorecard + Parts 8/10 findings. Read-only.

## Readiness matrix

| Module | Prod Ready | Security Ready | Compliance Ready | Notes |
|---|:--:|:--:|:--:|---|
| **TEFCA Registry** | **85%** | **85%** | **80%** | Cleanest module; router-RBAC, indexed, audited, spec-aligned. Loses on tests + create-time TEFCAID guard |
| **Platform Config** | **85%** | **80%** | **80%** | Well-modeled 13-table config, no routes to attack, idempotent seeds |
| **Auth / Users** | **80%** | **78%** | **75%** | Mature stack (bcrypt/JWT/refresh/revocation/lockout/Entra). Gaps: HS256, in-memory lockout, Entra id_token unverified |
| **Admin** | **78%** | **75%** | **72%** | Super-admin gating solid; untyped-dict update (Low) |
| **TEFCA ARC (legacy)** | **65%** | **75%** | **70%** | Functionally rich, router-gated; 2,852-LOC file + near-zero tests + high debt |
| **Documents** | **65%** | **70%** | **60%** | PHI; per-user filtering good; **read-audit gap**, no field encryption |
| **Migration Intelligence** | **60%** | **70%** | **60%** | Reasonable; low maintainability, low tests |
| **Bulletin Intelligence** | **55%** | **65%** | **60%** | Largest engine (3,618 LOC); **in-memory storage** (data lost on restart), has the only tests |
| **Audio / Meetings** | **55%** | **62%** | **55%** | PHI; ffmpeg subprocess (safe); low tests |
| **Healthcare Claims** | **50%** | **55%** | **50%** | PHI; **IDOR** (AUTHZ-02), **PHI in query strings** (DP-03), in-memory store |
| **Intelligence / Governance / Decisions / SLA / Enterprise** | **55%** | **62%** | **55%** | Functional; low tests, medium debt |
| **Case Management** ⚠ | **25%** | **20%** | **20%** | **THE EPICENTER** — unauthenticated PHI router (12 endpoints), unmasked PHI → Anthropic; patient CRUD are stubs |
| **GovCon / ERP** | **N/A (quarantine)** | **N/A (unwired)** | N/A | Dead code — not in `app.main:app`; recommend quarantine over investment |
| **ATS / Staffing** | **N/A (quarantine)** | **N/A (unwired)** | N/A | Dead code — not wired; second `Base` |

## Readiness bands
- **Federal-ready core (75–85%):** TEFCA Registry, Platform Config, Auth, Admin — deployable now, gated mainly by tests + minor hardening.
- **Functional-but-not-hardened (50–65%):** TEFCA ARC, Documents, Bulletin, Healthcare Claims, Audio, and the intelligence family — work, but carry PHI/read-audit/in-memory/IDOR gaps + near-zero tests.
- **Blocked (20–25%):** Case Management — the live Critical; must be authenticated + PHI-masked before it can be considered production.
- **Dead / quarantine (N/A):** GovCon/ATS — unwired commercial stack; the earlier "unauthenticated CRUD" concern applies **only if the tables/routes are ever deployed**.

## The correction that changes the module map
Parts 1–2 treated **GovCon/ATS** as the top unauthenticated-access risk. Verification of `app/main.py` shows those routers are **not registered** — they are dead code (readiness N/A, not a live finding). The live PHI exposure is **Case Management** (`safe_load` at `main.py:321`). This moves the "worst module" from a dormant commercial stack to a **live, PHI-handling module** — a smaller blast radius to fix (one module) but a higher urgency (it's actually reachable).

## Aggregate module readiness (live modules only, excludes dead GovCon/ATS)
- **Production:** ~62% (weighted by criticality toward the federal core → ~70%)
- **Security:** ~65% (Case Management is the single largest drag)
- **Compliance:** ~60% (HIPAA partials + Case Management + read-audit)
