# Sprint Report — TEFCA ARC Testing + Compliance Evidence v1.0

**Contract:** 7571MN26F80064 · **Date:** 2026-08-01 · **Environment tested:** Development

All figures below come from actual test execution output. Anything not run is
recorded as **Not Executed** with its reason, never estimated.

---

## Readiness matrix

| Area | Status | Evidence |
|------|--------|----------|
| Existing ARC logic audited | **Complete** | `docs/audit/REVIEW_RULES_AUDIT.md`, `VERIFY_RESPONSE_SAMPLE.md`, `WEEKLY_REPORT_SAMPLE.md`, `B3_RESOLUTION_SAMPLE.md`, `CONNECTOR_RESPONSES.md`, `CONNECTOR_HEALTH_MATRIX.md` |
| Dynamic Application Security Testing (DAST) | **Not Executed** | `docs/security/ZAP_FINDING_VALIDATION.md` — no Docker, no JRE, `zapv2` is a client only |
| Security Validation | **36 / 36 PASS** | `AGT-SA-001` |
| RBAC role verification | **5 / 5 PASS**, 5 role accounts authenticated | `docs/audit/RBAC_VERIFICATION_MATRIX.md`, `AGT-TE-006` |
| TEFCA operational validation | **24 PASS / 1 Not Executed** of 25 | `AGT-TE-005` |
| Performance baseline | **Complete** (single-request serial; no load test) | `docs/audit/PERFORMANCE_BASELINE.md`, `AGT-TE-006` |
| API contract validation | **PASS** — OpenAPI 3.1.0, 294 paths, 308 operations, schema-valid | `docs/audit/API_CONTRACT_VALIDATION.md` |
| Connector operational monitoring | **Complete** | `docs/audit/CONNECTOR_OPERATIONAL_MONITORING.md` |
| API v1.0 baseline frozen | **Complete** | `docs/API_VERSION_1.0_BASELINE.md`, `docs/api/openapi_v1.0.json` |
| State registry strategy | **Documented — pending ONC COR concurrence** | `docs/STATE_REGISTRY_STRATEGY.md` |
| Backup / restore procedure | **Documented — restore NOT rehearsed** | `docs/BACKUP_RESTORE_PROCEDURE.md` |
| Evidence packages (.md + .docx) | **3 of 3 delivered** | `docs/compliance/AGT-SA-001`, `AGT-TE-005`, `AGT-TE-006` |
| Backend test suite | **266 passed, 22 skipped, 0 failed** | `test-results.xml` |
| Static analysis (Bandit) | **0 High**, 12 Medium, 124 Low | Block 9 scan |
| Dependency audit | **1 finding** (`ecdsa` PYSEC-2026-1325, unreachable) | `pip-audit -r requirements.txt` |
| Deployment | dev + Azure prod deployed and verified | below |

---

## Test results

| Suite | Total | Pass | Fail | Not Executed |
|-------|-------|------|------|--------------|
| Security Validation | 36 | 36 | 0 | 0 |
| RBAC scenarios | 5 | 5 | 0 | 0 |
| TEFCA operational | 25 | 24 | 0 | 1 |
| pytest (backend unit/integration) | 288 | 266 | 0 | 22 skipped |

**TEF-26** (B3 manual resolution) is Not Executed because no pending B3 review
existed at run time — the only one available had already been resolved during the
Block 1 audit. That resolution, with before/after state, is recorded in
`docs/audit/B3_RESOLUTION_SAMPLE.md`. The capability is evidenced there rather
than claimed in the package.

---

## Findings — all validated before any code change

The mandated workflow was applied: detect → reproduce manually → confirm
exploitability → fix only if confirmed → re-test after fix.

### Two security findings — both confirmed FALSE POSITIVES, no code changed

| Finding | Reproduction | Verdict |
|---------|--------------|---------|
| TEST-SEC-35 — credential in response body | The match was `SAM_GOV_API_KEY` inside the note *"Federal Registration — GSA (requires SAM_GOV_API_KEY)"*. A configuration **name**, not a credential **value**. Nothing was disclosed. | False positive — assertion narrowed to credential value patterns. No application change. |
| TEST-SEC-08 — oversized request body | 1 MB and 11 MB bodies both rejected (401/429) in 2.5 s / 4.0 s. No 500, no hang, no resource exhaustion. The assertion demanded one specific status code. | False positive — assertion corrected to "any rejection; never 500 or hang". No application change. |

**Zero confirmed exploitable security findings.** Auto-fixing either would have
changed working code to satisfy a faulty test.

### One real defect found and fixed — import error detail was silently lost

Found while benchmarking import throughput: a batch reported `error_count: 2`
with `errors: []`. An auditor would see two failures with no record of what they
were.

**Cause.** `TefcaImportBatch.errors` is a plain `JSONB` column with no
`MutableList` wrapper. The constructor was handed the *live* `err_list`, so
SQLAlchemy's committed-state snapshot held a reference to the same list that was
subsequently mutated. By the time `batch.errors = err_list` ran, old and new
compared equal, no UPDATE was emitted, and the column kept the empty list it was
first flushed with. `error_count` — an Integer, genuinely changed — updated
correctly, which is exactly why the two disagreed.

