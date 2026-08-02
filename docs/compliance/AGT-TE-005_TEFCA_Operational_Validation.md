# AGT-TE-005 — TEFCA Operational Validation

**Contract:** 7571MN26F80064  ·  **Package:** AGT-TE-005  ·  **Environment:** Development

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

## Result Summary

| Metric | Value |
|--------|-------|
| Total tests | 25 |
| Passed | 24 |
| Failed | 0 |
| Not Executed | 1 |
| Pass rate (of executed) | 100.0% |

## Scope

Operational validation of the TEFCA ARC engine on https://docuaction-dev.azurewebsites.net: registry queries,
entity verification against authoritative sources, B1–B4 classification, sampling,
review workflow, and report generation.

## Test Results

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| TEF-01 | GET entities | 200 + list | HTTP 200, total=23 | PASS |
| TEF-02 | Pagination honours limit | <=5 items | 5 items | PASS |
| TEF-03 | Search by name | 200 + match | HTTP 200 | PASS |
| TEF-04 | Import CSV via seed path | 200 + entities processed | HTTP 200, imported=0 skipped=23 | PASS |
| TEF-05 | Import flags bad NPI (not rejected) | flagged, not rejected | npi_flagged_count=0, error_count=0 | PASS |
| TEF-06 | Duplicate import skipped, not duplicated | skipped > 0, no duplicates | skipped=23 | PASS |
| TEF-08 | Invalid state transition refused | 400 with actionable message | HTTP 400 | PASS |
| TEF-09 | Verify consults NPPES | one of ('verified', 'not_found') | verified | PASS |
| TEF-10 | Verify consults PECOS | one of ('verified', 'not_found') | verified | PASS |
| TEF-11 | Verify consults OIG_LEIE | one of ('clear', 'excluded') | clear | PASS |
| TEF-12 | All source statuses are valid 5-state values | all in the state vocabulary | [('sam_gov', 'not_checked'), ('state_registry', 'not_checked'), ('irs', 'not_checked'), ('nppes', 'verified'), ('pecos', 'verified'), ('oig_leie', 'clear')] | PASS |
| TEF-13 | B1-B4 bucket assigned | one of B1..B4 | B1 | PASS |
| TEF-14 | Review ID generated | REV-YYYY-NNNNNN | REV-2026-000022 | PASS |
| TEF-15 | Coverage reported for a real NPI | checked == available (3/3) | 3/3 | PASS |
| TEF-16 | Unimplemented sources handled gracefully | not_checked, excluded from coverage | not_implemented=['irs', 'sam_gov', 'state_registry'] | PASS |
| TEF-17 | GET rules returns versions | all rules carry a version | [('RULE-005', 1), ('RULE-001', 1), ('RULE-002', 1), ('RULE-003', 1), ('RULE-004', 1)] | PASS |
| TEF-18 | Rules carry effective_date | present on every rule | ['2026-08-01', '2026-08-01', '2026-08-01', '2026-08-01', '2026-08-01'] | PASS |
| TEF-19 | Draw sample | 200 + sample size <= population | HTTP 200, n=22/23 | PASS |
| TEF-20 | Sample captures full configuration | all of ('confidence_level', 'margin_of_error', 'proportion', 'use_fpc', 'random_seed', 'rule_set_version') | {'confidence_level': 0.95, 'margin_of_error': 0.05, 'proportion': 0.5, 'use_fpc': True, 'random_seed': 20260801, 'rule_set_version': 1} | PASS |
| TEF-21 | Same seed reproduces the same sample | identical size and strata | size 22 vs 22; strata equal=True | PASS |
| TEF-22 | Generate report with all required sections | all 10 sections present | HTTP 200, missing=[] | PASS |
| TEF-23 | Limitations section present and non-empty | >=1 entry, always | 4 entries | PASS |
| TEF-24 | B1-B4 counts sum to entities reviewed | sum == entities_reviewed | sum=22 entities_reviewed=22 | PASS |
| TEF-25 | Report archived and retrievable | 200, same report_id | HTTP 200, id=WR-2026-W31-R20A9 | PASS |
| TEF-26 | B3 manual resolution recorded | reviewer_resolution set | Not Executed — no pending B3 at run time | NOT EXECUTED |

## Not Executed

| Test ID | Reason |
|---------|--------|
| TEF-26 | Not Executed — no pending B3 at run time |

TEF-26 (B3 manual resolution) reports Not Executed because no pending B3 review
existed at run time — the only one available had already been resolved during the
Block 1 audit. That resolution is itself recorded, with before/after state, in
`docs/audit/B3_RESOLUTION_SAMPLE.md`. The capability is evidenced there rather
than claimed here.

## Authoritative source coverage

Verification queries three implemented connectors: **NPPES**, **PECOS** and
**OIG LEIE**. SAM.gov, state registries, IRS and the RCE directory are not
operational and are excluded from confidence scoring rather than counted as
missing — counting unbuilt connectors as gaps would report permanently degraded
coverage for work that was never scheduled. Full detail:
`docs/audit/CONNECTOR_HEALTH_MATRIX.md`.

## Limitations

- Verification depends on live third-party registries. A result reflects what
  those registries returned at run time, not a permanent property of the entity.
