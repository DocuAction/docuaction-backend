# DocuAction Governing Invariant Map

**Status:** Governance documentation only. Version 1.0, 2026-09-13. Authoritative list of the permanent architecture, security, isolation, governance and Entity / Evidence / Decision Intelligence truth invariants. Owner: Imran Siddiqui. Adding, removing or redefining an invariant requires the owner's approval and a version bump; an audit never redefines an invariant to make a list match.

**Reconciliation note (2026-09-13).** The 18 minimum Entity / Evidence / Decision Intelligence truth invariants supplied in the audit prompt (section 7D) were reconciled against the 31 governing invariants already recorded in `INDEPENDENT_AI_AUDIT_REPORT_2026-09-13.md`. Twelve of the 18 are the same statement as an existing invariant and are mapped to it without change. Six are new and are appended as I-32 to I-37. GOVERNING_INVARIANTS_TOTAL = 37.

Enforcement classes: **EXECUTABLE** (a committed repository test fails when the invariant is broken), **PROBE** (verified by an auditor probe against the frozen SHA but no committed test yet), **SOURCE** (verified by static inspection only), **PLATFORM** (enforced by GitHub / Azure configuration, not by code), **N/A** (permissive statement, nothing to break).

## Part A. Architecture, security, isolation and governance invariants (I-1 to I-31)

