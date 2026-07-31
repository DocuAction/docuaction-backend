# TEFCA ARC Functional Test Report

**Date:** 2026-07-30  
**Environment:** Development  
**Tester:** Claude Code (automated)  
**Run timestamp (UTC):** 2026-07-31T02:02:07+00:00

## Executive Summary

- **Total cases:** 64
- **Pass:** 30 | **Fail:** 3 | **Not implemented:** 21 | **Blocked:** 10
- **Pass rate (testable cases):** 90.9% (30/33)

Results use four outcomes, kept deliberately distinct:

- **PASS** - the endpoint exists and behaved as specified.
- **FAIL** - the endpoint exists and did not behave as specified. A defect.
- **NOT_IMPLEMENTED** - no such endpoint exists. A backlog item, not a defect. Confirmed against both the live OpenAPI schema and the route source.
- **BLOCKED** - the endpoint exists and answered correctly, but the assertion could not be evaluated because the dev registry holds zero entities. Reporting these as failures would misattribute a data-seeding gap to the code.

The pass rate below is computed over **testable** cases only (PASS + FAIL = 33); counting NOT_IMPLEMENTED or BLOCKED as failures would understate working code, and counting them as passes would overstate delivered capability.

### Headline findings

1. **All three failures share one root cause.** Every `/api/enterprise/*` endpoint returns HTTP 500 on dev. `app/models/enterprise_models.py` declares ten tables (`tenants`, `tenant_users`, `decisions`, `actions`, `state_audit_log`, `execution_queue`, and others) but is never imported by `app/models/__init__.py`. `main.py` startup runs `Base.metadata.create_all`, which only creates tables whose models were imported and registered on the metadata; `enterprise_models` is imported lazily *inside* each route handler, so the tables are never created. Every handler calls `_get_tenant_id()` first, which touches `tenants`/`tenant_users`, so the whole module fails uniformly.
2. **The TEFCA registry is read-only over the API.** There is no `POST /entities`, no `PUT`/`PATCH`, and no status-transition route. Entities can enter the system only through `POST /import/csv` or `POST /import/fhir-bundle`.
3. **Two well-built modules are unreachable.** `app/services/npi_validator.py` (CMS Luhn check) and `app/tefca_registry/state_machine.py` (guarded transitions plus an audit hook) are both implemented and unit-tested, but are imported *only* by their tests. No production code path reaches either.
4. **The dev registry is empty** (`entities_total: 0`), which blocked 10 cases. The endpoints answered correctly with well-formed empty results - this is a seeding gap, not a code defect. `app/tefca_registry/seed.py` exists but is not exposed as a route.

## Results by Suite

| Suite | Total | Pass | Fail | Not impl. | Blocked |
|-------|-------|------|------|-----------|---------|
| Setup | 1 | 1 | 0 | 0 | 0 |
| Suite 1 - Entity Management | 8 | 4 | 0 | 2 | 2 |
| Suite 2 - NPI Validation | 6 | 0 | 0 | 6 | 0 |
| Suite 3 - Verification Workflow | 8 | 2 | 0 | 1 | 5 |
| Suite 4 - State Machine | 8 | 0 | 0 | 8 | 0 |
| Suite 5 - Case Management | 5 | 3 | 0 | 1 | 1 |
| Suite 6 - Decisions | 5 | 2 | 2 | 1 | 0 |
| Suite 7 - Audit Trail | 6 | 3 | 1 | 2 | 0 |
| Suite 8 - QHIN / Participant Hierarchy | 5 | 3 | 0 | 0 | 2 |
| Suite 9 - Connectors | 6 | 6 | 0 | 0 | 0 |
| Suite 10 - Dashboard & Analytics | 6 | 6 | 0 | 0 | 0 |
| **TOTAL** | **64** | **30** | **3** | **21** | **10** |

## Detailed Results

### Setup

| ID | Description | Method | Expected | Actual | Status |
|----|-------------|--------|----------|--------|--------|
| TEST-TEFCA-AUTH | Obtain reviewer/admin token | POST | 200 + access_token | HTTP 200, token=obtained | PASS |

