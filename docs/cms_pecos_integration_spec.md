# FINAL SCOPE — TEFCA Evidence Architecture

Implement the TEFCA evidence-layer enhancement in ONE controlled development run.

Current QA/retesting is complete.

Before changing code:

1. Record current Git commit.
2. Record current passing test baseline.
3. Confirm zero existing failures.
4. Create a dedicated feature branch.
5. Record current database migration state.
6. Inspect the existing TEFCA architecture and actual ONC/HHS/RCE data.
7. Verify all current CMS datasets, schemas, fields, relational keys, and API endpoints against official CMS documentation.
8. Then implement.

DEV ONLY.
DO NOT deploy to production.

## PHASE 1 — IMPLEMENT NOW

### CMS / PECOS DATA SOURCES

Integrate the following evidence sources:

### 1. CMS PECOS Enrollment

Use the Medicare Fee-For-Service Public Provider Enrollment data.

API endpoint confirmed:
  https://data.cms.gov/data-api/v1/dataset/2457ea29-fc82-48b0-86ec-3b0755de7515/data

Keyless. No BAA required. Public domain. Quarterly updates.

Purpose: Medicare Enrollment Evidence.

Determine whether an applicable provider/entity has an approved Medicare enrollment represented in the public PECOS-derived data.

Do NOT use PECOS as the primary authority for NPI identity. NPPES remains the primary NPI identity source.

### 2. CMS Revoked Medicare Providers and Suppliers

Purpose: Medicare Revocation Evidence.

Keep this logically separate from PECOS enrollment. A potential revocation match must result in analyst REVIEW unless the approved ARC methodology explicitly defines a deterministic failure condition.

Do not automatically reject an entity based solely on a potential API/data match.

### 3. PECOS Practice Location

Use the PRACTICE_LOCATION relationship contained in the Medicare FFS Public Provider Enrollment Files.

Purpose: Address Corroboration.

CMS documents the relationship as:
  ENROLLMENT.ENRLMT_ID → PRACTICE_LOCATION.ENRLMT_ID → CITY_NAME, STATE_CD, ZIP_CD

Verify the current official schema before implementation. Do not invent field names or joins.

### 4. PECOS Reassignment

Use the REASSIGNMENT relationship contained in the Medicare FFS Public Provider Enrollment Files.

Purpose: Medicare Provider ↔ Organization Relationship Corroboration.

CMS documents:
  REASGN_BNFT_ENRLMT_ID = Individual practitioner enrollment
  RCV_BNFT_ENRLMT_ID = Entity receiving reassigned Medicare benefits

Join these identifiers to ENROLLMENT.ENRLMT_ID to obtain the associated provider identity information.

IMPORTANT: Do NOT substitute the CMS "Revalidation Reassignment List" for the full PPEF reassignment relationship. They are different datasets.

IMPORTANT: PECOS Enrollment, Practice Location, and Reassignment are NOT three unrelated CMS systems. They are relational components of the Medicare FFS Public Provider Enrollment Files (PPEF). CMS documents five relational files — Enrollment, Reassignment, Practice Location, Secondary Specialty, and Additional NPIs — with ENRLMT_ID providing the linkage.

## SIX EVIDENCE DIMENSIONS

Do NOT count APIs. Organize evidence into six verification dimensions.

### D1 — IDENTITY
Primary evidence: NPPES, RCE/TEFCA Directory
Evaluate: NPI, Type 1/Type 2, legal/org name, provider name, taxonomy, provider/entity type, applicable identifiers.
PECOS may corroborate identity but must not replace NPPES as the primary NPI identity source.

### D2 — MEDICARE ENROLLMENT
Primary evidence: CMS PECOS Enrollment
Possible dispositions: PASS, REVIEW, NOT_APPLICABLE, UNAVAILABLE
A PECOS non-match is NOT an automatic TEFCA failure.

