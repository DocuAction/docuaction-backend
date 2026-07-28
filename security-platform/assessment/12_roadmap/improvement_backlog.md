# Improvement Backlog

> Full backlog, organized by root-cause cluster. Priority: Critical / High / Medium / Low. Effort in hours (h) or days (d). "Cluster" = shared root cause (A–G). "Scores" = which assessment scores the item moves. Read-only — no fixes applied.

## Legend
**Clusters:** A = PHI Protection · B = Accessibility/Design System · C = Infrastructure · D = Testing · E = Governance · F = Compliance Hardening · G = Scale Prep.

## Backlog

| ID | Category | Priority | Description | Module | Effort | Cluster | Scores improved |
|---|---|:--:|---|---|:--:|:--:|---|
| IMP-001 | Security | **Critical** | Gate/unmount Case Management router | Backend | 0.5–2h | A | Security, Healthcare |
| IMP-002 | Security | **Critical** | Rotate `.env` credentials → Key Vault | Infra | 2h | A | Security |
| IMP-003 | Security | **High** | Auth + PHI masking before AI egress (case-mgmt) | Backend | 2–8h | A | Security, Healthcare, HIPAA |
| IMP-004 | Security | High | Route case-mgmt upload through FileScanner + auth | Backend | 1–2h | A | Security |
| IMP-005 | Security | High | Full auth + module-gate on all case-mgmt endpoints | Backend | 1d | A | Security, Healthcare |
| IMP-006 | Data Protection | High | Expand `mask_pii` (names/addresses) on all AI egress | Backend | 2–3d | A | Security, Healthcare |
| IMP-007 | Compliance | High | BAA sign + code-level PHI-egress gate | Backend/Docs | 1d+proc | A | Healthcare, Compliance |
| IMP-008 | Audit | High | Audit-log immutability (remove delete/update; append-only) | Backend | 2–3d | A | Healthcare, Compliance |
| IMP-009 | Crypto | Medium | Pin DB TLS in `connect_args` (one engine) | Backend | 0.5d | A | Healthcare, Security |
| IMP-010 | AuthZ | Medium | Fix healthcare-claims IDOR + PHI out of query strings | Backend | 1d | A | Security, Healthcare |
| IMP-011 | Audit | Medium | Log PHI reads (document + registry GETs) | Backend | 2–3d | A | Healthcare, Compliance |
| IMP-012 | A11y | High | Page titles (`metadata`) on all routes | Frontend | 0.5–1d | B | Accessibility |
| IMP-013 | A11y | High | Shared `Field` component + migrate forms | Frontend | 4–6d | B | Accessibility, UX |
| IMP-014 | Design System | High | Hex → token migration (fixes dark mode) | Frontend | 4–6d | B | Design System, Accessibility, Dark Mode |
| IMP-015 | Design System | Medium | 11px type floor + lint rule (hex/size/spacing) | Frontend | 3–4d | B | Design System, Accessibility |
| IMP-016 | A11y | Medium | Keyboard-enable custom controls + PageHeader `<h1>` + focus rings | Frontend | 2–3d | B | Accessibility |
| IMP-017 | Design System | Medium | Converge duplicate components + drop dead react-table | Frontend | 3–4d | B | Design System, Maintainability, Performance |
| IMP-018 | Infra | High | Redis layer (cache + distributed lockout/rate-limit + scheduler dedup) | Infra/Backend | 3–5d | C | Performance, Security, DevOps |
| IMP-019 | Infra | High | Enable Postgres HA + geo-redundant backup | Infra | config+1d | C | DevOps |
| IMP-020 | Infra | Medium | Second App Service instance / autoscale | Infra | config | C | DevOps, Scalability |
| IMP-021 | DevOps | High | CD pipeline (test→build→slot→swap) + provision slot | Infra | 3–5d | C | DevOps |
| IMP-022 | Performance | High | Hierarchy N+1 → recursive CTE + count anti-pattern fix | Backend | 2–3d | C | Performance |
| IMP-023 | Infra Security | Medium | Deploy network hardening (PE for PG+KV, IP restrictions) | Infra | 1–2d | C | Security, DevOps |
| IMP-024 | Testing | High | Unit-test framework (pytest + async test DB + coverage) | All | 2–3d | D | Test Coverage |
| IMP-025 | Testing | High | Integration tests — federal modules (registry/import/auth) | Backend | 6–8d | D | Test Coverage, Healthcare |
| IMP-026 | Testing | High | Security regression tests (auth-on-every-endpoint, IDOR, masking) | Backend | 3–4d | D | Test Coverage, Security |
| IMP-027 | DevOps | High | CI test gate (block merge on failure, ≥60% federal) | Infra | 1–2d | D/E | Test Coverage, DevOps |
| IMP-028 | Governance | Medium | Alembic on prod (schema migration governance) | Backend/Infra | 2–3d | E | DevOps, Maintainability |
| IMP-029 | Governance | Medium | Branch protection + non-author review + ruleset | Repo | 0.5d | E | DevOps |
| IMP-030 | DevSecOps | Medium | Make security scans blocking + runtime SCA on deploy | Infra | 0.5–1d | E | DevOps, Security |
| IMP-031 | Governance | Low | Run Bicep in a pipeline (what-if/apply) — drift control | Infra | 2–3d | E | DevOps |
| IMP-032 | Maintainability | Medium | Quarantine/remove dead GovCon/ATS code | Backend | 1d | E | Maintainability, Security |
| IMP-033 | A11y | Medium | Full WCAG 2.2 AA (finish DS convergence, skip-link, `lang`) | Frontend | 6–8d | F | Accessibility |
| IMP-034 | Compliance | Medium | HIPAA audit-ready (hash-chain, evidence_hash, field encryption) | Backend | 5–7d | F | Healthcare, Compliance |
| IMP-035 | AI Security | Medium | NER-based PHI minimization + egress logging | Backend | 3–4d | F | Healthcare, AI Security |
| IMP-036 | Compliance | Low | SOC 2 Type II preparation (map controls, evidence) | Docs/Infra | 4–6d | F | Compliance |
| IMP-037 | TEFCA | Medium | Preventive TEFCAID/HCID guard + Common Agreement enforcement | Backend | 2–3d | F | Healthcare |
| IMP-038 | Database | Medium | GIN on JSONB + legacy FK indexes + composites | Database | 2–3d | G | Performance, Scalability |
| IMP-039 | Performance | Medium | Server-side pagination + virtualization + `.all()` limits | Full-stack | 4–6d | G | Performance, Scalability |
| IMP-040 | Performance | Low | Pool consolidation (recycle/pre-ping) + AI client reuse + AI time cap | Backend | 1–2d | G | Performance |
| IMP-041 | Observability | Medium | Instrument App Insights + alerts + 2nd channel + diag settings | Infra | 3–4d | G | DevOps, Scalability |
| IMP-042 | Frontend Perf | Low | `next/dynamic` heavy libs + SWR client cache + search race-guard | Frontend | 2–3d | G | Performance |

## Totals: **42 improvement items** (2 Critical · 11 High · 21 Medium · 8 Low)

## By cluster
| Cluster | Items | Effort (days) | Primary scores |
|---|:--:|:--:|---|
| A — PHI Protection | 11 | ~10–14 | Security, Healthcare, Compliance |
| B — Accessibility/DS | 6 | ~17–24 | Accessibility, UX, Design System |
| C — Infrastructure | 6 | ~11–17 | DevOps, Performance |
| D — Testing | 4 | ~12–17 | Test Coverage (+ all) |
| E — Governance | 5 | ~6–8 | DevOps, Maintainability |
| F — Compliance Hardening | 5 | ~20–28 | Healthcare, Accessibility, Compliance |
| G — Scale Prep | 5 | ~12–18 | Performance, Scalability, Observability |
| **Total** | **42** | **~88–126** | **all** |