### Suite 1 - Entity Management

| ID | Description | Method | Expected | Actual | Status |
|----|-------------|--------|----------|--------|--------|
| TEST-TEFCA-E01 | List entities | GET | 200 + list | HTTP 200 | PASS |
| TEST-TEFCA-E02 | List honours limit=5 | GET | 200 + at most 5 | HTTP 200, count: 0 returned | PASS |
| TEST-TEFCA-E03 | List honours limit=1 | GET | 200 + exactly 1 | HTTP 200, 0 returned; registry is empty so the cap cannot be exercised | BLOCKED |
| TEST-TEFCA-E04 | Create entity | POST | 201/200 + entity created | No POST /api/tefca/registry/entities exists. Entities are created only via POST /import/csv or POST /import/fhir-bundle. | NOT_IMPLEMENTED |
| TEST-TEFCA-E05 | Get entity by id | GET | 200 + matching entity | BLOCKED - registry contains 0 entities; endpoint returned a well-formed empty result | BLOCKED |
| TEST-TEFCA-E06 | Update entity | PUT | 200 + updated entity | No PUT/PATCH route exists for registry entities. | NOT_IMPLEMENTED |
| TEST-TEFCA-E07 | Search entities | GET | 200 + search results | HTTP 200 | PASS |
| TEST-TEFCA-E08 | Entities deny anonymous | GET | 401 | HTTP 401 | PASS |

### Suite 2 - NPI Validation

| ID | Description | Method | Expected | Actual | Status |
|----|-------------|--------|----------|--------|--------|
| TEST-TEFCA-NPI01 | NPI valid Luhn: 1234567893 | POST | accepted at entity creation | No API surface accepts an NPI for validation at creation time, because no entity-creation endpoint exists. app/services/npi_validator.py implements the CMS Luhn check but is imported only by tests/test_npi_validation.py. | NOT_IMPLEMENTED |
| TEST-TEFCA-NPI02 | NPI invalid Luhn: 1234567890 | POST | rejected at entity creation | No API surface accepts an NPI for validation at creation time, because no entity-creation endpoint exists. app/services/npi_validator.py implements the CMS Luhn check but is imported only by tests/test_npi_validation.py. | NOT_IMPLEMENTED |
| TEST-TEFCA-NPI03 | NPI too short: 12345 | POST | rejected at entity creation | No API surface accepts an NPI for validation at creation time, because no entity-creation endpoint exists. app/services/npi_validator.py implements the CMS Luhn check but is imported only by tests/test_npi_validation.py. | NOT_IMPLEMENTED |
| TEST-TEFCA-NPI04 | NPI too long: 12345678901 | POST | rejected at entity creation | No API surface accepts an NPI for validation at creation time, because no entity-creation endpoint exists. app/services/npi_validator.py implements the CMS Luhn check but is imported only by tests/test_npi_validation.py. | NOT_IMPLEMENTED |
| TEST-TEFCA-NPI05 | NPI non-numeric: ABCDEFGHIJ | POST | rejected at entity creation | No API surface accepts an NPI for validation at creation time, because no entity-creation endpoint exists. app/services/npi_validator.py implements the CMS Luhn check but is imported only by tests/test_npi_validation.py. | NOT_IMPLEMENTED |
| TEST-TEFCA-NPI06 | NPI empty: (empty) | POST | rejected at entity creation | No API surface accepts an NPI for validation at creation time, because no entity-creation endpoint exists. app/services/npi_validator.py implements the CMS Luhn check but is imported only by tests/test_npi_validation.py. | NOT_IMPLEMENTED |

### Suite 3 - Verification Workflow

