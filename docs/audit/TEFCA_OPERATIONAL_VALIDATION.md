# TEFCA Operational Validation — Block 5

**Contract:** 7571MN26F80064

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL (Azure) |
| Deployment | Azure App Service |
| Build | Git SHA `6822dc441e1c6d741a592e00bded7eeaf27055d7` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-02T21:18:31.782613+00:00 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| httpx | 0.28.1 |

## Result summary

| Suite | Tests | Pass | Fail |
|---|---|---|---|
| Suite E — Entity Operations | 8 | 8 | 0 |
| Suite F — Verification | 8 | 8 | 0 |
| Suite G — Rules, Sampling, Reports | 10 | 10 | 0 |
| **Total** | **26** | **26** | **0** |

## Suite E — Entity Operations

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| E1 | GET entities returns the registry | 200 with entities | HTTP 200, count=71 | **PASS** |
| E2 | Pagination returns distinct pages | 5 per page, no overlap | page1=5, page2=5, overlap=0 | **PASS** |
| E3 | Search finds a known entity | 200, >=1 match | HTTP 200, count=1 | **PASS** |
| E4 | CSV import creates entities | 200, imported>=1 | HTTP 200, imported=1, errors=0 | **PASS** |
| E5 | Invalid NPI is flagged on verification | B4 (npi_invalid) | imported=1, bucket=B4 | **PASS** |
| E6 | Re-import of same TEFCAID updates, does not duplicate | imported=0 and exactly 1 row exists | imported=0, skipped=1, errors=0, rows=1 | **PASS** |
| E7 | Valid lifecycle transition is applied | draft -> pending_verification | draft -> pending_verification | **PASS** |
| E8 | Invalid EntityLevel rejected with an error | 400 or error_count>=1 | HTTP 200, error_count=1 | **PASS** |

## Suite F — Verification

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| F1 | NPPES is queried | status present | nppes=verified | **PASS** |
| F2 | PECOS is queried | status present | pecos=verified | **PASS** |
| F3 | OIG LEIE is queried | status present | oig_leie=clear | **PASS** |
| F4 | Statuses drawn from the defined vocabulary | subset of the defined set | observed=['clear', 'not_checked', 'verified'] | **PASS** |
| F5 | A B1-B4 bucket is assigned | one of B1..B4 | bucket=B1 | **PASS** |
| F6 | A review ID is generated | REV-YYYY-NNNNNN | review_id=REV-2026-000039 | **PASS** |
| F7 | Confidence is non-null for a real NPI | non-null | confidence_keys=['coverage_note', 'not_implemented', 'sources_available', 'sources_checked', 'sources_failed', 'sources_not_checked', 'sources_not_implemented', 'sources_unavailable', 'sources_verified'] | **PASS** |
| F8 | Unavailable source degrades gracefully | not_checked/unavailable + reason, request still 200 | HTTP 200, sam_gov=not_checked | **PASS** |

## Suite G — Rules, Sampling, Reports

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| G1 | Rules expose a version | every rule versioned | n=11, all_versioned=True | **PASS** |
| G2 | Rules expose effective_date | every rule dated | all_dated=True | **PASS** |
| G3 | Sample drawn with a computed size | size>0 | HTTP 200, size=62 | **PASS** |
| G4 | Sampling configuration is captured | >=3 config fields | captured=['confidence_level', 'margin_of_error', 'proportion', 'random_seed', 'use_fpc', 'population_size'] | **PASS** |
| G5 | Same seed reproduces the same sample size | size1==size2 | size1=62, size2=62 | **PASS** |
| G6 | Report generated with expected sections | executive_summary + classification_distribution present | HTTP 200, sections=['executive_summary', 'classification_distribution', 'limitations', 'sampling_summary', 'period'] | **PASS** |
| G7 | Mandatory limitations section present | non-empty | present=True, len=416 | **PASS** |
| G8 | B1-B4 counts reconcile with entities reviewed | sum(counts) == entities_reviewed | counts={'B1': 23, 'B2': 2, 'B3': 5, 'B4': 9}, sum=39, reviewed=39 | **PASS** |
| G9 | Report is archived and retrievable | generated report_id appears in /arc/reports | stored=19, this_report_archived=True | **PASS** |
| G10 | B3 resolution workflow functions | 200 and resolution recorded | HTTP 200, resolution=reclassified, effective=B2 | **PASS** |

