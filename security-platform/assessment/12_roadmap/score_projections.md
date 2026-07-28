# Score Projections

> Projected score movement as the 30/60/90-day clusters land. Read-only. Projections assume the roadmap is executed in the stated sequence; they are estimates, not guarantees.

## Category projections

| Category | Current | After 30d | After 60d | After 90d | Driver |
|---|:--:|:--:|:--:|:--:|---|
| **Security** | 6.0 | 7.5 | 8.0 | 8.5 | A (case-mgmt), C (network), D (regression tests), E (scan gate) |
| **Healthcare Compliance** | 6.0 | 7.5 | 8.0 | 8.5 | A (PHI+audit+TLS), F (hash-chain, BAA, encryption) |
| **Accessibility** | 6.4 | 8.0 | 8.5 | 9.0 | B (titles/Field/tokens), F (full AA) |
| **Performance** | 5.5 | 7.0 | 7.5 | 8.0 | C (Redis, CTE), G (GIN, pagination, pool) |
| **DevOps / Operations** | 5.0 | 6.5 | 7.5 | 8.0 | C (HA, CD), E (governance), G (observability) |
| **Test Coverage** | 1.4 | 3.0 | 6.0 | 7.0 | D (framework + federal + regression + gate) |
| **UI / UX** | 7.2 | 8.0 | 8.2 | 8.5 | B (convergence, consistency) |
| **Design System** | 7.0 | 8.0 | 8.3 | 8.7 | B (hex→token, lint, converge), F (100%) |
| **Scalability** | 4.5 | 5.5 | 6.0 | 7.0 | C (Redis, HA), G (pagination, pool, indexes) |
| **Maintainability** | 5.9 | 6.3 | 7.0 | 7.5 | E (Alembic, quarantine dead code), D (tests) |
| **Backend Engineering** | 6.5 | 7.0 | 7.5 | 8.0 | A/C/D (consolidation, tests, caching) |
| **Frontend Engineering** | 6.0 | 6.8 | 7.2 | 7.8 | B/G (converge, code-split, cache) |
| **Database Design** | 6.5 | 6.8 | 7.2 | 7.8 | A (TLS), G (GIN, FK indexes), E (Alembic) |
| **API Design** | 6.0 | 6.8 | 7.2 | 7.5 | A (PHI out of query), C (pagination) |
| **Architecture** | 5.9 | 6.3 | 6.8 | 7.3 | E (quarantine dead code, one auth stack) |
| **Documentation** | 7.5 | 7.6 | 7.8 | 8.0 | F (SOC2/control mapping) |
| **OVERALL** | **5.8** | **6.8** | **7.5** | **8.0** | all clusters |

## Overall grade trajectory
```
5.8 ──30d──▶ 6.8 ──60d──▶ 7.5 ──90d──▶ 8.0
 C+           B-            B+            A-
```

## Readiness projections
| Metric | Current | 30d | 60d | 90d |
|---|:--:|:--:|:--:|:--:|
| **Production Readiness** | 65% | 78% | 88% | 95% |
| **Federal Readiness** (TEFCA+HIPAA+508) | 55% | 72% | 82% | 92% |
| **Open Critical findings** | 1 | 0 | 0 | 0 |
| **Open High findings** | 2 | 0 | 0 | 0 |
| **Security-maturity areas < Good** | 2 | 0 | 0 | 0 |

## What each milestone unlocks
- **After 30 days:** Critical/High closed, HIPAA blockers addressed, accessibility AA on the core, HA + CD in place. **Safe for controlled PHI pilots** with a signed BAA.
- **After 60 days:** a real test suite + CI gate + governance — **regression-protected**; a case_management-style gap would now be caught automatically. **Defensible for ONC security review.**
- **After 90 days:** full WCAG AA, HIPAA audit-ready, scale-prepared, SOC 2 prep underway. **Production-ready for federal PHI use.**

## Caveats on the projections
- **Test Coverage 1.4→7.0** assumes coverage is measured on the federal modules (not the dead commercial stack). Whole-repo coverage will read lower until dead code is quarantined (IMP-032).
- **Scalability** gains depend on **Azure tier changes** (HA Postgres, autoscale) that are operating-cost decisions, not just engineering.
- Score movements are **not fully additive** — some items (e.g. Redis) contribute partial credit to several categories; the projections already account for shared attribution.
- These are **projections of a documented plan**, not commitments. Actual movement depends on staffing and whether the case_management module is kept (hardened) or retired.