| ID | Description | Method | Expected | Actual | Status |
|----|-------------|--------|----------|--------|--------|
| TEST-TEFCA-V01 | Trigger entity verification | POST | 200 | BLOCKED - registry contains 0 entities | BLOCKED |
| TEST-TEFCA-V02 | Verification consults NPPES | POST | 200 | BLOCKED - registry contains 0 entities | BLOCKED |
| TEST-TEFCA-V03 | Verification consults LEIE | POST | 200 | BLOCKED - registry contains 0 entities | BLOCKED |
| TEST-TEFCA-V04 | Verification consults PECOS | POST | 200 | BLOCKED - registry contains 0 entities | BLOCKED |
| TEST-TEFCA-V05 | Confidence score calculated | POST | confidence present | BLOCKED - no entity to verify | BLOCKED |
| TEST-TEFCA-V06 | Verification creates a reviewable case | POST | case created for review | Verification writes findings (GET /registry/findings, /entities/{id}/findings) and verification jobs, not 'cases'. No route links a verification to a case record. | NOT_IMPLEMENTED |
| TEST-TEFCA-V07 | Bulk verify endpoint | POST | 200/202 accepted | HTTP 200 | PASS |
| TEST-TEFCA-V08 | Verification jobs listed | GET | 200 | HTTP 200 | PASS |

### Suite 4 - State Machine

| ID | Description | Method | Expected | Actual | Status |
|----|-------------|--------|----------|--------|--------|
| TEST-TEFCA-SM01 | Entity starts as draft | PUT | transition enforced by API | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only by tests/test_tefca_state_machine.py. | NOT_IMPLEMENTED |
| TEST-TEFCA-SM02 | draft -> pending_verification | PUT | transition enforced by API | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only by tests/test_tefca_state_machine.py. | NOT_IMPLEMENTED |
| TEST-TEFCA-SM03 | pending_verification -> active | PUT | transition enforced by API | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only by tests/test_tefca_state_machine.py. | NOT_IMPLEMENTED |
| TEST-TEFCA-SM04 | draft -> active refused | PUT | transition enforced by API | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only by tests/test_tefca_state_machine.py. | NOT_IMPLEMENTED |
| TEST-TEFCA-SM05 | active -> suspended | PUT | transition enforced by API | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only by tests/test_tefca_state_machine.py. | NOT_IMPLEMENTED |
| TEST-TEFCA-SM06 | suspended -> active | PUT | transition enforced by API | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only by tests/test_tefca_state_machine.py. | NOT_IMPLEMENTED |
| TEST-TEFCA-SM07 | active -> inactive | PUT | transition enforced by API | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only by tests/test_tefca_state_machine.py. | NOT_IMPLEMENTED |
| TEST-TEFCA-SM08 | inactive -> active refused | PUT | transition enforced by API | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only by tests/test_tefca_state_machine.py. | NOT_IMPLEMENTED |

### Suite 5 - Case Management

| ID | Description | Method | Expected | Actual | Status |
|----|-------------|--------|----------|--------|--------|
| TEST-TEFCA-CM01 | Patients list | GET | 200 | HTTP 200 | PASS |
| TEST-TEFCA-CM02 | Government cases list | GET | 200 | HTTP 200 | PASS |
| TEST-TEFCA-CM03 | Case records carry an entity reference | GET | case objects reference an entity | HTTP 200, 0 cases present so the field cannot be evidenced | BLOCKED |
| TEST-TEFCA-CM04 | Case carries verification results | GET | verification results embedded in case | Case-management cases are clinical/CCM records and are not joined to TEFCA registry verification output. No route returns a case with verification results. | NOT_IMPLEMENTED |
| TEST-TEFCA-CM05 | Case management denies anonymous | GET | 401 | HTTP 401 | PASS |

### Suite 6 - Decisions

| ID | Description | Method | Expected | Actual | Status |
|----|-------------|--------|----------|--------|--------|
| TEST-TEFCA-D01 | Decision listing endpoint | GET | 200 | HTTP 500 | FAIL |
| TEST-TEFCA-D02 | Create A1 classification decision for a case | POST | decision recorded with A1 classification | No TEFCA decision route exists. Classification is applied to queue records via PATCH /api/v1/tefca/queue/{record_id}/classify, which is the legacy review module and is not connected to registry entities. | NOT_IMPLEMENTED |
| TEST-TEFCA-D03 | Decision endpoints require auth | GET | 401 anonymous | HTTP 401 | PASS |
| TEST-TEFCA-D04 | Decisions are immutable | PUT | no update route / 405 | No PUT or PATCH route exists on /api/enterprise/decisions/{id}; the only mutations are approve, reject and review transitions. | PASS |
| TEST-TEFCA-D05 | Decision activity is auditable | GET | 200 | HTTP 500 | FAIL |

