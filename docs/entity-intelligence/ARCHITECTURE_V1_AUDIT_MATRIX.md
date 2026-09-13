# Architecture v1.0 — audit of the existing isolated foundation (PR #54)

Audit performed before writing code (overnight master sprint, 2026-09-13). One architecture; nothing parallel. Every requirement below was checked against the branch at commit 388622d and then reused, modified or added.

| REQUIREMENT | EXISTS | PARTIAL | MISSING | REUSE | MODIFY | NEW | RATIONALE |
|---|---|---|---|---|---|---|---|
| Observation model with provenance | ✔ | | | ✔ | | | `EvidenceObservation`, `Provenance`, `SourceVersionRef` reused unchanged |
| NPPES V2 parser / adapter | ✔ | | | ✔ | | | unchanged; rights descriptor status corrected (below) |
| Historical delta | ✔ | ✔ | | ✔ | ✔ | | added `SOURCE_NO_LONGER_REPORTS_OBSERVATION` for evidence-side removals (PROOF 3) |
| Comparison logic | ✔ | ✔ | | ✔ | ✔ | | added `ROLE_ASSIGNMENT_DIFFERS` (PROOF 2) and `RELATIONSHIP_PERIOD_DIFFERS`; both ambiguous-class (not match, not conflict) |
| System Evidence Assessment | ✔ | | | ✔ | | | closed vocabulary unchanged; new signals map into existing classes; no automated contractual determination |
| IQVIA stub | ✔ | ✔ | | ✔ | ✔ | | added `ARRIVAL_STATUS`, ten-step `ARRIVAL_PROTOCOL`, `operational_use_permitted()` (false without human authorization) |
| State provider design | ✔ | ✔ | | ✔ | ✔ | | added `AcquisitionMode` (OFFICIAL_API … UNSUPPORTED) on `StateRegistryCapability`; still no adapter, no scraper |
| Google blueprint | ✔ | | | ✔ | | | unchanged; RESEARCH_ONLY |
| Source rights | ✔ | ✔ | | ✔ | ✔ | | statuses REVIEWED / ASSUMED_PUBLIC_DOMAIN / TERMS_REVIEW_REQUIRED / PENDING / NOT_PERMITTED / UNKNOWN; decision provenance fields; human-authorization guard in `__post_init__`; NPPES moved from DOCUMENTED to ASSUMED_PUBLIC_DOMAIN with no retention/redistribution right |
| Security tests | ✔ | | | ✔ | | | unchanged; new modules scanned by the same static checks |
| Feature flags | ✔ | | | ✔ | | | unchanged; master OFF |
| Isolated persistence | ✔ | | | ✔ | | | unchanged; no new table |
| Evidence inquiry subjects (ORGANIZATION / IDENTIFIER / LOCATION / RELATIONSHIP) | | | ✔ | | | ✔ | `profile.py` — `EvidenceInquiry`, `InquirySubjectType` |
| Entity Evidence Profile (IDENTITY / BUSINESS_IDENTITY / LOCATION / HEALTHCARE_AUTHORITY / RELATIONSHIPS / HISTORY) | | | ✔ | | | ✔ | `profile.py` — arrangement of observations by facet with sources; adds no facts |
| Explicit location roles (10 named roles) | | ✔ | | ✔ | ✔ | | `LocationRole` extended; foundation roles retained; `CARE_SITE_ROLES` / `ADMINISTRATIVE_ROLES` sets |
| Address observation preserves raw + normalized key + rule version | | ✔ | | ✔ | ✔ | | `location_observation()` helper; `NORMALIZATION_VERSION` stamped on every normalized address |
| Typed relationships (subject, relationship, object, direction, source, validity, observation time, raw, normalized, program context, provenance) | | ✔ | | ✔ | ✔ | | `relationship_observation()` helper + `RelationshipDirection`; role `"<PROGRAM>:<KIND>"` |
| SOURCE_QUESTION_AUTHORITY_MATRIX, versioned, no voting | | | ✔ | | | ✔ | `authority_matrix.py` — `MATRIX_V1` (matrix_version, effective_date, supersedes_version, change_reason, approved_by = PENDING_HUMAN_APPROVAL); answers are CAN_SUPPORT / CANNOT_ALONE_ESTABLISH / NOT_APPLICABLE / UNKNOWN; no numbers |
| PPEF five-file relational model | ✔ (design) | | | ✔ | | | matrix entries for ENROLLMENT / REASSIGNMENT / PRACTICE_LOCATION / ADDITIONAL_NPIS (SECONDARY_SPECIALTY recorded as not identity-relevant); adapter remains DESIGN reading the frozen Task 3 snapshots |
| Acquisition / interpretation firewall | ✔ | | | ✔ | | | reasoning modules import no acquisition code; new `profile.py` and `evidence_plan.py` read observations/records only (isolation tests) |
| Deterministic assessment vocabulary + reason codes | ✔ | ✔ | | ✔ | ✔ | | reason codes ROLE_ASSIGNMENT_DIFFERS, RELATIONSHIP_PERIOD_DIFFERS, SOURCE_NO_LONGER_REPORTS_OBSERVATION added as signals/delta types with careful templates |
| Explain-the-difference set (legal ≠ DBA, current ≠ former, corporate ≠ practice, agent ≠ site of care, domestic ≠ foreign, primary ≠ additional, program ≠ legal name, same address + different role, historical ≠ current) | ✔ | ✔ | | ✔ | ✔ | | covered by existing name/location signals plus the three new codes; kind-scoped relationships keep DOMESTIC_IN and FOREIGN_QUALIFIED_IN apart |
| Three synthetic proofs | | | ✔ | | | ✔ | `tests/test_entity_intelligence_architecture_v1.py` |
| IQVIA arrival protocol | | ✔ | | ✔ | ✔ | | see above; doc updated |
| 25K scale model (records → normalize → resolve → dedup → candidates → questions → plan) | | | ✔ | | | ✔ | `evidence_plan.py`; perf script extended with 30% duplicate records, RSS, entities/sec |
| Core → TEFCA prohibited (AST, dynamic imports) | | ✔ | | | | ✔ | `tests/test_core_boundary.py` scans every module under `app/core` (imports, importlib/`__import__` strings, module-string constants, f-strings) — passes today |
| Client-readiness lifecycle (PLATFORM_INTERNAL → INNOVATION_PREVIEW → CLIENT_OPERATIONAL) | | | ✔ | | | ✔ | recorded in this document set; the capability is PLATFORM_INTERNAL; no code promotes it |

## Not built, by rule

CMS/PECOS production connector · PPEF downloader (the frozen Task 3 job owns acquisition) · RCE policy engine · Google · LEIE / SAM adapters · state scrapers · IQVIA mapping · UI · graph database · any automated contractual category.