| ID | Invariant | Enforcement | Where |
|---|---|---|---|
| I-1 | TEFCA -> Core allowed | N/A | design |
| I-2 | Core -> TEFCA prohibited | EXECUTABLE | `tests/test_core_boundary.py`, `tests/test_entity_intelligence_isolation.py` |
| I-3 | Government source data immutable | SOURCE + PLATFORM | Area 1 grants migration (`20260828_area1`), `PPEF_BULK_INGEST_ENABLED` flag; `test_human_review_workflow::test_government_rows_are_untouched` (shared-DB coupled, see note) |
| I-4 | System observation != contractual finding | EXECUTABLE | `assessment.FORBIDDEN_TERMS`; `test_entity_intelligence_adversarial.py`, `_delta_assessment.py` |
| I-5 | System Evidence Assessment != human determination | EXECUTABLE | same as I-4; `profile.py` "No determination" |
| I-6 | Human determination != independent QA | EXECUTABLE (DB) | `test_final_e2e_acceptance::test_an_analyst_cannot_approve_their_own_determination`, `test_human_review_workflow::test_the_analyst_cannot_qa_their_own_determination`, `test_priority_review_operational::test_an_analyst_cannot_qa_their_own_priority_review`, `test_qa_gate::test_sod_trigger_refuses_self_review` |
| I-7 | Maker != checker | EXECUTABLE in-app (`test_phase9_operational::test_refuses_analyst_self_approval`, `test_learning_center::test_self_approval_is_prohibited`); PLATFORM gap at repository level (AUD-02) and audit level (AUD-03) |
| I-8 | NPI != credential | EXECUTABLE | `authority_matrix.MATRIX_V1` NPPES_V2 CREDENTIALING = CANNOT_ALONE_ESTABLISH; `test_entity_intelligence_architecture_v1.py` |
| I-9 | Business registration != healthcare license | EXECUTABLE | STATE_CORPORATE_REGISTRY LICENSURE = CANNOT_ALONE_ESTABLISH; `_architecture_v1.py` |
| I-10 | Foreign qualification != practice location | EXECUTABLE | DOMESTIC_FOREIGN_ROLE vs PRACTICE_LOCATION rows; PRINCIPAL_OFFICE vs practice -> ROLE_ASSIGNMENT_DIFFERS; `_architecture_v1.py` |
| I-11 | Registered agent != site of care | EXECUTABLE | REGISTERED_AGENT_ADDRESS vs practice -> ROLE_ASSIGNMENT_DIFFERS; `_architecture_v1.py` |
| I-12 | Address deliverability != entity legitimacy | SOURCE | no USPS / deliverability input in `app/core/entity_intelligence`; assessment vocabulary has no legitimacy term |
| I-13 | CMS enrollment != TEFCA eligibility | EXECUTABLE | CMS_PPEF TEFCA_ELIGIBILITY = CANNOT_ALONE_ESTABLISH, `NO_SINGLE_SOURCE_QUESTIONS`; `_participation_policy.py`, `_architecture_v1.py` |
| I-14 | Source absence != adverse determination | EXECUTABLE | `absence()` -> INSUFFICIENT_EVIDENCE / SOURCE_UNAVAILABLE; `_hardening.py`, `_comparison.py` |
| I-15 | Internal operational target != contractual SLA | SOURCE | LMS content grep; `test_learning_center.py` content-truth checks |
| I-16 | Proposed / submitted methodology != COR-accepted methodology | SOURCE | report status labels ("Draft, awaiting PM review"); D2 unaccepted wording |
| I-17 | Synthetic data != Government data | EXECUTABLE | `test_report_cross_format_reconciliation.py` (DEVELOPMENT / TEST label), `test_data_provenance.py` |
| I-18 | Source voting prohibited | EXECUTABLE | four weak vs one authoritative -> CONFLICTING_EVIDENCE; `_adversarial.py`, `_architecture_v1.py` |
| I-19 | Uncalibrated numeric AI confidence prohibited | EXECUTABLE | no confidence / score / probability / weight in output; `_adversarial.py` |
| I-20 | Evidence provenance preserved | EXECUTABLE | `Provenance` required on every observation; `_isolation.py`, `test_data_provenance.py` |
| I-21 | Feature changes require LMS impact review | SOURCE (process) | `test_lms_sync.py` covers version sync only |
| I-22 | Government branding defaults OFF | EXECUTABLE | branding double gate tests; regenerated artifact had no mark |
| I-23 | Secrets never enter source or frontend bundles | EXECUTABLE + PLATFORM | gitleaks workflow, `security-scan`; auditor bundle scan |
| I-24 | Entity Intelligence feature OFF until authorized | EXECUTABLE | `flags._as_bool` strict parsing; `_hardening.py`, `_security.py` |
| I-25 | IQVIA AWAITING_SCHEMA / AWAITING_TERMS until actual schema, terms, rights and authorization | EXECUTABLE | `iqvia_onekey.adapter` SchemaUnknown, `operational_use_permitted()`; `_security.py` |
| I-26 | Google research / governance controlled until authorized | EXECUTABLE + SOURCE | flag default False; no Google code path |
| I-27 | State-source acquisition jurisdiction-specific, explicit modes | SOURCE | `ports.AcquisitionMode`; no live adapters |
| I-28 | AI does not cross the human determination boundary | EXECUTABLE | closed assessment vocabulary; `service.py` writes no determination |
| I-29 | PROD requires separate human authorization | PLATFORM | GitHub `production` environment reviewer |
| I-30 | Government-data writes require an authorized workflow | SOURCE + PLATFORM | ingest flags, Area 1 grants, handshake-issue workflows |
| I-31 | Source acquisition method must not leak into evidence interpretation | PROBE | identical assessment across four transport paths; no committed test yet |

## Part B. Entity / Evidence / Decision Intelligence truth invariants (the 18-item minimum, reconciled)