### Suite 7 - Audit Trail

| ID | Description | Method | Expected | Actual | Status |
|----|-------------|--------|----------|--------|--------|
| TEST-TEFCA-A01 | QA audit trail readable | GET | 200 | HTTP 200 | PASS |
| TEST-TEFCA-A02 | Enterprise audit log readable | GET | 200 | HTTP 500 | FAIL |
| TEST-TEFCA-A03 | Verification generates an audit entry | GET | verify action recorded in audit | The QA audit trail covers review gates (tefca_qa_audit). Registry verification writes findings and verification_jobs rather than rows in that audit table, so a verification is not observable through an audit endpoint. | NOT_IMPLEMENTED |
| TEST-TEFCA-A04 | Decision generates an audit entry | GET | decision action recorded | Enterprise audit exists and is readable; no TEFCA decision route exists to generate an entry (see TEST-TEFCA-D02). | NOT_IMPLEMENTED |
| TEST-TEFCA-A05 | Audit entries carry timestamp and actor | GET | timestamp + user/actor + action present | created_at present=True, actor field present=True, action/gate present=True | PASS |
| TEST-TEFCA-A06 | Audit trail exposes no delete route | DELETE | no DELETE route in schema | No DELETE operation is declared on any audit path in the live schema. | PASS |

### Suite 8 - QHIN / Participant Hierarchy

| ID | Description | Method | Expected | Actual | Status |
|----|-------------|--------|----------|--------|--------|
| TEST-TEFCA-H01 | QHINs listed | GET | 200 | HTTP 200 | PASS |
| TEST-TEFCA-H02 | Participants listed | GET | 200 | HTTP 200 | PASS |
| TEST-TEFCA-H03 | Children of a QHIN resolve | GET | 200 | BLOCKED - registry contains 0 QHINs | BLOCKED |
| TEST-TEFCA-H04 | Hierarchy subtree resolves | GET | 200 | BLOCKED - registry contains 0 QHINs | BLOCKED |
| TEST-TEFCA-H05 | Hierarchy roots listed | GET | 200 | HTTP 200 | PASS |

### Suite 9 - Connectors

| ID | Description | Method | Expected | Actual | Status |
|----|-------------|--------|----------|--------|--------|
| TEST-TEFCA-C01 | Connector NPPES live=True | GET | live == True | live=True, status=OK | PASS |
| TEST-TEFCA-C02 | Connector OIG_LEIE live=True | GET | live == True | live=True, status=OK | PASS |
| TEST-TEFCA-C03 | Connector PECOS live=True | GET | live == True | live=True, status=OK | PASS |
| TEST-TEFCA-C04 | Connector SAM_GOV live=False | GET | live == False | live=False, status=UNAVAILABLE | PASS |
| TEST-TEFCA-C05 | Connector RCE_DIRECTORY live=False | GET | live == False | live=False, status=UNAVAILABLE | PASS |
| TEST-TEFCA-C06 | Connector status endpoint | GET | 200 | HTTP 200 | PASS |

### Suite 10 - Dashboard & Analytics

| ID | Description | Method | Expected | Actual | Status |
|----|-------------|--------|----------|--------|--------|
| TEST-TEFCA-DASH01 | Dashboard summary | GET | 200 | HTTP 200 | PASS |
| TEST-TEFCA-DASH02 | Summary includes entity counts | GET | entity/review counts present | count-bearing fields present=True | PASS |
| TEST-TEFCA-DASH03 | Summary includes verification stats | GET | verification statistics present | verification/validation fields present=True | PASS |
| TEST-TEFCA-DASH04 | Summary includes decision breakdown | GET | decision/classification breakdown present | decision/classification fields present=True | PASS |
| TEST-TEFCA-DASH05 | Dashboard denies anonymous | GET | 401 | HTTP 401 | PASS |
| TEST-TEFCA-DASH06 | Dashboard trends | GET | 200 | HTTP 200 | PASS |

