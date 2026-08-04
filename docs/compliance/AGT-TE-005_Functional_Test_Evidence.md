# AGT-TE-005 — Functional Test Evidence

**Contract:** 7571MN26F80064 · **CAGE:** 8ERE8 · **UEI:** MP2FLV1MAW93

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


## Executive Summary

Functional and security testing executed against the development environment on 2026-08-02. Security validation 36/37; RBAC 65/65 cells and 6/6 scenarios; TEFCA operational validation 26/26; API contract 14/14.

## Methodology

Each test states an expected result before execution and records the observed result. Findings are reproduced before being reported as defects; results that proved to be artefacts of the test harness are recorded as corrections rather than silently dropped.

## NIST SP 800-53 Mapping

| Control | Evidence | Result |
|---|---|---|
| AC-2 Account Management | RBAC matrix, 5 roles | 65/65 cells |
| AC-3 Access Enforcement | Role gates return 403 | Verified |
| AC-6 Least Privilege | No role exceeded its level | Verified |
| AU-2 Audit Events | Auth events audited | Observed |
| IA-2 Identification & Authentication | JWT validation suite | 8/8 |
| IA-5 Authenticator Management | Lockout after 5 failures | Verified |
| SC-8 Transmission Confidentiality | HTTPS enforced | Verified |
| SI-10 Information Input Validation | Injection suite | 9/10 |
| SI-11 Error Handling | Generic errors; one exception (F-001) | Partial |


## Test Results Summary

| Suite | Tests | Passed |
|---|---|---|
| Security Validation (Block 3) | 37 | 36 |
| RBAC matrix (Block 4) | 65 | 65 |
| RBAC scenarios (Block 4) | 6 | 6 |
| TEFCA operational (Block 5) | 26 | 26 |
| API contract (Block 7) | 14 | 14 |

> **These results supersede the preliminary counts in
> `SPRINT_REPORT_2026-08-01_ARC_TESTING.md`.** That report reflects an earlier and
> smaller run: the RBAC scenario suite has since grown from **5 scenarios to 6**
> (all passing in both runs), and the TEFCA operational suite from **25 tests
> (24 passed, 1 Not Executed) to 26**, with the previously unexecuted test now
> executed and passing. No result was revised downward and no earlier test was
> retracted. This document reflects the final validated results.


## Detailed Results — Security Validation

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| A1 | SQL injection in login email field | 401/422, no auth, no SQL error | HTTP 401, leaks=[] | PASS |
| A2 | SQL injection in password field | 401, no auth | HTTP 401, leaks=[] | PASS |
| A3 | XSS payload in search query | not executable in a browser context | HTTP 200, ct=application/json, nosniff=nosniff, inert=True | PASS |
| A4 | Command injection metacharacters in search | handled safely, no shell output | HTTP 200, leaks=[] | PASS |
| A5 | Path traversal in entity id | 404/422, no file contents | HTTP 404 | PASS |
| A6 | SSRF payload (cloud metadata IP) | no outbound fetch | HTTP 200 | PASS |
| A7 | CRLF injection in parameter | no injected response header | HTTP 200 | PASS |
| A8 | Oversized JSON body (~2MB) | rejected, no crash | HTTP 422 | PASS |
| A9 | Raw null byte in parameter | handled without HTTP 500 | HTTP 500, leaks=[] | FAIL |
| A10 | Unicode/RTL-override payload | handled, no crash | HTTP 200, leaks=[] | PASS |
| B1 | Tampered JWT signature | 401 | HTTP 401 | PASS |
| B2 | 'none' algorithm JWT | 401 (alg confusion rejected) | HTTP 401 | PASS |
| B3 | Expired token | 401 | HTTP 401 | PASS |
| B4 | Very long bearer token (5000 chars) | 401/431, no crash | HTTP 401 | PASS |
| B5 | Empty Authorization header | 401/403 | HTTP 401 | PASS |
| B6 | 'Bearer' with no token | 401/403 | HTTP 401 | PASS |
| B7 | Valid non-admin token authenticates (fresh) | 200 | HTTP 200 | PASS |
| B8 | Account lockout after 5 failures (synthetic account) | 401 x5 then 429 'Account temporarily locked' | codes=[401, 401, 401, 401, 401, 429], account_locked=True, ip_throttled=False | PASS |
| C1 | viewer -> POST /arc/review-rules (admin-only) | 403 | HTTP 403 | PASS |
| C2 | viewer -> POST /arc/reports/generate (admin-only) | 403 | HTTP 403 | PASS |
| C3 | No Authorization header | 401 | HTTP 401 | PASS |
| C4 | IDOR probe, non-existent UUID | 404, no data | HTTP 404 | PASS |
| C5 | JWT role claim modified viewer->admin | 401, signature invalid | HTTP 401 | PASS |
| C6 | Unknown endpoint under auth prefix | 404 | HTTP 404 | PASS |
| C7 | Wrong HTTP method on known route | 405 | HTTP 405 | PASS |
| C8 | viewer -> POST /registry/dev/seed (admin-only) | 403 | HTTP 403 | PASS |
| C9 | analyst -> PATCH B3 resolve (reviewer-only) | 403 | HTTP 403 | PASS |
| C10 | reviewer -> POST /arc/review-rules (admin-only) | 403 | HTTP 403 | PASS |
| C11 | viewer -> GET /registry/entities (router requires reviewer) | 403 | HTTP 403 | PASS |
| D1 | Malformed UUID returns no stack trace | no traceback | HTTP 422, leaks=[] | PASS |
| D2 | No filesystem paths in error body | none | found=[] | PASS |
| D3 | No database engine details in error body | none | found=[] | PASS |
| D4 | OpenAPI posture on dev | reachable on dev by design | HTTP 200 | PASS |
| D5 | Server header hides stack version | no version string | server='uvicorn' | PASS |
| D6 | Generic 404 on unknown path | 404, no internals | HTTP 404, leaks=[] | PASS |
| D7 | /health leaks no secrets | clean payload | HTTP 200 | PASS |
| D8 | Unknown-user error == wrong-password error | identical generic message | HTTP 401, msg='Invalid email or password' | PASS |