| # | Statement | Maps to | Status 2026-09-13 | Evidence |
|---|---|---|---|---|
| D-1 | NPI != credential | I-8 | PASS | matrix: NPI_OBSERVATION CAN_SUPPORT; CREDENTIALING, LICENSURE CANNOT_ALONE_ESTABLISH |
| D-2 | business registration != healthcare license | I-9 | PASS | matrix: CORPORATE_REGISTRATION CAN_SUPPORT; LICENSURE, CREDENTIALING CANNOT_ALONE_ESTABLISH |
| D-3 | foreign qualification != practice location | I-10 | PASS | matrix rows; PRINCIPAL_OFFICE at the delivered practice address -> ROLE_ASSIGNMENT_DIFFERS |
| D-4 | registered agent != site of care | I-11 | PASS | ROLE_ASSIGNMENT_DIFFERS; registry SITE_OF_CARE CANNOT_ALONE_ESTABLISH |
| D-5 | address deliverability != entity legitimacy | I-12 | PASS (source) | no deliverability input, no legitimacy output term |
| D-6 | CMS enrollment != TEFCA eligibility | I-13 | PASS | CMS_PPEF: MEDICARE_ENROLLMENT_OBSERVATION CAN_SUPPORT, TEFCA_ELIGIBILITY CANNOT_ALONE_ESTABLISH; no source may establish it alone |
| D-7 | source absence != adverse determination | I-14 | PASS | NOT_FOUND absence -> INSUFFICIENT_EVIDENCE, basis MISSING_IDENTIFIER; no adverse term |
| D-8 | system assessment != human determination | I-5 | PASS | six-value closed vocabulary disjoint from FORBIDDEN_TERMS (incl. DETERMINATION) |
| D-9 | same address != same role | **I-32 (new)** | PASS with caveat | HQ, mailing, registered agent, principal office at the same address -> ROLE_ASSIGNMENT_DIFFERS; UNKNOWN_SOURCE_ROLE -> NORMALIZED_LOCATION_MATCH (finding AUD-11) |
| D-10 | multiple sources != automatic truth | **I-33 (new)** | PASS | four agreeing sources -> EVIDENCE_CORROBORATES; vocabulary has no TRUE / VERIFIED / CONFIRMED / VALID |
| D-11 | source disagreement != contractual discrepancy | **I-34 (new)** | PASS | disagreement -> CONFLICTING_EVIDENCE; no "discrepancy" or Task 3 category term in output |
| D-12 | analyst determination != independent QA approval | I-6 | PASS (DB) | 4 route/trigger tests passed against an ephemeral migrated database; 3 data-dependent tests skipped |
| D-13 | source voting is prohibited | I-18 | PASS | four weak vs one authoritative -> CONFLICTING_EVIDENCE; the only "vote" mentions in the package are prohibitions in docstrings |
| D-14 | uncalibrated numeric confidence is prohibited | I-19 | PASS | no confidence / score / probability / weight / % in serialized output |
| D-15 | business existence != healthcare authority | **I-35 (new)** | PASS | CORPORATE_STATUS CAN_SUPPORT; LICENSURE, CREDENTIALING, TEFCA_ELIGIBILITY CANNOT_ALONE_ESTABLISH |
| D-16 | evidence observation != contractual finding | I-4 | PASS | vocabulary disjoint from NON_COMPLIANT / FINDING / Task 3 categories |
| D-17 | automated corroboration != analyst determination | **I-36 (new)** | PASS (probe + source) | EVIDENCE_CORROBORATES is an assessment value; `service.py` writes no determination |
| D-18 | analyst determination != final program result until independent QA / maker-checker control is satisfied | **I-37 (new)** | PASS (DB) | `test_priority_review_operational::test_only_an_independent_qa_approval_releases_the_result` and the D-12 tests passed on the ephemeral database |

## Part C. Counts (audit of 2026-09-13, frozen EI head 4ef1bba, main 75383cf)

```
GOVERNING_INVARIANTS_TOTAL          = 37   (31 existing + 6 new: I-32..I-37)
INVARIANTS_TESTED                   = 36   (all except I-1, which is permissive)
INVARIANTS_PASS                     = 36   (D-9 / I-32 passes with the AUD-11 caveat for UNKNOWN_SOURCE_ROLE)
INVARIANTS_FAIL                     = 0
INVARIANTS_MISSING_TEST_COVERAGE    = 11   (no committed executable test: I-3, I-12, I-15, I-16, I-21, I-27, I-30, I-31, I-33, I-34, I-36)
```

Committed executable coverage: 26 of 37. Platform-enforced without code: I-29 (and I-7 partially). The 11 without committed tests are covered tonight only by auditor probes or static inspection; the builder should convert the probe cases for I-31, I-33, I-34, I-36 and the UNKNOWN_SOURCE_ROLE case of I-32 into repository tests (finding AUD-20).

## Part D. Notes

- `test_human_review_workflow::test_government_rows_are_untouched` asserts that at least 43 pre-existing review records exist; it fails on any freshly migrated empty database and therefore measures shared-QA state rather than code (AUD-21, INFORMATIONAL).
- Invariant IDs are stable. Never renumber; append.