### D3 — EXCLUSION / DEBARMENT / REVOCATION
Evidence: OIG LEIE, SAM.gov, CMS Revoked Providers/Suppliers
Keep each result separately identifiable. Do not collapse three different controls into a generic "federal check passed."

### D4 — ADDRESS
Evidence hierarchy:
  ONC/HHS/RCE Supplied Address → NPPES → PECOS Practice Location → USPS → Official Entrant Website (supplemental only)
Compare rather than overwrite. Possible results: MATCH, PARTIAL_MATCH, CONFLICT, NOT_FOUND, UNAVAILABLE.
Preserve each source value and the normalized comparison.

### D5 — TEFCA ALIGNMENT
Use the actual ONC/HHS/RCE data available to DocuAction. Evaluate applicable: QHIN, Participant, Subparticipant, parent/child relationship, TEFCA identifiers, HCID, NPI, provider/entity type, Exchange Purpose.
Do not invent fields that ONC/HHS did not provide. Do not infer Exchange Purpose from PECOS.

### D6 — PROVIDER ↔ ORGANIZATION RELATIONSHIP
Primary TEFCA evidence: ONC/HHS/RCE supplied relationship information.
Medicare corroborative evidence: PECOS Reassignment.
Identity corroboration: NPPES, PECOS Enrollment.
RCE answers: "What is this organization's relationship within TEFCA?"
PECOS answers: "Has this practitioner reassigned Medicare benefits to this entity?"
Never treat those relationships as equivalent.

## FIVE CORE DISPOSITION STATES

For authoritative verification controls:
  PASS, FAIL, REVIEW, NOT_APPLICABLE, UNAVAILABLE

Supplemental evidence may additionally use:
  CORROBORATED, CONFLICT, INSUFFICIENT_EVIDENCE, NOT_FOUND

Do not force supplemental website evidence into PASS/FAIL.

## APPLICABILITY ENGINE

Do NOT implement simplistic rules such as "Hospital = all APIs mandatory."

Determine applicability from:
1. ONC/HHS supplied entity type
2. RCE/TEFCA classification
3. NPPES Type 1/Type 2
4. NPPES taxonomy
5. provider/organization type
6. Medicare relevance
7. existing ARC methodology
8. evidence available for the review

Examples:

Individual Healthcare Provider:
  NPPES, OIG, SAM, PECOS Enrollment when Medicare-relevant, PECOS Reassignment when organizational relationship corroboration is relevant, Address, TEFCA Alignment.

Provider Organization / Hospital / Health System:
  NPPES, PECOS Enrollment when applicable, OIG, SAM, CMS Revocation, Practice Location, PECOS Reassignment where relevant, TEFCA Alignment.
  Do NOT assume every hospital must have every type of PECOS relationship.

Public Health Agency:
  TEFCA/RCE evidence, organizational identity, SAM/OIG per methodology, address.
  PECOS: NOT_APPLICABLE unless evidence indicates Medicare relevance.

Payer / Health Plan:
  PECOS provider enrollment/reassignment: normally NOT_APPLICABLE unless specific evidence establishes applicability.

HIE / HIN / QHIN:
  RCE/TEFCA relationship, organizational identity, SAM/OIG, address.
  PECOS: normally NOT_APPLICABLE unless specific provider circumstances establish applicability.

Inspect the actual ONC/HHS data and existing ARC methodology before finalizing rules.

## RCE / ONC DATA INSPECTION

Before coding D5 or D6, inspect the actual data supplied by ONC/HHS.

Determine which fields are actually available. Look for:
  Participant, Subparticipant, Child Entry, Parent organization, QHIN, NPI, Type 1/Type 2 NPI, HCID, TEFCA identifier, Organization name, Provider/entity type, Address, Exchange Purpose, Relationship identifiers.

Do NOT assume these fields exist. Create a mapping:
  ONC/HHS FIELD → SEMANTIC MEANING → EVIDENCE DIMENSION → AUTHORITATIVE SOURCE → EXTERNAL CORROBORATION SOURCE