**Fix.** Pass a copy to the constructor so the snapshot stays `[]`, assign a copy
at the end, and `flag_modified` the column so the UPDATE does not depend on how
history comparison treats a JSONB list.

**Re-test.** The first fix (end-assignment copy only) was deployed and
**re-tested — and still failed**, which is what exposed the constructor as the
real cause. After the complete fix: `error_count: 4` with a populated `errors[]`
array. Recorded because the first attempt looked right and was not.

### One test-harness defect — 6 pre-existing failures

`test_injection.py` login tests asserted "a hostile payload must not produce a
500", but a local Postgres was listening with credentials that fail
authentication. Every DB-backed request 500s regardless of input, so the tests
could not distinguish an injection defect from a missing test database. Gated on
the existing `db_required` fixture; they now skip with a reason. Not an
application defect — dev returned 401/429 for the same oversized-body case.

---

## Connector operational monitoring

5 calls each, measured during this window.

| Connector | Uptime | Avg Latency | Note |
|-----------|--------|-------------|------|
| NPPES | 5/5 (100%) | 391 ms | CMS NPI Registry, key-less |
| PECOS | 5/5 (100%) | 242 ms | Same CMS dataset as NPPES — correlated by construction, not independent |
| OIG LEIE | 5/5 (100%) | 428 ms | HHS Exclusion List, public CSV |
| SAM.gov | **0/5 (0%)** | 252 ms | HTTP 404 on all calls — documented `DEMO_KEY` behaviour, not an outage. Requires a registered api.data.gov key. |

This is a point-in-time measurement, not a historical availability record. No
continuous monitoring exists.

---

## Performance baseline (selected)

| Measurement | Result |
|-------------|--------|
| CSV parse + validate | 61,884 rows/sec at 5,000 rows (in-process, no DB) |
| End-to-end CSV import | 50 rows in 11.94 s (HTTP 200, 48 imported, 2 errors) |
| Read-path latency | 0.71–1.36 s mean across 4 endpoints |
| Entity verification | 1.84 s mean (n=10, includes live third-party registries) |
| Report generation | weekly 0.86 s, quarterly 0.90 s |

**Large-volume end-to-end import: Not Executed.** The registry exposes no delete
endpoint, so a 1,000+ row benchmark would permanently seed synthetic entities
into the dev ARC registry and contaminate every subsequent sample draw and
weekly report. The parse stage is measured at 5,000 rows because it is the part
measurable without writing anything. No throughput was extrapolated between the
two.

**No concurrent-load or soak test was run.** All figures are single-request
serial measurements.

---

## Deployment

| Target | Result |
|--------|--------|
| dev backend (`docuaction-dev`) | Deployed, restarted, `/health` 200, import fix re-tested and confirmed |
| Azure prod (`Docuaction`) | Deployed `--clean true`, restarted, `/health` 200, `/api/config` reports `environment=production` |
| Rollback artifact | `prod-deploy.prev.zip` preserved before deploy |

### Material finding — `api.docuaction.io` did NOT receive this deploy

`api.docuaction.io` still responds with `Server: railway-hikari`. **It is served
by Railway, not by the Azure App Service.** The Azure production app answers on
`api-prod.docuaction.io` and its default `*.azurewebsites.net` host, both of
which were verified serving this build.

The Railway→Azure cutover remains pending. Anything deployed to the Azure
production app — including the import-error fix in this sprint — is **not** live
on `api.docuaction.io` until the DNS cutover happens. This is flagged prominently
because a reader could otherwise reasonably assume "prod deployed and verified"
means the customer-facing host was updated. It was not.

---

## Limitations carried forward

- **DAST Not Executed.** Security Validation exercises paths chosen deliberately;
  a crawler exercises paths nobody thought to choose. The gap is open.
- **Coverage is 3 of 7 possible authoritative sources** (NPPES, PECOS, OIG LEIE).
  SAM.gov, state registries, IRS and the RCE directory are excluded from scoring
  rather than counted as gaps.
- **Restore has never been rehearsed.** RTO is unmeasured.
- **Geo-redundant backup is Disabled on the primary prod database** and cannot be
  enabled after creation. `docuaction-db-geo` exists for cutover.
- Testing was against dev. Production configuration was not assessed.

## Open manual items

- [ ] Railway → Azure DNS cutover for `api.docuaction.io`
- [ ] Request a SAM.gov API key at api.data.gov (free; necessary but not
      sufficient — SAM is keyed on UEI, which the registry does not capture)
- [ ] Confirm the state registry strategy with the ONC COR
- [ ] Rehearse a database restore and measure RTO
- [ ] Run DAST from a runner with Docker or a JRE
- [ ] Rotate dev passwords (recommended manual)
- [ ] Key Vault migration from an approved network