## Finding — CSV import returns raw database exceptions to the client

| Field | Value |
|---|---|
| Severity | **Medium** — information disclosure |
| Endpoint | `POST /api/tefca/registry/import/csv` |
| Status | **CONFIRMED** by reproduction |
| Discovered | Block 5, Suite E |

**Reproduction.** Import a CSV whose row claims an NPI already held by another
entity. The request returns `HTTP 200` with `status: "failed"`, and the `errors`
array contains the unmodified SQLAlchemy exception.

**What is disclosed.** Every marker below was verified present in the response
body returned to the API client:

| Disclosed | Present |
|---|---|
| ORM name (`sqlalchemy`) | YES |
| Database driver (`asyncpg`) | YES |
| Exception class (`UniqueViolationError`) | YES |
| Constraint name (`idx_tefca_ident_unique`) | YES |
| Table name (`tefca_entity_identifiers`) | YES |
| Raw `INSERT` statement | YES |
| Column names | YES |

**Sample, exactly as returned:**

```
Leak Repro 1785705627: IntegrityError: (sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class 'asyncpg.exceptions.UniqueViolationError'>: duplicate key value violates unique constraint "idx_tefca_ident_unique"
DETAIL:  Key (identifier_type, identifier_value, system_uri)=(npi, 1770626038, http://hl7.org/fhir/sid/us-npi) already exists.
[SQL: INSERT INTO tefca_entity_identifiers (id, entity_id, identifier_type, identifier_value, system_uri, is_primary, identifier_status, effective_date, end_date) VALUES ($1::UUID, $2::UUID, $3::VARCHAR, $4::VARCHAR, $5::VARCHAR, $6::BOOLEAN, $7::VARCHAR,
```

**Why this matters.** The application's general error handler is correct — Block 3
Suites D1–D3 confirmed that malformed input elsewhere returns a generic message
with a `request_id` and no internals. This path bypasses that handler: the import
routine catches the exception per row and copies `str(exc)` into a user-visible
`errors` list. An attacker learns the schema, the constraint layout and the exact
INSERT shape without needing any other vulnerability.

**Note on the status code.** The response is `HTTP 200` with `status: "failed"`.
A batch import legitimately reports partial success, so 200 is defensible — but it
means monitoring keyed on status codes will not see these failures.

**Recommended fix.** Map database exceptions to a caller-safe message at the
import boundary (for a unique violation: *"NPI 1770626038 is already assigned to
another entity"*), and log the full exception server-side against the
`request_id`. This is a Medium-severity finding, so under the governing rule it is
recorded for risk acceptance rather than fixed inside this validation block —
changing import behaviour mid-validation would invalidate Suite E above.

## Corrections to this run's own method

Two Suite E tests initially failed on faulty premises rather than application
behaviour, and are recorded here because the corrected result is the one
tabulated above.

**E7 — lifecycle transition.** The first attempt observed `active -> active` and
was read as a missing transition. The CSV importer defaults `OperationalStatus`
to `active` when the column is absent (`csv_import.py`), so the entity never sat
in `draft` and there was no transition to make. Re-imported with
`OperationalStatus=draft`, verification applied `draft -> pending_verification`
with `allowed: true`.

**E5 — invalid NPI.** The first attempt reported `imported=0`, because NPI
`1234567890` had already been consumed by an earlier aborted run and the row
failed on the identifier unique constraint before ever reaching verification —
the same constraint behind the finding above. Re-run with a verified-unused
invalid NPI, the row imported, raised a validation warning at import time, and
classified **B4** with rationale *"npi_validation is invalid"*.

**Response-shape parsing.** An earlier pass reported seven Suite G failures that
were entirely artefacts of reading the wrong JSON keys. These endpoints return
`{"rules": [...]}`, `{"reviews": [...]}` and `{"reports": [...]}` rather than a
bare list or an `items` key, and `POST /arc/reports/generate` nests its content
under `data`. Shapes were confirmed empirically before the results above were
accepted.