## PECOS REASSIGNMENT RULES

RCE Relationship + PECOS Reassignment Agree:
  Result: CORROBORATED. Do not turn into numerical scoring.

RCE Relationship Exists + No PECOS Reassignment:
  NOT_APPLICABLE or REVIEW based on Medicare applicability. Never automatically FAIL.

RCE and PECOS Show Different Organizations:
  Result: REVIEW. Do not automatically treat as conflict/failure. A practitioner may have multiple legitimate relationships. Present all to the analyst.

Non-provider TEFCA Entity:
  PECOS Reassignment: NOT_APPLICABLE unless specific circumstances establish applicability.

## TYPE 1 / TYPE 2 NPI ALIGNMENT

Add explicit comparison logic. Evaluate:
  RCE supplied NPI ↔ NPPES NPI ↔ NPPES entity type ↔ NPPES taxonomy ↔ PECOS Enrollment ↔ PECOS Reassignment where applicable.

Do not assume Type 1 and Type 2 NPIs serve the same purpose. Where RCE data identifies an organizational Type 2 NPI, compare organizational identity separately from individual practitioner Type 1 NPI.

## EVIDENCE DATA MODEL

Do not simply store "connector = success."

The evidence record should support:
  entity_id, review_id/review_cycle_id, evidence_dimension, source, source_dataset, source_record_identifier, query_identifier, query_timestamp, dataset/as-of date, raw result reference, normalized evidence, fields evaluated, field-level matches, field-level conflicts, disposition, analyst notes, retrieved_at, reviewed_by, reviewed_at, version/audit information.

Do not destroy or replace existing historical evidence when a verification is rerun. Maintain traceability.

## ADDRESS RECONCILIATION

Normalize: street suffix, suite/unit, capitalization, punctuation, ZIP/ZIP+4, state, city, organization-location association.

Never silently replace the ONC/HHS submitted address.

Store: SOURCE, ORIGINAL VALUE, NORMALIZED VALUE, MATCH RESULT, RETRIEVAL TIME, DATASET/AS-OF DATE.

## WEBSITE CORROBORATION

Secondary evidence only. Possible results: CORROBORATED, CONFLICT, NOT_FOUND, UNAVAILABLE, INSUFFICIENT_EVIDENCE, NOT_APPLICABLE.

Website unavailable (DNS, timeout, 403, 429, 5xx, SSL, anti-bot) must NOT negatively affect entity determination. No official website found must NOT automatically negatively affect the entity.

## TIN / EIN

OUT OF SCOPE. Do NOT assume PECOS public data exposes EIN/TIN. Do not scrape, derive, fabricate, or expose tax identifiers. Document the gap.

## DECISION WORKSPACE

Do NOT redesign the application. Update only what is required.

Group evidence by dimension:
  IDENTITY → MEDICARE ENROLLMENT → EXCLUSION/REVOCATION → ADDRESS → TEFCA ALIGNMENT → PROVIDER ↔ ORGANIZATION

Allow evidence drill-down using existing UI patterns. Show: source, disposition, fields checked, matches, conflicts, retrieval timestamp, dataset/as-of date, analyst notes, evidence provenance.

Do not rely solely on color for status. Maintain Section 508/WCAG accessibility.

## CONNECTOR HUB

IMPORTANT: PECOS Enrollment, Practice Location, Reassignment, and Additional NPIs are related PPEF data components. Do not misrepresent them as four unrelated external systems merely to show "four new APIs."

The UI may represent:
  CMS / PECOS Public Provider Enrollment (with capabilities: Enrollment, Practice Location, Reassignment, Additional NPIs)
  CMS Revoked Providers/Suppliers (separate)

Choose the representation that accurately reflects the underlying systems.

## HEALTH CHECK

Monitor CMS capabilities. Distinguish: AVAILABLE, DEGRADED, UNAVAILABLE.
An upstream CMS outage must never become an entity verification failure.

