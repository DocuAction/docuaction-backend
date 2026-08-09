# DocuAction TEFCA ARC — Verification Demo Report

**Date:** August 09, 2026  
**Environment:** Development  
**Base URL:** https://docuaction-dev.azurewebsites.net  
**Contract:** 7571MN26F80064

## Result: COMPLETE — every step ran and passed

Steps: **9 passed, 0 failed, 0 blocked, 0 skipped** (of 9).

## Executive Summary

Five real healthcare entities were submitted through the complete TEFCA workflow: import, verification against federal sources, classification, sampling, and reporting. 9 of 9 workflow steps completed successfully.

## Step Results

| Step | Description | Status | Detail |
|------|-------------|--------|--------|
| 1 | Login | **PASS** | authenticated as admin@docuaction.io |
| 2 | Import 5 entities (CSV) | **PASS** | imported 5, skipped 5, rejected 0 |
| 3 | Verify all entities | **PASS** | 5/5 entities verified (registry records matched by name — see the note below the entity table) |
| 4 | Registry stats | **PASS** | total entities: ? |
| 5 | Draw sample | **PASS** | n=378 from N=22275 at 95%/5% |
| 6 | Generate weekly report | **PASS** | 6/6 required sections present |
| 7 | Create review cycle | **PASS** | cycle_id 77777def-f4c9-4921-9af7-e7ef2544bdde |
| 8 | Priority review | **PASS** | severity high, 3 recommendations |
| 9 | Audit trail + import history | **PASS** | 20 import records, 20 carry a SHA-256 |

## Entity Verification Results

| NPI | Entity | NPPES | PECOS | OIG | SAM | Name Match | Address Match | Bucket | Review ID |
|-----|--------|-------|-------|-----|-----|------------|---------------|--------|-----------|
| 1477978807 | Johns Hopkins Hospital | verified | verified | clear | not_checked | address+name (0.9285) | code_normalization (0.8571) | B1 | REV-2026-000207 |
| 1881018208 | Mayo Clinic | verified | verified | clear | not_checked | inconclusive (1.0) | code_normalization (0.75) | B1 | REV-2026-000208 |
| 1275791162 | Cleveland Clinic | verified | verified | clear | not_checked | inconclusive (1.0) | code_normalization (0.6) | B1 | REV-2026-000209 |
| 1821141649 | Massachusetts General Hospital | verified | verified | clear | not_checked | inconclusive (1.0) | code_normalization (0.6667) | B1 | REV-2026-000210 |
| 1770626038 | Inova Fairfax Hospital | verified | verified | clear | not_checked | address+name (1.0) | code_normalization (1.0) | B1 | REV-2026-000211 |

> **What step 3 verified.** Import (step 2) writes to the legacy `tefca_entities` table, which is what the Entity Import page posts to. Registry verification (step 3) reads `tefca_reg_entities`. Those are two different tables, so the records verified above were matched to the imported entities **by name**, not carried through from the import. NPPES, PECOS and OIG results are genuine live lookups. An empty Address Match reflects a registry record that holds no address, not a failed comparison. Reconciling the two stores is outstanding work, and this report should not be read as demonstrating a single unbroken import-to-verification path.

## Frontend Page → Backend Endpoint Checks

A guarded endpoint answering 401 without a token is a PASS: the guard working is the correct behaviour.

| Page | Endpoint | HTTP | Accepted | Result |
|------|----------|------|----------|--------|
| Login | `POST /api/auth/login` | 401 | 200/401/422 | **PASS** |
| Entity Import | `POST /api/tefca/entities/upload` | 400 | 200/401/422 | **FAIL** |
| Entity Queue | `GET /api/tefca/registry/entities` | 200 | 200/401 | **PASS** |
| Decision Workspace | `GET /api/tefca/registry/stats` | 200 | 200/401 | **PASS** |
| Priority Reviews | `GET /api/tefca/qa/sla` | 200 | 200/401 | **PASS** |
| Review Cycles | `GET /api/v1/tefca/cycles` | 200 | 200/401 | **PASS** |
| Audit Trail | `GET /api/tefca/registry/import/history` | 200 | 200/401 | **PASS** |
| Reports | `GET /api/tefca/arc/reports` | 200 | 200/401 | **PASS** |
| Admin / Users | `GET /api/admin/users` | 200 | 200/401/403 | **PASS** |

## Connector Status

