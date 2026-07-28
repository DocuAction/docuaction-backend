# DocuAction Platform — Executive Security & Quality Assessment

**Phase 0 · Read-only static assessment · 12-part review**
Prepared for: HHS/ONC security review · AGT leadership · future FedRAMP/SOC 2 assessors
Scope: full-stack (Next.js frontend + FastAPI/PostgreSQL backend on Azure). **No production code was modified.**

---

## 1. Executive Summary

DocuAction is a **healthcare interoperability and reviewer platform** for the federal **TEFCA** program — a registry of exchange organizations (QHINs/Participants/Sub-Participants) with automated verification against government sources, FHIR/CSV import, and an AI-assisted document pipeline.

**Overall product grade: 5.8 / 10 (C+ / Conditional).** The platform is **bimodal**: a **strong federal core** (TEFCA/FHIR engineering, authentication, documentation — scoring 7–8.5) sitting on **weak operations** (testing, HA/DR, scale — scoring 4.5–5.5), with **one module carrying a critical, containable risk**.

The security *fundamentals* are strong — no injection, modern auth, hardened headers, file scanning. The risk is concentrated: **one module (Case Management) is unauthenticated and sends unmasked PHI to a third-party AI service.** It is the single source of the platform's critical exposure — and therefore the single highest-leverage fix.

**Recommendation: Conditional Go** — proceed to remediation, with the Case Management critical closed **first**, before any further HHS/ONC interaction involving live PHI. The path to production-ready is short, well-defined (~90 engineer-days), and concentrated in a handful of shared-root-cause fixes — not a rebuild.

---

## 2. Overall Product Grade & Scorecard

| Category | Score | Key finding |
|---|:--:|---|
| Architecture | 5.9/10 | Clean federal stack; dragged by dormant commercial modules |
| Code Quality | 6.0/10 | Strong in registry/import; oversized files + dead code elsewhere |
| UI / UX | 7.2/10 | Token pages strong, legacy weak (three-dialect fault line) |
| Design System | 7.0/10 | ~70% exists; 191 hex leaks + 688 sub-11px + ~46 non-token pages |
| Backend Engineering | 6.5/10 | Async FastAPI, strong auth, file scanner; N+1 registry, no caching |
| Frontend Engineering | 6.0/10 | Good token components; no code-split/virtualization/client-cache |
| Database Design | 6.5/10 | Registry richly indexed; ~10 unindexed legacy FKs, 0 GIN, no prod Alembic |
| API Design | 6.0/10 | RESTful+RBAC in registry; PHI in query strings, missing pagination |
| Security | 6.0/10 | One Critical module; core genuinely strong |
| Performance | 5.5/10 | Fast today, latent at scale (hierarchy N+1, no caching) |
| Accessibility | 6.4/10 | Token pages 8.5, legacy 3.5; 9 WCAG AA failures |
| Healthcare Compliance | 6.0/10 | TEFCA/FHIR spec-aligned; not PHI-ready |
| DevOps / Operations | 5.0/10 | Scanned not automated; no CD, no HA/DR |
| Maintainability | 5.9/10 | Dead code, dual stacks, three dialects, ~1.4 tests |
| Scalability | 4.5/10 | Single instance, in-memory state, Burstable-no-HA DB |
| Test Coverage | 1.4/10 | 1 test file — biggest single lever |
| Documentation | 7.5/10 | Genuine strength — runbooks, IR, ATO/SSP, IaC |
| **Technical Debt Level** | **Medium** | Concentrated in legacy/commercial + test absence, not the federal core |
| **Production Readiness** | **65%** | Federal core deployable; PHI-handling + resilience gate full production |
| **Federal Readiness** | **55%** | TEFCA ✅ · HIPAA ◐ · 508 ◐ |
| **OVERALL PRODUCT GRADE** | **5.8/10** | C+ / Conditional |

*Grade = mean of 17 categories (5.84); distribution is bimodal, not uniformly average. Mean excluding the Test-Coverage outlier = 6.1.*

---

## 3. Per-Module Readiness

| Module | Prod Ready | Security Ready | Compliance Ready |
|---|:--:|:--:|:--:|
| TEFCA Registry | 85% | 85% | 80% |
| Platform Config | 85% | 80% | 80% |
| Auth / Users | 80% | 78% | 75% |
| Admin | 78% | 75% | 72% |
| TEFCA ARC (legacy) | 65% | 75% | 70% |
| Documents | 65% | 70% | 60% |
| Bulletin Intelligence | 55% | 65% | 60% |
| Healthcare Claims | 50% | 55% | 50% |
| **Case Management** ⚠ | **25%** | **20%** | **20%** |
| GovCon / ATS | N/A (dead code — quarantine) | N/A (unwired) | N/A |

**Bands:** federal-ready core (75–85%) · functional-but-not-hardened (50–65%) · blocked (Case Management 20–25%) · dead/quarantine (GovCon/ATS). Full detail: `11_executive/module_readiness.md`.