## AUDIT TRAIL

Do NOT remove or replace the existing audit trail. Extend as required.

Every evidence determination must be reproducible: entity/review ID, evidence dimension, source, dataset, query identifier, query timestamp, dataset/as-of date, normalized result, original source values, fields evaluated, matches, conflicts, disposition, rule applied, analyst action, analyst notes, final determination relationship.

Historical evidence must not be overwritten.

## NO API COUNTING

NEVER implement "4 APIs passed = 90% verified" or "6 APIs matched = stronger B1." Evaluate whether each required evidence dimension has sufficient authoritative evidence. Correlated CMS data must not be counted as independent votes.

## NO UNAPPROVED B1-B4 CHANGE

Do not modify B1-B4 methodology unless the existing approved methodology explicitly requires it.

## NO STRUCTURAL APPLICATION REDESIGN

Do NOT redesign: 11 TEFCA pages, TEFCA Registry, Import workflow, RBAC, Reports, AI Control Plane, FCC Bulletin, unrelated DocuAction modules.

## TESTING

Run complete existing regression suite plus new tests. At minimum test:
  PECOS enrollment match, non-match, Medicare applicability, NOT_APPLICABLE, CMS unavailable, timeout, malformed response, rate limiting, Revocation match, multiple/false-positive handling, Practice Location linkage, multiple locations, missing location, Reassignment linkage, multiple reassignments, not applicable, conflicting RCE/PECOS relationships, Type 1/Type 2 NPI, multiple NPI, Additional NPIs when MULTIPLE_NPI_FLAG=Y, address normalization, address conflict, website unavailable, website conflict, evidence provenance, historical preservation, analyst override, Decision Workspace, Connector Hub, health monitoring, RBAC regression, Registry regression, Reports regression, Audit Trail regression, accessibility regression, complete TEFCA workflow regression.

No existing test may be deleted, weakened, skipped, or altered merely to make the enhancement pass.

---

# CMS TECHNICAL AMENDMENTS (MANDATORY)

## AMENDMENT 1: CMS REVOCATION — NEGATIVE RESULT SEMANTICS

Do NOT interpret absence from the CMS Revoked Medicare Providers and Suppliers dataset as proof that the provider is currently enrolled, eligible to enroll, in overall good standing, or that no other enrollment has an issue.

CMS documents this dataset as providers/suppliers currently revoked and under an active re-enrollment bar.

Represent a negative lookup as:
  NO_ACTIVE_REVOCATION_RECORD_FOUND

This satisfies ONLY the CMS Revocation evidence check.

Use PECOS Enrollment SEPARATELY to determine whether an applicable Medicare enrollment appears in the PPEF.

A positive potential revocation match remains: REVIEW pending identity matching and analyst evaluation.

Capture where available: ENRLMT_ID, NPI, provider/organization name, STATE_CD, PROVIDER_TYPE_DESC, REVOCATION_RSN, REVOCATION_EFCTV_DT, REENROLLMENT_BAR_EXPRTN_DT, dataset/as-of date.

## AMENDMENT 2: ADDITIONAL NPIs

The Medicare FFS PPEF includes an ADDITIONAL_NPIS relational file.

When MULTIPLE_NPI_FLAG = Y, do not conclude that an ONC/RCE NPI conflicts with PECOS merely because it differs from the NPI in the primary ENROLLMENT record.

Consult the ADDITIONAL_NPIS relationship using ENRLMT_ID and determine whether the supplied NPI appears among the additional NPIs.

Evidence should preserve: primary PECOS NPI, additional PECOS NPI(s), RCE/ONC supplied NPI, NPPES result, matching relationship, ENRLMT_ID, PAC ID where available.

Do NOT treat multiple NPIs as multiple independent evidence votes. They belong to the same underlying PECOS provider/enrollment evidence.

## AMENDMENT 3: PECOS RELATIONAL MODEL

