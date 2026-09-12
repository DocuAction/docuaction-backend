# Entity Identity & Location Intelligence — engineering artifacts

Isolated foundation, feature OFF, DEV engineering only. Draft PR #54; not merged, not deployed. Start with the architecture, then the rules, then the overnight summary.

## Foundation (2026-09-11)

1. [ENTITY_IDENTITY_INTELLIGENCE_ARCHITECTURE.md](ENTITY_IDENTITY_INTELLIGENCE_ARCHITECTURE.md)
2. [NPPES_V2_IDENTITY_MAPPING.md](NPPES_V2_IDENTITY_MAPPING.md) — now with the per-code traceability table
3. [IQVIA_ONEKEY_ADAPTER_CONTRACT.md](IQVIA_ONEKEY_ADAPTER_CONTRACT.md)
4. [GOOGLE_ADDRESS_COMPLIANCE_BLUEPRINT.md](GOOGLE_ADDRESS_COMPLIANCE_BLUEPRINT.md)
5. [STATE_REGISTRY_CONNECTOR_DESIGN.md](STATE_REGISTRY_CONNECTOR_DESIGN.md)
6. [IDENTITY_COMPARISON_RULES.md](IDENTITY_COMPARISON_RULES.md)
7. [HISTORICAL_DELTA_RULES.md](HISTORICAL_DELTA_RULES.md)
8. [SYSTEM_EVIDENCE_ASSESSMENT_MODEL.md](SYSTEM_EVIDENCE_ASSESSMENT_MODEL.md)
9. [QA_BASELINE_ISOLATION_PROOF.md](QA_BASELINE_ISOLATION_PROOF.md)

## Overnight research, hardening & validation sprint (2026-09-12)

10. [OVERNIGHT_EXECUTIVE_SUMMARY.md](OVERNIGHT_EXECUTIVE_SUMMARY.md) — read this first in the morning; decision gates A–H
11. [ENTITY_INTELLIGENCE_PERSISTENCE_ADR.md](ENTITY_INTELLIGENCE_PERSISTENCE_ADR.md) — options A/B/C, recommendation B
12. [EVIDENCE_SOURCE_DATA_RIGHTS_MODEL.md](EVIDENCE_SOURCE_DATA_RIGHTS_MODEL.md)
13. [RCE_IQVIA_DATA_INTAKE_CHECKLIST.md](RCE_IQVIA_DATA_INTAKE_CHECKLIST.md) — questions only
14. [NPPES_ACQUISITION_AND_REFRESH_DESIGN.md](NPPES_ACQUISITION_AND_REFRESH_DESIGN.md) — design, no downloader
15. [ENTITY_INTELLIGENCE_PERFORMANCE_REPORT.md](ENTITY_INTELLIGENCE_PERFORMANCE_REPORT.md) — 2k–50k synthetic, PostgreSQL validation, security review
16. [ENTITY_INTELLIGENCE_TERMINOLOGY.md](ENTITY_INTELLIGENCE_TERMINOLOGY.md)
17. [ENTITY_INTELLIGENCE_TASK_2_5_INTEGRATION_ANALYSIS.md](ENTITY_INTELLIGENCE_TASK_2_5_INTEGRATION_ANALYSIS.md)
18. [ENTITY_INTELLIGENCE_FUTURE_UI_SPEC.md](ENTITY_INTELLIGENCE_FUTURE_UI_SPEC.md) — not built
19. [OBSERVABILITY_AND_FAILURE_MODEL.md](OBSERVABILITY_AND_FAILURE_MODEL.md)
20. [HUMAN_AND_AI_BOUNDARY.md](HUMAN_AND_AI_BOUNDARY.md)
21. [ACTIVE_FALSIFICATION_FUTURE_EXTENSION.md](ACTIVE_FALSIFICATION_FUTURE_EXTENSION.md) — design note

Code: `app/core/entity_intelligence/` (Core incl. `intake_safety.py`), `app/evidence_sources/` (adapters), `tests/test_entity_intelligence_*.py` (8 files, 342 tests), `tests/ei_fixtures.py` (synthetic only), `scripts/ei_perf.py`, `scripts/ei_pg_isolated_validation.py`.

## OVERNIGHT VALIDATION SUMMARY

| Item | Status |
|---|---|
| Baseline regression | 3202 passed / 333 skipped / 0 failed (baseline existing 2860 + new 342) |
| OpenAPI vs main | byte-identical (411 paths, 106 schemas) |
| Alembic head | `20260903_delivery_grants`, unchanged |
| Pre-existing files changed | `app/core/config.py` only (+12 lines, five flags default False) |
| Research re-verified | NPPES V2 from the CMS readme v.2 (May 12, 2026), CodeValues (Feb 1, 2025), NPI_Files.html and the weekly sample; IQVIA public page; Google policies page (terms page only partially retrievable — recorded) |
| New verified NPPES facts | type code 6 is a pointer to the reference file, not a name type; `<UNAVAIL>` placeholder in EIN / other-name / parent-TIN columns; both handled and tested |
| Defects fixed (isolated) | flag string parsing (`"false"` was truthy); `<UNAVAIL>` would have become a name; pointer code 6 would have produced an UNKNOWN-kind name and false ambiguity; parse status was implicit; csv-module errors could raise mid-file; import-time `assert` |
| Security | bandit 0 issues; secret/network/exec/logging static checks tested; intake safety helpers with tests |
| Performance | ≈1.1 ms per entity end-to-end; 50k entities in 60 s single-process; linear |
| PostgreSQL | POSTGRESQL_ISOLATED_VALIDATION = PASSED (ephemeral local cluster, deleted) |
| Persistence | ADR written; decision required (gate D) |
| Not changed | Tasks 1–6 code, RBAC, auth, reports, LMS (1.2.0), frontend, shared DEV DB, PROD, OIDC, secrets |
| Not merged, not deployed | PR #54 remains DRAFT |