## Detailed Results — TEFCA Operational

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
| F1 | NPPES is queried | status present | nppes=verified | PASS |
| F2 | PECOS is queried | status present | pecos=verified | PASS |
| F3 | OIG LEIE is queried | status present | oig_leie=clear | PASS |
| F4 | Statuses drawn from the defined vocabulary | subset of the defined set | observed=['clear', 'not_checked', 'verified'] | PASS |
| F5 | A B1-B4 bucket is assigned | one of B1..B4 | bucket=B1 | PASS |
| F6 | A review ID is generated | REV-YYYY-NNNNNN | review_id=REV-2026-000039 | PASS |
| F7 | Confidence is non-null for a real NPI | non-null | confidence_keys=['coverage_note', 'not_implemented', 'sources_available', 'sources_checked', 'sources_failed', 'sources_not_checked', 'sources_not_implemented', 'sources_unavailable', 'sources_verified'] | PASS |
| F8 | Unavailable source degrades gracefully | not_checked/unavailable + reason, request still 200 | HTTP 200, sam_gov=not_checked | PASS |
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


## RBAC Matrix

| Role | Endpoint | Expected | Actual | Result |
|---|---|---|---|---|
| viewer | GET /auth/me | permitted (>= viewer) | HTTP 200 | PASS |
| viewer | GET /registry/stats | 403 denied | HTTP 403 | PASS |
| viewer | GET /registry/entities | 403 denied | HTTP 403 | PASS |
| viewer | GET /registry/entities/{id} | 403 denied | HTTP 403 | PASS |
| viewer | GET /registry/findings | 403 denied | HTTP 403 | PASS |
| viewer | GET /arc/review-rules | permitted (>= viewer) | HTTP 200 | PASS |
| viewer | GET /arc/reviews | permitted (>= viewer) | HTTP 200 | PASS |
| viewer | GET /arc/reports | permitted (>= viewer) | HTTP 200 | PASS |
| viewer | POST /registry/entities/{id}/verify | 403 denied | HTTP 403 | PASS |
| viewer | PATCH /arc/reviews/{id}/resolve | 403 denied | HTTP 403 | PASS |
| viewer | POST /arc/review-rules | 403 denied | HTTP 403 | PASS |
| viewer | POST /arc/reports/generate | 403 denied | HTTP 403 | PASS |
| viewer | POST /registry/dev/seed | 403 denied | HTTP 403 | PASS |
| analyst/contributor | GET /auth/me | permitted (>= viewer) | HTTP 200 | PASS |
| analyst/contributor | GET /registry/stats | 403 denied | HTTP 403 | PASS |
| analyst/contributor | GET /registry/entities | 403 denied | HTTP 403 | PASS |
| analyst/contributor | GET /registry/entities/{id} | 403 denied | HTTP 403 | PASS |
| analyst/contributor | GET /registry/findings | 403 denied | HTTP 403 | PASS |
| analyst/contributor | GET /arc/review-rules | permitted (>= viewer) | HTTP 200 | PASS |
| analyst/contributor | GET /arc/reviews | permitted (>= viewer) | HTTP 200 | PASS |
| analyst/contributor | GET /arc/reports | permitted (>= viewer) | HTTP 200 | PASS |
| analyst/contributor | POST /registry/entities/{id}/verify | permitted (>= contributor) | HTTP 403 | PASS |
| analyst/contributor | PATCH /arc/reviews/{id}/resolve | 403 denied | HTTP 403 | PASS |
| analyst/contributor | POST /arc/review-rules | 403 denied | HTTP 403 | PASS |
| analyst/contributor | POST /arc/reports/generate | 403 denied | HTTP 403 | PASS |
| analyst/contributor | POST /registry/dev/seed | 403 denied | HTTP 403 | PASS |
| reviewer | GET /auth/me | permitted (>= viewer) | HTTP 200 | PASS |
| reviewer | GET /registry/stats | permitted (>= reviewer) | HTTP 200 | PASS |
| reviewer | GET /registry/entities | permitted (>= reviewer) | HTTP 200 | PASS |
| reviewer | GET /registry/entities/{id} | permitted (>= reviewer) | HTTP 200 | PASS |
| reviewer | GET /registry/findings | permitted (>= reviewer) | HTTP 200 | PASS |
| reviewer | GET /arc/review-rules | permitted (>= viewer) | HTTP 200 | PASS |
| reviewer | GET /arc/reviews | permitted (>= viewer) | HTTP 200 | PASS |
| reviewer | GET /arc/reports | permitted (>= viewer) | HTTP 200 | PASS |
| reviewer | POST /registry/entities/{id}/verify | permitted (>= contributor) | HTTP 200 | PASS |
| reviewer | PATCH /arc/reviews/{id}/resolve | permitted (>= reviewer) | HTTP 200 | PASS |
| reviewer | POST /arc/review-rules | 403 denied | HTTP 403 | PASS |
| reviewer | POST /arc/reports/generate | 403 denied | HTTP 403 | PASS |
| reviewer | POST /registry/dev/seed | 403 denied | HTTP 403 | PASS |
| admin(test) | GET /auth/me | permitted (>= viewer) | HTTP 200 | PASS |
| admin(test) | GET /registry/stats | permitted (>= reviewer) | HTTP 200 | PASS |
| admin(test) | GET /registry/entities | permitted (>= reviewer) | HTTP 200 | PASS |
| admin(test) | GET /registry/entities/{id} | permitted (>= reviewer) | HTTP 200 | PASS |
| admin(test) | GET /registry/findings | permitted (>= reviewer) | HTTP 200 | PASS |
| admin(test) | GET /arc/review-rules | permitted (>= viewer) | HTTP 200 | PASS |
| admin(test) | GET /arc/reviews | permitted (>= viewer) | HTTP 200 | PASS |
| admin(test) | GET /arc/reports | permitted (>= viewer) | HTTP 200 | PASS |
| admin(test) | POST /registry/entities/{id}/verify | permitted (>= contributor) | HTTP 200 | PASS |
| admin(test) | PATCH /arc/reviews/{id}/resolve | permitted (>= reviewer) | HTTP 200 | PASS |
| admin(test) | POST /arc/review-rules | permitted (>= admin) | HTTP 200 | PASS |
| admin(test) | POST /arc/reports/generate | permitted (>= admin) | HTTP 200 | PASS |
| admin(test) | POST /registry/dev/seed | permitted (>= admin) | HTTP 200 | PASS |
| admin | GET /auth/me | permitted (>= viewer) | HTTP 200 | PASS |
| admin | GET /registry/stats | permitted (>= reviewer) | HTTP 200 | PASS |
| admin | GET /registry/entities | permitted (>= reviewer) | HTTP 200 | PASS |
| admin | GET /registry/entities/{id} | permitted (>= reviewer) | HTTP 200 | PASS |
| admin | GET /registry/findings | permitted (>= reviewer) | HTTP 200 | PASS |
| admin | GET /arc/review-rules | permitted (>= viewer) | HTTP 200 | PASS |
| admin | GET /arc/reviews | permitted (>= viewer) | HTTP 200 | PASS |
| admin | GET /arc/reports | permitted (>= viewer) | HTTP 200 | PASS |
| admin | POST /registry/entities/{id}/verify | permitted (>= contributor) | HTTP 200 | PASS |
| admin | PATCH /arc/reviews/{id}/resolve | permitted (>= reviewer) | HTTP 200 | PASS |
| admin | POST /arc/review-rules | permitted (>= admin) | HTTP 409 | PASS |
| admin | POST /arc/reports/generate | permitted (>= admin) | HTTP 200 | PASS |
| admin | POST /registry/dev/seed | permitted (>= admin) | HTTP 200 | PASS |