Treat the PPEF as a relational evidence source:

```
  ENROLLMENT (ENRLMT_ID)
    ├── PRACTICE_LOCATION (Address Evidence)
    ├── REASSIGNMENT (Provider ↔ Org Relationship)
    ├── ADDITIONAL_NPIS (NPI Corroboration)
    └── SECONDARY_SPECIALTY (Taxonomy Corroboration)
```

Do not implement these as unrelated external systems. Preserve source provenance down to the specific PPEF component used.

## AMENDMENT 4: PECOS PRACTICE LOCATION — ONE-TO-MANY

Account for one-to-many relationships. A provider/enrollment may have multiple enrollment IDs, multiple practice locations, multiple reassignments, and multiple NPIs.

Do NOT arbitrarily select the first API result. Collect applicable records and reconcile against the ONC/RCE entity.

CMS documents that some individual enrollment scenarios may legitimately have no Practice Location record. Therefore NO_PRACTICE_LOCATION must not automatically become a verification failure. Determine applicability before assigning REVIEW or NOT_APPLICABLE.

## AMENDMENT 5: REASSIGNMENT + LOCATION CORRELATION

Where useful for a provider-to-organization review:
  RCE Practitioner NPI → NPPES Identity → PECOS Enrollment → REASSIGNMENT → Receiving Entity ENRLMT_ID → PECOS Enrollment → Receiving Organization NPI/Name → PRACTICE_LOCATION → Address Corroboration

This relationship is Medicare-specific corroboration. It must NOT be interpreted as the authoritative TEFCA Participant/Subparticipant relationship. RCE/ONC-supplied TEFCA relationship data remains the primary evidence.

## AMENDMENT 6: POINT-IN-TIME EVIDENCE

For every CMS lookup store both:
  - query/retrieval timestamp
  - CMS dataset/as-of/version date where available

Do not label CMS public enrollment evidence as real-time. Historical evidence used for an ARC determination must remain preserved even after CMS publishes a newer dataset.

---

# FINAL EXECUTION ORDER

1. Capture tested baseline.
2. Create feature branch.
3. Inspect repository.
4. Inspect actual ONC/HHS/RCE input data.
5. Inspect existing ARC methodology.
6. Verify official CMS schemas/endpoints.
7. Produce impact assessment.
8. Extend evidence model.
9. Implement PECOS Enrollment.
10. Implement CMS Revocation.
11. Implement PECOS Practice Location.
12. Implement PECOS Reassignment.
13. Implement ADDITIONAL_NPIS awareness.
14. Implement applicability engine.
15. Implement RCE/NPPES/PECOS cross-validation.
16. Implement address reconciliation.
17. Implement optional website corroboration.
18. Update Decision Workspace.
19. Update Connector Hub.
20. Update health monitoring.
21. Extend evidence provenance/audit logging.
22. Run new tests.
23. Run COMPLETE regression suite.
24. Validate development UI.
25. Produce implementation report.
26. STOP.

DO NOT deploy to production.

## FINAL IMPLEMENTATION REPORT

Return:
1. Starting Git commit
2. Feature branch
3. Original test baseline
4. Files/components changed
5. Database migrations
6. CMS datasets/endpoints actually used
7. CMS fields and relational keys actually used
8. PPEF relational model implemented
9. ADDITIONAL_NPIS handling
10. ONC/HHS/RCE fields discovered
11. Evidence-dimension mapping
12. Applicability rules implemented
13. Reassignment logic
14. Address reconciliation logic
15. Website fallback behavior
16. Revocation negative-result semantics
17. Point-in-time evidence storage
18. UI changes
19. Connector/health changes
20. Audit changes
21. New tests
22. Complete regression results
23. Performance impact
24. Security/privacy findings
25. Unresolved TIN/EIN issue
26. Warnings/regressions
27. Recommendations for next enhancement

STOP and wait for approval. No production deployment.
