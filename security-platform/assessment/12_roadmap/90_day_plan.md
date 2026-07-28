# 90-Day Plan

> Compliance hardening and scale preparation — bringing the platform to a defensible federal-production bar. Read-only. Effort in engineer-days. Assumes 30- and 60-day clusters complete.

## Cluster F — Compliance Hardening → Healthcare + Accessibility + Compliance
Move from "gaps closed" to "audit-ready."

| Item | Action | Effort |
|---|---|---|
| IMP-033 | **Full WCAG 2.2 AA** — finish the DS convergence (remaining Dialect-2/3 pages), verify all 9 failing criteria pass, add skip-link + `lang`, per-combo contrast matrix | 6–8d |
| IMP-034 | **HIPAA gap closure to audit-ready** — audit hash-chain + tamper-detection routine, populate `evidence_hash`, add `user_agent` to audit, field-level encryption for highest-sensitivity PHI columns (MRN/diagnoses) | 5–7d |
| IMP-035 | **PHI minimization for AI (hardened)** — NER-based name/address detection beyond regex; egress logging + review; confirm zero-retention | 3–4d |
| IMP-036 | **SOC 2 Type II preparation** — map existing controls (already strong docs) to Trust Services Criteria; evidence collection automation; gap list | 4–6d |
| IMP-037 | **Preventive TEFCAID/HCID enforcement** (create-time guard) + **Common Agreement** exchange-purpose enforcement | 2–3d |

**Cluster F effort: ~20–28 days. Scores: Healthcare 8.0→8.5, Accessibility 8.5→9.0, Compliance → audit-ready.**

## Cluster G — Scale Preparation → Performance + Scalability + Observability
Ready the platform for 100K+ rows and multi-instance operation.

| Item | Action | Effort |
|---|---|---|
| IMP-038 | **GIN indexes on JSONB** (`fhir_resource` + expression index on `->>'id'`) + the ~10 unindexed legacy FK columns + suggested composites | 2–3d |
| IMP-039 | **Server-side pagination + virtualization** — fix `DataTable` default `pageSize`, add server paging, virtualize large lists; enforce `.all()` limits | 4–6d |
| IMP-040 | **Connection-pool optimization** — one engine, `pool_recycle` + `pool_pre_ping`, tuned `pool_size`; reuse the AI httpx client; cap total AI time | 1–2d |
| IMP-041 | **Comprehensive monitoring** — code-instrument App Insights; add latency/memory/DB-saturation/cert-expiry alerts; second alert channel + recipient; diagnostic settings; longer log retention | 3–4d |
| IMP-042 | **Frontend performance** — `next/dynamic` the export/chart libs; add SWR (client cache) + the search race-guard | 2–3d |

**Cluster G effort: ~12–18 days. Scores: Performance 7.0→8.0, Scalability 4.5→7.0, Observability → production-grade.**

## 90-day totals
- **Effort:** ~32–46 engineer-days on top of the 30+60-day work.
- **Scores moved:** Healthcare →8.5 · Accessibility →9.0 · Performance →8.0 · Scalability →7.0 · Compliance → audit/SOC2-prep-ready.
- **Cumulative (all-phase) effort:** ~87–124 engineer-days (**≈90 days midpoint** for 1 engineer; ~2 months for a small team).

## End-state at 90 days
- **No open Critical or High findings.**
- **No Security-maturity area below *Good*.**
- **HIPAA §164.312 safeguards all Compliant** (or documented-accepted); TEFCA/FHIR compliant with preventive enforcement; **WCAG 2.2 AA** across the app.
- **HA + CD + tests + monitoring** in place; public data-plane hardened.
- **Overall grade ~8.0/10 — production-ready for federal PHI use**, with an evidence base suitable for ONC review and FedRAMP/SOC 2 pursuit.