## Failed Tests

### TEST-TEFCA-D01 - Decision listing endpoint

- **Request:** `GET https://docuaction-dev.azurewebsites.net/api/enterprise/decisions`
- **Expected:** 200
- **Actual:** HTTP 500
- **Root cause:** `enterprise_models` is not imported by `app/models/__init__.py`, so its ten tables are absent from `Base.metadata` when startup runs `create_all`. The tables are never created on a database that did not receive them by other means.
- **Recommended fix:** import the enterprise models in `app/models/__init__.py` so they register on the metadata, then restart to let the existing startup `create_all` provision them. Verify on dev before prod: prod currently answers these routes, most likely because its database was migrated wholesale from Railway and already contains the tables. Adding the import will cause prod startup to attempt creation too - it is idempotent, but should be confirmed against a prod backup first.

### TEST-TEFCA-D05 - Decision activity is auditable

- **Request:** `GET https://docuaction-dev.azurewebsites.net/api/enterprise/audit`
- **Expected:** 200
- **Actual:** HTTP 500
- **Root cause:** `enterprise_models` is not imported by `app/models/__init__.py`, so its ten tables are absent from `Base.metadata` when startup runs `create_all`. The tables are never created on a database that did not receive them by other means.
- **Recommended fix:** import the enterprise models in `app/models/__init__.py` so they register on the metadata, then restart to let the existing startup `create_all` provision them. Verify on dev before prod: prod currently answers these routes, most likely because its database was migrated wholesale from Railway and already contains the tables. Adding the import will cause prod startup to attempt creation too - it is idempotent, but should be confirmed against a prod backup first.

### TEST-TEFCA-A02 - Enterprise audit log readable

- **Request:** `GET https://docuaction-dev.azurewebsites.net/api/enterprise/audit`
- **Expected:** 200
- **Actual:** HTTP 500
- **Root cause:** `enterprise_models` is not imported by `app/models/__init__.py`, so its ten tables are absent from `Base.metadata` when startup runs `create_all`. The tables are never created on a database that did not receive them by other means.
- **Recommended fix:** import the enterprise models in `app/models/__init__.py` so they register on the metadata, then restart to let the existing startup `create_all` provision them. Verify on dev before prod: prod currently answers these routes, most likely because its database was migrated wholesale from Railway and already contains the tables. Adding the import will cause prod startup to attempt creation too - it is idempotent, but should be confirmed against a prod backup first.

## Not Implemented (backlog)