---

## 4. Security Posture & Maturity

**Fundamentals strong, data-protection & ops-governance weaker.**

| Area | Maturity |
|---|---|
| Authentication | **Good** |
| Authorization | Moderate |
| Input Validation | **Good** |
| Cryptography | **Good** |
| Logging & Audit | Moderate |
| Secrets Management | Moderate |
| DevSecOps | Moderate |
| Infrastructure Security | Moderate |
| Data Protection | Developing |
| Incident Response | **Good** |
| Compliance | Moderate |
| Supply Chain Security | **Good** |
| AI Security | Developing |

**OWASP Top 10 (2021):** A01 **Critical** · A04 **High** · A02/A05/A07/A08/A09 Medium · A03/A10 **Low**. The classic technical categories (injection, crypto, headers, upload) are genuinely strong; risk is concentrated in one module + governance. Detail: `11_executive/security_maturity.md`, `08_security/`.

---

## 5. Compliance Readiness (TEFCA + HIPAA + Section 508)

- **TEFCA / FHIR — Compliant.** Entity hierarchy, verification engine (NPI Luhn, Tarjan SCC), and two-pass FHIR/CSV import are spec-aligned; FHIR identifier URIs/profiles/Bundle handling all conform. Gaps: mandatory TEFCAID/HCID is detective-not-preventive; Common Agreement is docs-only.
- **HIPAA §164.312 — Partial.** Authentication ✅; Access Control / Audit / Integrity ◐; **Transmission Security ❌** (DB TLS unpinned, no in-app HTTPS layer). **PHI is sent to Anthropic without full minimization, without auth (case-mgmt), and without a code-enforced BAA.**
- **Section 508 / WCAG 2.2 AA — 6.4/10.** Token pages 8.5; ~46 legacy pages carry **9 failing criteria**. Detail: `10_healthcare/`, `06_accessibility/`.

---

## 6. Top 5 Strengths
1. **Domain-correct, spec-aligned TEFCA/FHIR federal engine** — the moat.
2. **Strong security fundamentals** (no injection, modern auth, hardened headers, file scanning).
3. **Exceptional documentation & governance** for the team size (runbooks, IR plan, ATO/SSP, IaC, scanning CI).
4. **Design system ~70% already built**, accessibility baked into components.
5. **Honest, fail-closed engineering culture** (truthful empty states, audit trails, no fabricated metrics).

Detail: `11_executive/strengths_report.md`.

---

## 7. Top 5 Risks

> **Correction to a prior headline number.** Part 2 cited **"~106 unauthenticated endpoints"** (~32 GovCon + ~44 ATS + others). **Those routers are NOT registered in `app/main.py` — they are dead/unwired code and are NOT a live exposure.** The true count of live, truly-unauthenticated, state-changing endpoints is **12 — all in `app/case_management`** (`main.py:321`). The ~106/~32/~44 figures are **superseded**.

1. 🔴 **CRITICAL — Case Management is unauthenticated and leaks PHI to AI** (the epicenter).
2. 🟠 **HIGH — Test coverage ~1.4/10** (one test file; no CI gate) — amplifies every other risk.
3. 🟠 **HIGH — No HA/DR and no CD** (single Burstable instance; RTO/RPO aspirational).
4. 🟠 **MEDIUM-HIGH — Audit integrity + transmission gaps** (mutable audit log, no read-audit, DB TLS unpinned, no BAA) — HIPAA blockers.
5. 🟡 **MEDIUM — Public data-plane + no scale readiness** (public Postgres+KV, in-memory state, N+1, no caching).

Detail: `11_executive/risks_report.md`.

---

## 8. Architecture Overview

The system is a client-side-rendered Next.js static site (Azure Static Web Apps) calling a FastAPI backend (Azure App Service P0v3) over PostgreSQL 16 (Flexible Server), with Key Vault + Managed Identity, Defender Standard, and App Insights. See the **11 Mermaid diagrams** in `02_architecture/diagrams/` — C4 context/containers/components, system architecture, data flow, trust boundaries, auth flow, TEFCA import flow, and Azure infrastructure. Trust-boundary and attack-surface analysis: `02_architecture/`.

---

## 9. The Epicenter Finding — `app/case_management`

**One module causes the platform's entire critical risk profile.** Wired live at `main.py:321` via `safe_load("app.case_management", …)`, its router (`routes.py:34`) has **no authentication** on any of 12 state-changing PHI endpoints, and its AI engines (`ccm_engine.py:25,164`; `discharge_engine.py:19,33`) send **unmasked PHI** (names, MRN, DOB, diagnoses) **directly to Anthropic** with no BAA gate. This single module is:
- the **Part 8 Critical** (unauthenticated PHI router),
- the **Part 8 High** (unmasked PHI → AI),
- the **Part 10 HIPAA Access-Control gap**, and
- the **Part 10 PHI-egress gap**.

