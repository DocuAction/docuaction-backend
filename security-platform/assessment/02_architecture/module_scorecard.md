# Per-Module Scorecard (Section 2N)

Scores 1–10 (10 = excellent). Based on read-only static review. **Test Coverage** is low across the board — the repository contains **exactly one test file** (`app/bulletin_intelligence/test_bulletin_enhancements.py`), so most modules score ≤2 there regardless of quality.

| Module | Arch | Security | Code Quality | Maintainability | Test Coverage | Tech Debt (10=low debt) | **Risk** |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **TEFCA Registry** | 8 | 8 | 8 | 8 | 3 | 8 | **Low** |
| **Platform Config** | 8 | 7 | 8 | 8 | 2 | 8 | **Low** |
| **Auth / Users** | 7 | 7 | 7 | 7 | 1 | 6 | **Medium** |
| **Admin** | 7 | 7 | 7 | 7 | 1 | 7 | **Medium** |
| **Migration Intelligence** | 6 | 7 | 6 | 5 | 1 | 6 | **Medium** |
| **Audio / Meetings** | 6 | 6 | 6 | 6 | 1 | 7 | **Medium** (PHI) |
| **Documents** | 6 | 7 | 6 | 6 | 1 | 6 | **Medium** (PHI) |
| **Healthcare Claims** | 6 | 6 | 6 | 6 | 1 | 5 | **Medium-High** (PHI) |
| **Case Management** | 6 | 6 | 5 | 5 | 1 | 5 | **Medium-High** (PHI) |
| **TEFCA ARC (legacy)** | 6 | 7 | 5 | 5 | 1 | 4 | **Medium** |
| **Bulletin Intelligence** | 5 | 6 | 5 | 4 | 3 | 3 | **Medium** |
| **Intelligence/Governance/Decisions/SLA/Enterprise** | 5 | 6 | 5 | 5 | 1 | 4 | **Medium** |
| **GovCon / ERP** | 4 | 3 | 5 | 4 | 0 | 3 | **High**¹ |
| **ATS / Staffing** | 4 | 3 | 5 | 4 | 0 | 3 | **High**¹ |

¹ *Risk is HIGH by design (unauthenticated CRUD) but currently unexploitable-for-data because tables aren't deployed. It becomes HIGH-active if the tables are created.*

## Rationale highlights

**TEFCA Registry (8/8/8)** — cleanest module. Explicit indexes incl. partial + unique, `ON DELETE CASCADE`, deterministic idempotent seeds, per-entity savepoints in import, full audit trail, router-level RBAC, self-contained (no cross-module reach-ins). Loses points only on committed automated tests (verification/import were validated manually).

**Platform Config (8)** — well-modeled 13-table config layer, idempotent upsert seed, no routes to attack. Low risk.

**Auth/Users (7)** — bcrypt + JWT + refresh + token-epoch revocation + lockout + Entra SSO is a mature stack. Deductions: **HS256** (symmetric — single shared secret vs RS256/asymmetric), **in-memory** lockout/rate-limit (per-process; ineffective across multiple workers/instances — though currently 1 instance), and `passlib` redundancy.

**TEFCA ARC legacy (6/7/5)** — functionally rich (real NPPES/LEIE/SAM connectors, QA engine, evidence model) and properly router-gated, but a **2,852-LOC single routes file** + **786-LOC mock_data** = high complexity, low maintainability, near-zero tests.

**Bulletin (5/…/3 debt)** — the **largest engine (3,618 LOC)** with in-memory article/briefing storage (not DB-backed → data lost on restart unless persisted), multiple external news APIs, and the only tests in the repo. High tech debt, medium risk.

**Healthcare Claims / Case Management (PHI)** — engines present and reasonable, but handle **PHI** with the lowest test coverage; PHI-flow and masking must be verified (Part 10).

**GovCon/ATS (4/3)** — dormant commercial stack: unauthenticated endpoints, tables not deployed, second `Base`. Recommend quarantine over investment.

## Aggregate (unweighted mean across 14 modules)
| Dimension | Mean |
|---|:--:|
| Architecture | **5.9 / 10** |
| Security | **6.1 / 10** |
| Code Quality | **6.0 / 10** |
| Maintainability | **5.9 / 10** |
| Test Coverage | **1.4 / 10** ⚠ |
| Tech Debt (10=low) | **5.4 / 10** |

**Weighted to the CRITICAL/federal modules only** (TEFCA Registry, Platform Config, Auth, Admin, Migration, Healthcare, Case Mgmt, TEFCA ARC), the picture is notably better: Architecture ≈ 6.5, Security ≈ 6.9, Code ≈ 6.4 — the federal stack is the strong part of the codebase; the commercial stack drags the mean down.
