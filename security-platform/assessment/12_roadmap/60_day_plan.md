# 60-Day Plan

> Testing and governance — the multipliers that de-risk everything else. Read-only. Effort in engineer-days. Assumes the 30-day clusters (A/B/C) are underway or complete.

## Cluster D — Testing → ALL modules (the biggest single lever)
Test coverage is 1.4/10 (one test file). This cluster is what turns every future change from "hope" into "verified."

| Item | Action | Effort |
|---|---|---|
| IMP-024 | Stand up the **unit-test framework** (pytest + fixtures + async test DB); wire coverage reporting | 2–3d |
| IMP-025 | **Integration tests for the federal modules** — TEFCA registry verification engine (Luhn, Tarjan SCC, identifier/hierarchy checks), FHIR/CSV import (ordering, reference resolution, idempotency), auth (login, lockout, refresh, revocation) | 6–8d |
| IMP-026 | **Security regression tests** — assert auth on every state-changing endpoint (would have caught case_management), IDOR ownership checks, PHI-masking-before-egress | 3–4d |
| IMP-027 | **CI test gate** — add a pytest job to the GitHub Actions workflows; **block merge on failure**; target ≥60% coverage on federal modules | 1–2d |

**Cluster D effort: ~12–17 days. Scores: Test Coverage 1.4→6.0. Indirect: raises confidence in Security/Healthcare/Backend by making regressions detectable.**

## Cluster E — Governance → DevOps + Compliance + Maintainability
Close the process gaps that let the case_management Critical ship unnoticed.

| Item | Action | Effort |
|---|---|---|
| IMP-028 | **Alembic on prod** — bring schema under migration governance (currently no migrations run on prod) | 2–3d |
| IMP-029 | **Branch protection + required non-author review** (add a second CODEOWNER/reviewer; ruleset in-repo) | 0.5d |
| IMP-030 | **Make security scans blocking** — drop `|| true` on Bandit high/critical; add a runtime SCA gate on deploy | 0.5–1d |
| IMP-031 | **Run Bicep in a pipeline** (`what-if` on PR, apply on main) to end IaC drift | 2–3d |
| IMP-032 | **Quarantine the dead GovCon/ATS code** — formally isolate or remove (prevents accidental future wiring of unauthenticated CRUD) | 1d |

**Cluster E effort: ~6–8 days. Scores: DevOps 6.5→7.5, Maintainability +, Compliance +.**

## 60-day totals
- **Effort:** ~18–25 engineer-days on top of the 30-day work.
- **Scores moved:** Test Coverage 1.4→6.0 · DevOps 6.5→7.5 · Security 7.5→8.0 (regression-protected) · Healthcare 7.5→8.0 · Maintainability improving.
- **Cumulative (30+60 day) effort:** ~55–78 engineer-days.

## Why testing comes after the immediate PHI fixes but before scale/compliance polish
The Critical must be contained in week 1 (immediate) — you don't wait for a test suite to close an unauthenticated PHI hole. But **before** investing in the larger 90-day compliance/scale work, the test suite (Cluster D) + governance gate (Cluster E) must exist, so that (a) the remediation itself is verified, and (b) a future case_management-style gap is caught by the "auth on every state-changing endpoint" regression test rather than by the next audit.