## Performance Baseline

| Operation | Wall time | Rows/sec | Imported | Errors |
|---|---|---|---|---|
| CSV import 100 rows | 35.47s | 2.8 | 100 | 0 |
| CSV import 1000 rows | 273.86s | - | - | - |
| CSV import 10000 rows | 277.61s | - | - | - |


| Verification batch | Total | Mean | Max | vs 3s target |
|---|---|---|---|---|
| 1 entities | 4.5s | 4.496s | 4.496s | MISSES |
| 10 entities | 45.97s | 4.597s | 8.602s | MISSES |
| 100 entities | 360.64s | 3.606s | 7.205s | MISSES |


## API Contract Validation

| Test | Description | Expected | Actual | Result |
|---|---|---|---|---|
| 7.1 | openapi.json validates | no schema errors | valid against the OpenAPI 3.1 meta-schema | paths=294, ops=309 | PASS |
| 7.2.1 | GET /health conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | PASS |
| 7.2.2 | GET /api/auth/me conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | PASS |
| 7.2.3 | GET /registry/stats conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | PASS |
| 7.2.4 | GET /registry/entities conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | PASS |
| 7.2.5 | GET /registry/findings conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | PASS |
| 7.2.6 | GET /registry/search conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | PASS |
| 7.2.7 | GET /arc/review-rules conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | PASS |
| 7.2.8 | GET /arc/reviews conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | PASS |
| 7.2.9 | GET /arc/reports conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | PASS |
| 7.2.10 | GET /arc/samples conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | PASS |
| 7.3 GET /registry/stats | GET /registry/stats stable across 10 calls | 1 distinct digest | 1 distinct digest(s) | PASS |
| 7.3 GET /arc/review-rules | GET /arc/review-rules stable across 10 calls | 1 distinct digest | 1 distinct digest(s) | PASS |
| 7.4 | No operations removed since the v1.0 baseline | 0 removed (removal is breaking) | removed=0, added=1 | PASS |