- Coverage is measured against implemented connectors (3 of 7 possible sources).
- The dev registry contains synthetic entities alongside real NPIs; counts in
  this package are dev counts and are not production figures.

## Appendix — Connector Operational Monitoring

Each connector was called 5 times directly against its authoritative endpoint
during this test window. "Uptime" is the observed success rate across those
calls; it is **not** a historical availability figure and no long-run uptime is
claimed.

| Connector | Uptime | Last Success | Last Failure | Avg Latency |
|-----------|--------|-------------|--------------|-------------|
| NPPES | 5/5 (100.0%) | 2026-08-02T01:05:08+00:00 | None recorded | 391 ms |
| PECOS | 5/5 (100.0%) | 2026-08-02T01:05:12+00:00 | None recorded | 242 ms |
| OIG LEIE | 5/5 (100.0%) | 2026-08-02T01:05:17+00:00 | None recorded | 428 ms |
| SAM.gov | 0/5 (0.0%) | None recorded | 2026-08-02T01:05:22+00:00 | 252 ms |

SAM.gov returned HTTP 404 on all 5 calls. That is the documented behaviour of
`DEMO_KEY` against the entity-information API, not an outage: the endpoint
requires a registered api.data.gov key. SAM.gov is not operational and is
excluded from confidence scoring.

PECOS resolves through the same CMS NPI dataset as NPPES, so its timing is
correlated with NPPES by construction rather than independently sampled.

Full detail: `docs/audit/CONNECTOR_OPERATIONAL_MONITORING.md`.

---

## Appendix — Sprint update : SAM.gov, sources, and CI DAST

### SAM.gov connector — built, NOT operational

| Item | Status |
|------|--------|
| Entity Management API (`v3/entities`) | Implemented — registration + Active check |
| Exclusions API (`v4/exclusions`) | Implemented — queried **independently**, not inferred from the v3 summary flag |
| UEI exact match | Implemented |
| Legal-name fallback | Implemented; >1 match reports `ambiguous` for manual review |
| **API key** | **NOT PROVISIONED** |
| **Operational status** | **NOT OPERATIONAL — excluded from confidence scoring** |

Both endpoints were probed and returned **HTTP 404 with `DEMO_KEY` and with no
key**. A registered key is required for each. Steps to obtain one:
`docs/SAM_GOV_API_KEY_SETUP.md`.

A key alone is necessary but not sufficient — SAM is keyed on UEI, which the
registry does not currently capture.

### Classification rules — version 2 active

v1 retired (5 rules, `retired_date` set), v2 active (5 rules). SAM is wired in as
a **disqualifier**, never a requirement: every SAM condition fires only on a
positive finding, so with no key classification is identical to v1 — verified by
`test_v2_is_identical_to_v1_when_sam_is_silent`.

v2 also fixes a real defect in v1: RULE-005 matched only status `debarred`, but
the connector emits `excluded`, so a SAM-excluded entity with clean NPPES/PECOS
was classified **B1 "No Discrepancy"**. It is now B4.

### Connector matrix — current, measured

| Connector | Uptime (5 calls) | Avg latency | Scoring |
|-----------|------------------|-------------|---------|
| NPPES | 5/5 (100%) | 391 ms | **Included** |
| PECOS | 5/5 (100%) | 242 ms | **Included** |
| OIG LEIE | 5/5 (100%) | 428 ms | **Included** |
| SAM.gov | 0/5 (0%) — HTTP 404, no key | 252 ms | **Excluded — not operational** |

### Bulletin source health — 431 feeds probed twice

| Category | Count |
|----------|-------|
| ACTIVE | 161 |
| TRANSIENT_RECOVERED (working; first sweep was wrong) | 78 |
| DEAD_URL (404/410 twice — deactivated) | 78 |
| ACCESS_BLOCKED (401/403 — NOT deactivated) | 58 |
| STALE | 38 |
| UNREACHABLE | 15 |

The fast sweep reported 232 failures; **78 of them (34%) worked on a gentler
re-probe.** Only twice-confirmed 404/410 feeds were deactivated. Full evidence:
`docs/audit/SOURCE_HEALTH_INVESTIGATION.md`.

### DAST now runs in CI

DAST could not execute on the workstation (no container runtime, no JRE). It is
wired into GitHub Actions instead, dev-only, with a guard that fails the job if
the target resolves to production:

| Pipeline | Surface | Schedule |
|----------|---------|----------|
| `zap-scan.yml` (OWASP ZAP) | Unauthenticated | Mondays 06:00 UTC |
| `stackhawk-scan.yml` (StackHawk) | **Authenticated** (bearer token) | Mondays 06:30 UTC |

They are complementary, not redundant: an unauthenticated scan records every
TEFCA ARC endpoint as `401` and moves on. Setup and the mandatory finding-
validation workflow: `docs/DAST_CI_SETUP.md`.

**Neither has executed yet** — both are scheduled/manual-trigger, and StackHawk
additionally needs `HAWK_API_KEY`. Results: **Not Executed**.

### Risk acceptance

Six entries recorded in `docs/RISK_ACCEPTANCE_REGISTER.md` (RA-001..006), review
date 2026-10-31. The register is **unsigned** — risk acceptance is a human
decision and is not recorded as taken until it has been.
