# API Contract Validation

**Contract:** 7571MN26F80064

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL (Azure) |
| Deployment | Azure App Service (Linux) |
| Build | Git SHA `706a2f641f3a48f3dc117f57d579ddc82dbd5686` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-01T22:33:41+00:00 |
| Contract | 7571MN26F80064 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| pytest | pytest 9.1.1 |
| Bandit | __main__.py 1.9.4 |
| openapi-spec-validator | 0.9.0 |
| curl | curl 8.21.0 (Windows) libcurl/8.21.0 Schannel zlib/1.3.2 WinIDN WinLDAP |
| OWASP ZAP | Not Available — see ZAP_FINDING_VALIDATION.md |

## Specification

| Field | Value |
|-------|-------|
| OpenAPI version | 3.1.0 |
| Documented paths | 294 |
| Documented operations | 308 |
| TEFCA paths | 91 |
| Spec source | `GET https://docuaction-dev.azurewebsites.net/openapi.json` |

## Schema validation

| Test ID | Description | Expected | Actual | Result |
|---------|-------------|----------|--------|--------|
| API-01 | `openapi.json` retrievable from dev | HTTP 200, JSON body | HTTP 200, 256,354 bytes | PASS |
| API-02 | Document validates against the OpenAPI 3.1.0 schema | No validation errors | `openapi-spec-validator` reported no errors | PASS |
| API-03 | Every documented path carries at least one operation | 0 empty paths | 0 empty paths | PASS |

## Live behaviour vs contract

The 25 TEFCA operational tests in `AGT-TE-005` exercise the documented
endpoints against the running dev service and compare observed status codes and
payload shape to the contract. Result: **24 PASS / 0 FAIL / 1 Not
Executed.**

## TEFCA paths in the specification

- `/api/tefca/admin/seed-mock-data`
- `/api/tefca/arc/priority-review`
- `/api/tefca/arc/reports`
- `/api/tefca/arc/reports/generate`
- `/api/tefca/arc/reports/{report_id}`
- `/api/tefca/arc/reports/{report_id}/excel`
- `/api/tefca/arc/reports/{report_id}/html`
- `/api/tefca/arc/review-rules`
- `/api/tefca/arc/review-rules/history`
- `/api/tefca/arc/review-rules/{rule_id}`
- `/api/tefca/arc/reviews`
- `/api/tefca/arc/reviews/{review_id}`
- `/api/tefca/arc/reviews/{review_id}/resolve`
- `/api/tefca/arc/samples`
- `/api/tefca/arc/samples/{sample_id}`
- `/api/tefca/arc/samples/{sample_id}/stats`
- `/api/tefca/dashboard/summary`
- `/api/tefca/dashboard/trends`
- `/api/tefca/demo/run-cycle`
- `/api/tefca/discrepancy-taxonomy`
- `/api/tefca/entities/upload`
- `/api/tefca/findings`
- `/api/tefca/findings/{finding_id}`
- `/api/tefca/import/history`
- `/api/tefca/methodology`
- `/api/tefca/priority`
- `/api/tefca/priority/create`
- `/api/tefca/priority/quarterly-report`
- `/api/tefca/priority/{case_id}`
- `/api/tefca/priority/{case_id}/execute`
- `/api/tefca/priority/{case_id}/report`
- `/api/tefca/qa/alerts`
- `/api/tefca/qa/alerts/test`
- `/api/tefca/qa/audit`
- `/api/tefca/qa/audit/export`
- `/api/tefca/qa/connector-health`
- `/api/tefca/qa/evidence-summary`
- `/api/tefca/qa/golden-records`
- `/api/tefca/qa/health`
- `/api/tefca/qa/inter-rater`
- `/api/tefca/qa/internal-consistency`
- `/api/tefca/qa/regression`
- `/api/tefca/qa/report`
- `/api/tefca/qa/report-gate`
- `/api/tefca/qa/sampling-validation`
- `/api/tefca/qa/score`
- `/api/tefca/qa/sla`
- `/api/tefca/qa/statistical`
- `/api/tefca/qa/sweep`
- `/api/tefca/qa/validate-evidence/{review_id}`
- `/api/tefca/qa/validate-review/{review_id}`
- `/api/tefca/registry/dev/seed`
- `/api/tefca/registry/entities`
- `/api/tefca/registry/entities/{entity_id}`
- `/api/tefca/registry/entities/{entity_id}/children`
- `/api/tefca/registry/entities/{entity_id}/findings`
- `/api/tefca/registry/entities/{entity_id}/hierarchy`
- `/api/tefca/registry/entities/{entity_id}/status`
- `/api/tefca/registry/entities/{entity_id}/verify`
- `/api/tefca/registry/findings`
- `/api/tefca/registry/hierarchy`
- `/api/tefca/registry/import/csv`
- `/api/tefca/registry/import/fhir-bundle`
- `/api/tefca/registry/import/history`
- `/api/tefca/registry/import/{batch_id}`
- `/api/tefca/registry/participants`
- `/api/tefca/registry/qhins`
- `/api/tefca/registry/search`
- `/api/tefca/registry/stats`
- `/api/tefca/registry/verification-jobs`
- `/api/tefca/registry/verification-jobs/{job_id}`
- `/api/tefca/registry/verify`
- `/api/tefca/reports`
- `/api/tefca/reports/biweekly`
- `/api/tefca/reports/export`
- `/api/tefca/reports/final`
- `/api/tefca/reports/quarterly`
- `/api/tefca/reports/weekly`
- `/api/tefca/reports/{report_id}`
- `/api/tefca/reports/{report_id}/csv`
- `/api/tefca/reports/{report_id}/docx`
- `/api/tefca/reports/{report_id}/download`
- `/api/tefca/reports/{report_id}/pdf`
- `/api/tefca/reviews`
- `/api/tefca/reviews/new-submissions`
- `/api/tefca/reviews/run-sample`
- `/api/tefca/reviews/{review_id}`
- `/api/tefca/reviews/{review_id}/execute`
- `/api/tefca/sampling-runs`
- `/api/tefca/search`
- `/api/tefca/status`

## Limitations

- Validation confirms the document is a **well-formed and schema-valid** OpenAPI
  3.1.0 document, and that the TEFCA endpoints behave as tested. It does **not**
  prove every one of the 308 documented operations matches its declared
  request/response schema at runtime — only the endpoints covered by the
  operational and security suites were exercised.
- Response-schema conformance checking for the full surface: **Not Executed.**