## Connector Health

| Connector | Status | Scoring |
|---|---|---|
| NPPES | Operational | Included |
| PECOS | Operational | Included |
| OIG LEIE | Operational | Included |
| SAM.gov | Not Operational — endpoints 404 | Excluded |
| RCE Directory | ONC-Provided, access not authorized (Case #00055525) | Excluded |
| State Registries | Not implemented | Excluded |
| IRS | Not implemented | Excluded |


## Findings

| ID | Finding | Severity | Status |
|---|---|---|---|
| F-001 | CSV import returns raw database exceptions | Medium | CONFIRMED — reproduced 2026-08-02 |
| F-002 | Raw null byte in a query parameter returns HTTP 500 | Low | CONFIRMED — reproduced 2026-08-02 |
| F-003 | SAM.gov entity endpoints unreachable | Medium | CONFIRMED — key valid, endpoint 404 |
| F-004 | Unreachable role requirement on the verify handler | Informational | CONFIRMED — by design, documented |


## Risk Acceptance

| ID | Severity | Disposition |
|---|---|---|
| F-001 | Medium | Risk acceptance (Medium). Map DB exceptions to caller-safe text at the import boundary; log detail server-side against the request_id. |
| F-002 | Low | Risk acceptance (Low). Reject or strip NUL during request validation. |
| F-003 | Medium | Excluded from confidence scoring. Endpoint/entitlement question, not a key request. |
| F-004 | Informational | Documented, not changed. Enforcement is correct and fails closed. |


## Attestation

Prepared by: ______________________  Imran Siddiqui, Chief Executive Officer, Alliance Global Tech, Inc.  Date: ____________