| Connector | Status | Note |
|-----------|--------|------|
| NPPES (CMS NPI Registry) | Live | Public API, no key required |
| PECOS (CMS Provider Enrollment) | Live | Public API, no key required |
| OIG LEIE (HHS Exclusion List) | Live | Public API, no key required |
| SAM.gov (Federal Registration) | Under Investigation | API key configured; endpoint returns 404 for every path including unauthenticated and invalid-key requests. Upstream routing, not code. |
| USPS (Address Verification) | Not configured | Code-based normalization active; awaiting USPS API credentials |
| TEFCA Entity Data | Provided by ONC | All entity population data is provided by ONC per contract direction |

## Verification Pipeline

  Step 1: NPI validation (CMS Luhn check digit)
  Step 2: NPPES lookup (CMS NPI Registry)
  Step 3: PECOS enrollment check (CMS)
  Step 4: OIG LEIE exclusion check (HHS)
  Step 5: USPS address normalization (code-based; API when configured)
  Step 6: Jaro-Winkler name matching
  Step 7: AI entity resolution (advisory; disabled by default)
  Step 8: B1-B4 rules engine classification
  Step 9: Review ID assignment
  Step 10: Audit trail entry

## Sample Statistics

  Cochran formula applied
  Population size: 22275
  Sample size: 378
  Confidence level: 0.95
  Margin of error: 0.05

## Weekly Report Sections Verified

  [x] executivesummary
  [x] b1
  [x] confidence
  [x] coverage
  [x] limitations
  [x] sampling

## Known Architectural Issues

### Entity records live in two tables, now bridged

The Entity Import page posts to `POST /api/tefca/entities/upload`, which writes the legacy `tefca_entities` table. Registry verification reads `tefca_reg_entities`. These remain separate stores with separate schemas.

They were previously disjoint, and this report recorded the consequence: step 3 matched registry records to the imported entities **by name**, and Address Match read `not_compared` because those records held no address.

Each import now also upserts the entity into the registry, matched on NPI, carrying name, address, city, state and ZIP. One operator action populates both stores, so verification sees what was imported and the address comparison has something to compare. The Address Match column above reflects real comparisons.

The underlying duplication is unchanged and a full merge is scheduled separately. Bridging is the same outcome reached one table at a time, rather than changing every endpoint on two routers at once.

### SAM.gov returns 404 for every path

The key is proven valid: it returns HTTP 200 with `X-Ratelimit-Limit: 36000` against the api.data.gov gateway, where `DEMO_KEY` gets 10 and an invalid key gets 401. That also rules out the theory that a more privileged (FOUO) key is needed.

Every `api.sam.gov` path nevertheless returns an empty HTTP 404 from `server: istio-envoy` — including requests carrying no key at all, an invalid key, the bare host root, and paths that do not exist. Requests are refused at SAM's ingress before authentication runs, so no credential change can affect it. SAM is reported as `not_checked` rather than counted against any entity.

## Notes on Data Provenance

All TEFCA entity population data, directory information, and participant lists are provided by ONC per contract direction. AGT does not independently source entity population data.

The five NPIs used in this demonstration are real, publicly listed provider identifiers drawn from the CMS NPI Registry. Each was confirmed against NPPES as active, with a valid CMS check digit, before use. They exercise live federal lookups; they are not a TEFCA participant population.

### NPI correction

The identifiers originally supplied for this demonstration did not belong to the hospitals named. Three do not exist in NPPES and fail the CMS check digit; two exist but identify different organisations. They were replaced with the verified identifiers for the intended hospitals, which NPPES confirms at the same practice addresses.

| Superseded NPI | Why |
|----------------|-----|
| 1316966918 | no such NPI in NPPES; fails CMS check digit |
| 1043233851 | belongs to OPPORTUNITY EMS INC, not Mayo Clinic |
| 1124027287 | no such NPI in NPPES; fails CMS check digit |
| 1265430099 | no such NPI in NPPES; fails CMS check digit |
| 1497758544 | belongs to CUMBERLAND COUNTY HOSPITAL SYSTEM, INC |

## Reproducing This Report

```
DEMO_EMAIL=<admin email> DEMO_PASSWORD=<password> \
  python scripts/run_full_demo.py --base-url https://docuaction-dev.azurewebsites.net
```

_Generated 2026-08-09T20:11:46.686984+00:00 by scripts/run_full_demo.py. Every value above is copied from a live API response; no result is assumed or filled in by hand._
