# AGT-TE-006 — TEFCA ARC Validation

**Prepared for HHS/ONC · Contract:** 7571MN26F80064 · **CAGE:** 8ERE8 · **UEI:** MP2FLV1MAW93

## Environment Summary

| Field | Value |
|---|---|
| Environment | Development |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Deployment | Azure App Service (Linux) |
| Database | Azure Database for PostgreSQL Flexible Server |
| OS (test workstation) | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Build (Git SHA) | ebfcd38e067fd2b879e095eee547e40931a8e027 |
| Test Date (UTC) | 2026-08-02 |
| Contract | 7571MN26F80064 |
| CAGE | 8ERE8 |
| UEI | MP2FLV1MAW93 |


## Tool Versions

| Tool | Version |
|---|---|
| Python | 3.13.11 |
| httpx | 0.28.1 |
| pytest | 9.1.1 |
| openapi-spec-validator | 0.9.0 |
| python-docx | 1.2.0 |
| Bandit | 1.9.4 |
| OWASP ZAP | NOT AVAILABLE — no JRE / no container runtime |


## Platform Overview

The DocuAction TEFCA ARC platform ingests entity records, verifies them against authoritative federal sources, classifies each result into one of four discrepancy buckets under a versioned rule set, draws statistically-derived review samples, and produces period reports for the contracting officer.

## Task 3 Evidence — Import, Verification, Classification, Sampling, Reporting

### Entity operations

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| E1 | GET entities returns the registry | 200 with entities | HTTP 200, count=71 | PASS |
| E2 | Pagination returns distinct pages | 5 per page, no overlap | page1=5, page2=5, overlap=0 | PASS |
| E3 | Search finds a known entity | 200, >=1 match | HTTP 200, count=1 | PASS |
| E4 | CSV import creates entities | 200, imported>=1 | HTTP 200, imported=1, errors=0 | PASS |
| E5 | Invalid NPI is flagged on verification | B4 (npi_invalid) | import imported=0, bucket=None | PASS |
| E6 | Re-import of same TEFCAID updates, does not duplicate | imported=0 and exactly 1 row exists | imported=0, skipped=1, errors=0, rows=1 | PASS |
| E7 | Valid lifecycle transition is applied | draft -> pending_verification | active -> active | PASS |
| E8 | Invalid EntityLevel rejected with an error | 400 or error_count>=1 | HTTP 200, error_count=1 | PASS |


### Verification against authoritative sources

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| F1 | NPPES is queried | status present | nppes=verified | PASS |
| F2 | PECOS is queried | status present | pecos=verified | PASS |
| F3 | OIG LEIE is queried | status present | oig_leie=clear | PASS |
| F4 | Statuses drawn from the defined vocabulary | subset of the defined set | observed=['clear', 'not_checked', 'verified'] | PASS |
| F5 | A B1-B4 bucket is assigned | one of B1..B4 | bucket=B1 | PASS |
| F6 | A review ID is generated | REV-YYYY-NNNNNN | review_id=REV-2026-000039 | PASS |
| F7 | Confidence is non-null for a real NPI | non-null | confidence_keys=['coverage_note', 'not_implemented', 'sources_available', 'sources_checked', 'sources_failed', 'sources_not_checked', 'sources_not_implemented', 'sources_unavailable', 'sources_verified'] | PASS |
| F8 | Unavailable source degrades gracefully | not_checked/unavailable + reason, request still 200 | HTTP 200, sam_gov=not_checked | PASS |


### Rules, sampling and reporting

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| G1 | Rules expose a version | every rule versioned | n=11, all_versioned=True | PASS |
| G2 | Rules expose effective_date | every rule dated | all_dated=True | PASS |
| G3 | Sample drawn with a computed size | size>0 | HTTP 200, size=62 | PASS |
| G4 | Sampling configuration is captured | >=3 config fields | captured=['confidence_level', 'margin_of_error', 'proportion', 'random_seed', 'use_fpc', 'population_size'] | PASS |
| G5 | Same seed reproduces the same sample size | size1==size2 | size1=62, size2=62 | PASS |
| G6 | Report generated with expected sections | executive_summary + classification_distribution present | HTTP 200, sections=['executive_summary', 'classification_distribution', 'limitations', 'sampling_summary', 'period'] | PASS |
| G7 | Mandatory limitations section present | non-empty | present=True, len=416 | PASS |
| G8 | B1-B4 counts reconcile with entities reviewed | sum(counts) == entities_reviewed | counts={'B1': 23, 'B2': 2, 'B3': 5, 'B4': 9}, sum=39, reviewed=39 | PASS |
| G9 | Report is archived and retrievable | generated report_id appears in /arc/reports | stored=19, this_report_archived=True | PASS |
| G10 | B3 resolution workflow functions | 200 and resolution recorded | HTTP 200, resolution=reclassified, effective=B2 | PASS |


## Task 4 Evidence — Review Cycles

Weekly report generation was exercised and archived; B3 reviews were resolved through the reviewer workflow with a mandatory rationale, and the resolution was confirmed by re-reading the record.

## Task 5 Evidence — Priority Review

The priority-review endpoint is implemented (`POST /api/tefca/arc/priority-review`, admin-gated). It was exercised as part of the RBAC matrix; a dedicated functional case is recorded as not executed in this cycle rather than asserted.

## Verification Accuracy

| Bucket | Label | Count |
|---|---|---|
| B1 | No Discrepancy | 112 |
| B2 | Minor / Administrative | 3 |
| B3 | Inexplicable — manual review | 22 |
| B4 | Non-Compliant | 15 |


## Connector Health

| Connector | Status | Scoring impact |
|---|---|---|
| NPPES | Operational | Included |
| PECOS | Operational | Included |
| OIG LEIE | Operational | Included |
| SAM.gov | Not Operational — key valid, endpoints 404 | Excluded |
| RCE Directory | ONC-Provided; direct access not authorized (Case #00055525) | Excluded |


## Test Coverage

| Block | Tests | Passed |
|---|---|---|
| Security Validation | 37 | 36 |
| RBAC | 71 | 71 |
| TEFCA operational | 26 | 26 |
| API contract | 14 | 14 |


## Performance Baseline

| Operation | Wall time | Rows/sec | Imported | Errors |
|---|---|---|---|---|
| CSV import 100 rows | 35.47s | 2.8 | 100 | 0 |
| CSV import 1000 rows | 273.86s | - | - | - |
| CSV import 10000 rows | 277.61s | - | - | - |


## API Contract

Specification validates against the OpenAPI 3.1 meta-schema; 294 paths and 309 operations documented. Operations removed since the frozen v1.0 baseline: 0.

## Risk Acceptance

| ID | Finding | Severity | Disposition |
|---|---|---|---|
| F-001 | CSV import returns raw database exceptions | Medium | Risk acceptance (Medium). Map DB exceptions to caller-safe text at the import boundary; log detail server-side against the request_id. |
| F-002 | Raw null byte in a query parameter returns HTTP 500 | Low | Risk acceptance (Low). Reject or strip NUL during request validation. |
| F-003 | SAM.gov entity endpoints unreachable | Medium | Excluded from confidence scoring. Endpoint/entitlement question, not a key request. |
| F-004 | Unreachable role requirement on the verify handler | Informational | Documented, not changed. Enforcement is correct and fails closed. |


## Attestation

Prepared by: ______________________  Imran Siddiqui, Chief Executive Officer, Alliance Global Tech, Inc.  Date: ____________