*Nuance:* patient CRUD endpoints are currently non-persisting stubs, but the AI/upload endpoints ingest and forward real PHI. **Fixing this one module resolves the critical security finding and two HIPAA gaps simultaneously** — the clearest illustration of the shared-root-cause thesis below.

---

## 10. Shared Root-Cause Analysis

A handful of fixes each move **multiple** scores — remediation is leverage, not a long list of unrelated tickets:

| Root-cause fix | Scores it moves |
|---|---|
| **Case Management auth + PHI masking** | Security **+** Healthcare |
| **Audit immutability + DB TLS + BAA** | Healthcare **+** Compliance |
| **Redis layer** (cache + distributed lockout/rate-limit + scheduler dedup) | Performance **+** Security **+** DevOps |
| **HA/DR + CD pipeline** | DevOps (resilience) |
| **Design-system convergence** (hex→token, Field component, 11px floor) | Accessibility **+** UX **+** Dark Mode |
| **Automated tests + CI gate** | **ALL** modules (de-risks every change) |

Seven clusters (A–G) organize all 42 backlog items. Detail: `12_roadmap/improvement_backlog.md`.

---

## 11. Prioritized Remediation Roadmap (30 / 60 / 90)

- **Immediate (week 1):** contain the Critical (gate/authenticate Case Management), rotate exposed credentials, stop unmasked PHI egress. *~1 day.* (`12_roadmap/immediate_actions.md`)
- **30-day (Clusters A/B/C):** PHI protection + audit immutability + DB TLS; accessibility/design-system convergence; Redis + HA + CD pipeline + N+1 fix. *~37–53 days.*
- **60-day (Clusters D/E):** automated test suite + CI gate; governance (Alembic on prod, branch protection, blocking scans, quarantine dead code). *~18–25 days.*
- **90-day (Clusters F/G):** full WCAG AA, HIPAA audit-ready (hash-chain, field encryption), SOC 2 prep; GIN indexes, server-side pagination, observability. *~32–46 days.*

---

## 12. Score Projections (current → 30d → 60d → 90d)

| Category | Now | 30d | 60d | 90d |
|---|:--:|:--:|:--:|:--:|
| Security | 6.0 | 7.5 | 8.0 | 8.5 |
| Healthcare | 6.0 | 7.5 | 8.0 | 8.5 |
| Accessibility | 6.4 | 8.0 | 8.5 | 9.0 |
| Performance | 5.5 | 7.0 | 7.5 | 8.0 |
| DevOps | 5.0 | 6.5 | 7.5 | 8.0 |
| Test Coverage | 1.4 | 3.0 | 6.0 | 7.0 |
| **OVERALL** | **5.8** | **6.8** | **7.5** | **8.0** |
| Production Readiness | 65% | 78% | 88% | 95% |
| Federal Readiness | 55% | 72% | 82% | 92% |

Detail + caveats: `12_roadmap/score_projections.md`.

---

## 13. Estimated Investment

- **Total:** ~88–126 engineer-days (**midpoint ~90–105 days**) → grade **5.8 → ~8.0**, all Critical/High closed.
- **≈ $90K–$126K** engineering (at ~$1K/day), excluding the BAA (legal/process) and Azure tier upgrades (HA Postgres, autoscale — operating cost).
- **Highest-ROI slice:** the immediate + Cluster-A work (**~10–15 days / ~$10K–$15K**) removes the single largest compliance/security liability in the first two weeks.

---

## 14. Final Assessment & Go/No-Go Recommendation

**DocuAction is a strong federal application on weak operational footing, with one module carrying a critical, containable risk.** The hard, hard-to-build things — domain-correct TEFCA/FHIR engineering, security fundamentals, governance depth — are done well. The remaining gaps — one module's authentication, PHI minimization, test coverage, HA/DR, design-system adoption — are mechanical, well-understood, and concentrated.

**Recommendation: CONDITIONAL GO.**
- **Do not** conduct any HHS/ONC interaction involving **live PHI** until the **Case Management critical is closed** (immediate actions, week 1) and a **BAA is signed**.
- **Proceed** with the 30/60/90-day roadmap; the platform reaches **production-ready for federal PHI use (~8.0/10, 95% production readiness)** in roughly **90 engineer-days**.
- The existence of this assessment, the ATO/SSP documentation, and the Bicep IaC constitute a **credible security-governance evidence base** for ONC review and FedRAMP/SOC 2 pursuit.

**Production impact of this entire assessment: ZERO — read-only, documentation only, no production code modified.**

---

*Supporting detail: Parts 1–12 under `security-platform/assessment/01_discovery/` … `12_roadmap/`. This document consolidates them; the per-part files carry the file:line evidence, WCAG criteria, OWASP/CWE/NIST mappings, and Mermaid diagrams.*