| ID | Capability | Evidence |
|----|-----------|----------|
| TEST-TEFCA-E04 | Create entity | No POST /api/tefca/registry/entities exists. Entities are created only via POST /import/csv or POST /import/fhir-bundle. |
| TEST-TEFCA-E06 | Update entity | No PUT/PATCH route exists for registry entities. |
| TEST-TEFCA-NPI01 | NPI valid Luhn: 1234567893 | No API surface accepts an NPI for validation at creation time, because no entity-creation endpoint exists. app/services/npi_validator.py implements the CMS Luhn |
| TEST-TEFCA-NPI02 | NPI invalid Luhn: 1234567890 | No API surface accepts an NPI for validation at creation time, because no entity-creation endpoint exists. app/services/npi_validator.py implements the CMS Luhn |
| TEST-TEFCA-NPI03 | NPI too short: 12345 | No API surface accepts an NPI for validation at creation time, because no entity-creation endpoint exists. app/services/npi_validator.py implements the CMS Luhn |
| TEST-TEFCA-NPI04 | NPI too long: 12345678901 | No API surface accepts an NPI for validation at creation time, because no entity-creation endpoint exists. app/services/npi_validator.py implements the CMS Luhn |
| TEST-TEFCA-NPI05 | NPI non-numeric: ABCDEFGHIJ | No API surface accepts an NPI for validation at creation time, because no entity-creation endpoint exists. app/services/npi_validator.py implements the CMS Luhn |
| TEST-TEFCA-NPI06 | NPI empty: (empty) | No API surface accepts an NPI for validation at creation time, because no entity-creation endpoint exists. app/services/npi_validator.py implements the CMS Luhn |
| TEST-TEFCA-V06 | Verification creates a reviewable case | Verification writes findings (GET /registry/findings, /entities/{id}/findings) and verification jobs, not 'cases'. No route links a verification to a case recor |
| TEST-TEFCA-SM01 | Entity starts as draft | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only  |
| TEST-TEFCA-SM02 | draft -> pending_verification | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only  |
| TEST-TEFCA-SM03 | pending_verification -> active | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only  |
| TEST-TEFCA-SM04 | draft -> active refused | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only  |
| TEST-TEFCA-SM05 | active -> suspended | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only  |
| TEST-TEFCA-SM06 | suspended -> active | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only  |
| TEST-TEFCA-SM07 | active -> inactive | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only  |
| TEST-TEFCA-SM08 | inactive -> active refused | No status-transition route exists. app/tefca_registry/state_machine.py implements validate_transition/assert_transition and an audit hook, but is imported only  |
| TEST-TEFCA-CM04 | Case carries verification results | Case-management cases are clinical/CCM records and are not joined to TEFCA registry verification output. No route returns a case with verification results. |
| TEST-TEFCA-D02 | Create A1 classification decision for a case | No TEFCA decision route exists. Classification is applied to queue records via PATCH /api/v1/tefca/queue/{record_id}/classify, which is the legacy review module |
| TEST-TEFCA-A03 | Verification generates an audit entry | The QA audit trail covers review gates (tefca_qa_audit). Registry verification writes findings and verification_jobs rather than rows in that audit table, so a  |
| TEST-TEFCA-A04 | Decision generates an audit entry | Enterprise audit exists and is readable; no TEFCA decision route exists to generate an entry (see TEST-TEFCA-D02). |

## Blocked (no seed data)

| ID | Capability | Reason |
|----|-----------|--------|
| TEST-TEFCA-E03 | List honours limit=1 | HTTP 200, 0 returned; registry is empty so the cap cannot be exercised |
| TEST-TEFCA-E05 | Get entity by id | BLOCKED - registry contains 0 entities; endpoint returned a well-formed empty result |
| TEST-TEFCA-V01 | Trigger entity verification | BLOCKED - registry contains 0 entities |
| TEST-TEFCA-V02 | Verification consults NPPES | BLOCKED - registry contains 0 entities |
| TEST-TEFCA-V03 | Verification consults LEIE | BLOCKED - registry contains 0 entities |
| TEST-TEFCA-V04 | Verification consults PECOS | BLOCKED - registry contains 0 entities |
| TEST-TEFCA-V05 | Confidence score calculated | BLOCKED - no entity to verify |
| TEST-TEFCA-CM03 | Case records carry an entity reference | HTTP 200, 0 cases present so the field cannot be evidenced |
| TEST-TEFCA-H03 | Children of a QHIN resolve | BLOCKED - registry contains 0 QHINs |
| TEST-TEFCA-H04 | Hierarchy subtree resolves | BLOCKED - registry contains 0 QHINs |

Seeding the dev registry (via `POST /api/tefca/registry/import/csv` or the existing `app/tefca_registry/seed.py`) would convert all ten into executable cases.

## Method

- Authenticated as `admin@docuaction.io` against dev; one token reused across all cases.
- Route existence was confirmed two ways before any case was marked NOT_IMPLEMENTED: absence from the live `/openapi.json`, and absence of a matching decorator in the route source. A 404 alone was not treated as sufficient evidence.
- Read-only apart from `POST /entities/{id}/verify` and `POST /verify`, neither of which executed because the registry is empty.
