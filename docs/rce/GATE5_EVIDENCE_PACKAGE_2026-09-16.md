# Gate 5 Evidence Package — Official-Delivery Traceability Remediation

Date: 2026-09-17 (work performed 2026-09-16 evening to 2026-09-17, US Eastern)
Status: Gates 2, 3 and 4 complete in an isolated runtime. **Stopped at Gate 5. Nothing deployed. No shared database written. No branch merged.**

## 1. Executive summary

The ONC/RCE official-delivery workflow now answers, from persisted evidence, what file was received, who registered it, what every stage did and when, what happened to every delivered row, which findings and identifier conflicts exist, what was verified, what a report was built from, and which application build produced each fact. Delivery rows navigate to a job-keyed detail page that works for processed, failed and never-parsed jobs. NPI validation emits nine distinct outcomes; an invalid-checksum NPI holds the record and is never promoted. An identifier that conflicts with the registry is raised beside the quality finding, both values are shown, and the registry is not changed until an analyst decides. Reconciliation enforces Received = Created + Updated + Matched/Unchanged + Held + Rejected + Missing Key + Excluded and persists hashed snapshots that the delivery report cites.

Everything was proven on an isolated PostgreSQL 18 cluster with the real application objects, the real static frontend build in a real browser, the two synthetic three-record fixtures, and the 184-record client demo file. The historical "S" delivery on DEV was not touched.

## 2. Root causes fixed

| Diagnostic finding | Fix |
|---|---|
| Row click silently did nothing without an intake id; detail rendered off-screen | Explicit "View details" link plus row activation to `/tefca-arc/deliveries/detail/?job={job_id}`; job-keyed API resolves any job |
| No job-keyed contract; no timeline, dispositions, exceptions, coverage, reports in one place | `GET /api/tefca/rce/delivery-jobs/{id}/detail` with `resolved_from`, availability per block, build and correlation identity |
| "View exceptions" opened a legacy page that ignored the delivery | Delivery-scoped exception ledger endpoint and tab; Validation Queue re-pointed at the held-record work queue |
| NPI length and format shared one code; checksum was MEDIUM and promoted | Rule set 1.2.0 with nine distinct codes; checksum HIGH, holds, blocked at promotion |
| Existing NPI silently retained on match; delivered value invisible to the registry | Conflict comparison for every matched row, `NPI_EXISTING_VALUE_CONFLICT`, append-only decision events, no overwrite until `CONFIRM_SUBMITTED` |
| Matched records counted as updated with no history | Material-field comparison; `MATCHED_UNCHANGED` versus `UPDATED` with a version row and audit |
| Held records never reached an analyst | DQ review bridge wired after curation and promotion; work-queue intake filter |
| No per-row disposition, no persisted reconciliation, READY on success regardless | Disposition events per row, reconciliation snapshots with the equation enforced by a database CHECK, READY only when passed |
| Single ambiguous status | Two-axis Processing outcome / Review state derived from persisted evidence |
| No per-stage timestamps | Stage event rows per attempt with start, end, duration, counts and failure |
| No delivery report; reports defaulted to the newest intake | `delivery_processing` report (HTML, PDF, CSV) requiring an explicit id; durable delivery-report links; generation and download audited; regeneration pinned to a snapshot |
| Static PECOS "partial" | Data-driven connector and coverage badges |
| No request correlation, no structured logs, no build identity | Request-context middleware, JSON logging with redaction, `git_sha` and `build_time` in `/health`, admin health with migration revision |
| `/api/v1/tefca` outside the module gate | Prefix added; tested |
| Legacy synchronous upload at contributor floor | Raised to program_manager, deprecated headers, audited |

## 3. Branches and commits

| Repository | Branch | Base | Head |
|---|---|---|---|
| docuaction-backend | fix/delivery-workflow-remediation | origin/main 51b8735 | c8821db (00ec451 remediation, c8821db evidence ignore rule; PR #59 cherry-picked as abf52bc, 35df1c4) |
| docuaction-frontend | fix/delivery-workflow-remediation | origin/main 04d7af7 | 39cf105 |

Worktrees: `backend-remediation` and `frontend-remediation` under the project folder. Nothing pushed.

## 4. Files changed

Backend: 109 files, +22,797 / −285 (one migration, 21 new test files, 19 documentation and evidence files). Frontend: 56 files, +7,545 / −784. Full lists via `git diff --stat origin/main HEAD` in each worktree.

## 5. Migrations added

`alembic/versions/20260917_delivery_traceability.py` (revises `20260915_curated_text_columns`). Creates `rce_delivery_stage_events`, `rce_disposition_events`, `rce_reconciliation_snapshots`, `tefca_identifier_decision_events`, `rce_delivery_report_links` and the view `rce_current_dispositions`. Guarded, offline-safe, no DML. Runtime grants: SELECT and INSERT on all five, UPDATE on stage events only, DELETE nowhere. Pre-checks `REFERENCES` privilege on referenced tables and prints the exact GRANT statements if missing (expected on DEV where several referenced tables are runtime-owned). Downgrade refuses when any evidence row exists. Proven by `tests/test_traceability_migration.py` (upgrade, downgrade on empty, re-upgrade, grant matrix, append-only by grant, refusal with evidence, view semantics, equation CHECK) and by the convergence chain tests on a pristine cluster.

## 6. API changes

Additive unless noted. New under `/api/tefca/rce`: `GET /delivery-jobs/{id}/detail`, `GET /delivery-jobs/{id}/timeline`, `GET /deliveries/{intake_id}/dispositions` and `.csv`, `GET /deliveries/{intake_id}/exceptions`, `GET /deliveries/{intake_id}/verification-coverage`, `GET /deliveries/{intake_id}/audit`, `POST /issues/{issue_id}/dispositions`, `POST /identifier-decisions`. New: `GET /api/admin/health` (admin), `GET /api/reports/by-delivery/{job_id}`, report type `delivery_processing`. Changed: `/delivery-jobs` items carry `processing_outcome` and `review_state`; `/deliveries/{id}/dashboard` adds status, dispositions, snapshot; `/records`, `/curated`, `/curated/{id}/lineage`, `/issues` move from viewer to reviewer (intentional contract change); `POST /deliveries` (synchronous) moves to program_manager with `Deprecation`, `Sunset` and `Link` headers; RCE report types require `job_id` or `intake_id` (422 `DELIVERY_IDENTIFIER_REQUIRED`); public `/health` reduced to service, version, git_sha, build_time, environment, modules; every response carries `X-Request-ID`.

## 7. UI and navigation changes

Registered deliveries: explicit "View details" link, row click, Enter and Space navigate; two-axis status columns; list state in the URL. New detail page with eight accessible tabs and honest availability states; failed-job guidance; focus to the heading; live-region announcements. New Delivery Exceptions picker page. Validation Queue reads the held-record work queue. "Data-Quality Issues" renamed "Registry Findings". Data-driven connector and coverage badges. Session-expired and permission notices with request id. Build SHA in the footer and `build-info.json` written and verified by the deploy workflow.

## 8. NPI validation results (isolated acceptance)

Three unique invalid-NPI records: `188165950` → `NPI_LENGTH_INVALID` (NPI-002), `1982916079` → `NPI_CHECKSUM_INVALID` (NPI-003, Luhn with 80840; correct check digit is 8), `A124071014` → `NPI_FORMAT_INVALID` (NPI-004). All three HIGH, all three records HELD, zero identifier rows written, ledger shows all three codes.

## 9. Reconciliation results (isolated acceptance)

| Scenario | Received | Created | Updated | Unchanged | Held | Rejected | Missing key | Excluded | Holds |
|---|---|---|---|---|---|---|---|---|---|
| A three unique | 3 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | yes |
| B existing conflict | 3 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | yes |
| C 184-record master | 184 | 180 | 0 | 3 | 1 | 0 | 0 | 0 | yes |
| D forced failure | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | no (3 unaccounted, outcome Failed) |

Scenario C outcome is derived, not assumed: Completed — With Exceptions (376 findings, 1 held record), Review state Ready for Analyst Review. Every row number is unique and present; API rows, API counts, CSV rows and CSV totals agree.

## 10. Security and authorization results

Tested in `tests/test_rbac_delivery_fields.py`, `tests/test_rbac_roles.py`, `tests/test_rbac.py`, `tests/test_health_split.py`: anonymous 401, forged token refused, expired token refused, insufficient role 403 with required and current role, viewer denied dispositions, exceptions, audit and records, reviewer allowed, program_manager required for registration and the synchronous upload, admin required for admin health; public health carries no secrets, hostnames or migration revision. Acceptance confirmed viewer evidence blocks are `requires_role:reviewer` and a viewer cannot post an identifier decision. Bandit on the changed files: 0 high, 8 medium/low-confidence B608 findings, all constant SQL fragments with bound parameters. pip-audit: two pre-existing advisories (weasyprint 69 → 70 addressed by open PR #56/#61; ecdsa transitive).

## 11. Module-gate resolution

`/api/v1/tefca` (ten mutating legacy routes) added to the tefca_arc prefixes in `app/core/modules.py`; `tests/test_module_gate_v1.py` proves a disabled profile answers 404 for GET and POST and an enabled profile is unchanged.

## 12. Accessibility results

Playwright axe (wcag2a, wcag2aa, wcag21aa, wcag22aa): 0 violations on the deliveries list and the detail page. Tabs follow the WAI-ARIA tabs pattern with arrow keys; rows have visible focus and text-plus-icon status; heading receives focus after navigation; live regions announce loading, success, failure and disposition saves. Report HTML passes the existing `validate_html` accessibility checks.

## 13. Observability implementation

`app/core/request_context.py` (context variables, X-Request-ID preservation or minting, W3C traceparent parsing, echo header, one access line per request), `app/core/logging_config.py` (JSON formatter with request, job, intake, stage, attempt, report, build fields; redaction of tokens and secrets; stack traces), pipeline errors logged with `exc_info=True`, `GIT_SHA` and `BUILD_TIME` baked into the image and reported by `/health`, migration revision on admin health. KQL, sampling, retention, redaction, cost and alert thresholds in `docs/observability/DELIVERY_OBSERVABILITY.md`. The OpenTelemetry dependency increment is documented but **not implemented** in this branch (condition 7: pinned dependency approval is separate).

## 14. Test inventory and exact results

Backend (isolated PostgreSQL rebuilt from an empty database at migration head, two sequential batches, `-m "not network"`, final run on commit c8821db): **3,528 passed, 67 skipped, 9 failed** (batch 1: 1,368 passed / 35 skipped / 1 failed; batch 2: 2,160 passed / 32 skipped / 8 failed). All 9 failures are pre-existing and reproduce identically on the unmodified main tree against the same database: `test_phase7_report_data` (6, populated QA dataset), `test_phase8_reconciliation::test_the_legacy_population_is_entirely_synthetic`, `test_ppef_jobs::test_partial_unique_index_refuses_a_second_active_job`, `test_human_review_workflow::test_government_rows_are_untouched` (fixed DEV row ids). Skips are WeasyPrint native libraries on Windows, env-gated convergence tests (run separately on a pristine cluster: 9/9 pass), sandbox-database concurrency tests and role parametrisations below viewer.

Frontend: Vitest 46/46 (10 files); static guardrails all pass (131 assertions); Playwright stubbed suite 9/9 with axe; live smoke 2/2 against the isolated backend with the real static build; `npm run build` succeeds. No ESLint gate exists in the repository (its legacy config is incompatible with the installed ESLint 10).

Security: bandit and pip-audit as in section 10.

## 15. Three-record evidence

`docs/evidence/acceptance_2026-09-16/isolated_acceptance_evidence.json` scenario A, report `A_DA-ARC-2026-001.{html,csv}`, screenshot `screenshots/09_three_record_exceptions.png`. All eleven checks pass: three received, three held, distinct codes, Completed — With Exceptions, Ready for Analyst Review, equation holds, nothing promoted, report generated with three CSV rows, report refused without an identifier (422), viewer blocks gated.

## 16. Existing-conflict evidence

Scenario B: the three master rows were seeded, then the fixture that mutates their NPIs was delivered. Each row raised both the quality finding and `NPI_EXISTING_VALUE_CONFLICT` with submitted and existing values side by side (UTMB: submitted 1982916079, existing 1982916078), records held, registry unchanged, `CONFIRM_EXISTING` accepted from a reviewer with a reason, refused without a reason (422), refused for a viewer, and the registry identical before and after. Report `B_DA-ARC-2026-002.{html,csv}`.

## 17. 184-record isolated evidence

Scenario C: 184 received, 184 dispositions, unique line numbers, equation 184 = 180 + 0 + 3 + 1 + 0 + 0 + 0, timeline with durations for 13 stages, coverage Not Run for NPPES, PECOS, LEIE and Not Configured for SAM (from evidence rows, not connector readiness), report `C_DA-ARC-2026-003` linked to snapshot `e075605d…` with generation audit, build `localgate4` and migration revision shown. Screenshots `02` to `07`. The C report artefacts are kept out of the repository (`.gitignore`) because they carry the client file's rows.

## 18. Failed-job evidence

Scenario D: curation forced to raise → outcome Failed, review state Not Ready, failed stage CURATION, error reason and remediation guidance shown, timeline shows the FAILED attempt, Area 1 records readable, correlation id present. A second job with an undelimited file fails at PARSING with no intake and still opens. Unknown job id → 404. Screenshot `08_failed_job_detail.png`.

## 19. Screenshots and artefacts

`docs/evidence/acceptance_2026-09-16/screenshots/01…09.png` (list, overview, timeline, records, exceptions, verification, reports, failed job, three-record exceptions), the HTML and CSV reports for A and B, the evidence JSON, Playwright traces under `frontend-remediation/test-results` (not committed).

## 20. Remaining DEV-only validations

1. Resolve the failed job and intake identifiers with the read-only SQL from the Gate 1 amendment.
2. Migration preflight on DEV: the migration will list the `GRANT REFERENCES` statements the owner role needs on runtime-owned tables; an operator applies them as a recorded step.
3. Reconstruction dry run of the "S" delivery with `scripts/dryrun_reconstruct_dispositions.py` (read-only), then a decision on marked backfill versus fresh registration.
4. Test Admin sign-in and the live journey on DEV (list, detail, exceptions, disposition, report).
5. PDF rendering (the Docker image carries the WeasyPrint libraries; Windows does not).
6. Application Insights or Log Analytics diagnostic settings (operator action, not executed).

## 21. Known limitations

- OpenTelemetry export not implemented (documented plan only).
- `npi_required` treats an empty hl7orgrole as not imposing the requirement (documented in the rule).
- Sync upload route remains mounted (deprecated) pending consumer analysis.
- Reports are linked by a dedicated table; `review_reports` itself is not indexed by intake.
- The 15-minute non-admin token without refresh is unchanged.
- ESLint is not runnable with the repository's legacy configuration.

## 22. Dependency and vulnerability review

Frontend dev dependencies added (exact pins): vitest 4.1.11, jsdom 30.0.1, @testing-library/react 16.3.3, @testing-library/jest-dom 7.0.1, @testing-library/user-event 14.6.7, @vitejs/plugin-react 6.1.1; lockfile updated. No backend dependency added. pip-audit: weasyprint 69.0 (PYSEC-2026-3940, fix 70.0, addressed by open PRs) and ecdsa 0.19.2 (PYSEC-2026-1325, transitive, pre-existing). bandit: no high findings.

## 23. Deployment plan (not executed)

1. Merge PR #59 or accept its cherry-picked commits with this branch.
2. Open pull requests from both `fix/delivery-workflow-remediation` branches; CI green.
3. Backend: dev-release on main with `apply_migrations=true`, `expected_current=20260915_curated_text_columns`, handshake issue #27; approve gates; add and delete the two temporary /32 rules; if preflight reports missing `REFERENCES`, apply the printed GRANTs as a recorded operator step and re-run.
4. Frontend: deploy-frontend with environment dev; verify `build-info.json` and the footer SHA.
5. Verify `/health` git_sha, admin health migration revision, `X-Request-ID`, Test Admin access, then run the three-record and 184-record journeys on DEV and generate a delivery report.

## 24. Rollback plan

Frontend: redeploy 04d7af7. Backend: PATCH `linuxFxVersion` to the recorded previous digest. Migration: additive tables are harmless to the previous image; downgrade only when no evidence rows exist. Status fields are additive. Logging format falls back with `DOCUACTION_LOG_FORMAT=plain`. Delivery history is never deleted.

## 25. Historical reconstruction dry-run result

Not run against DEV (no database access from this session). The tool `scripts/dryrun_reconstruct_dispositions.py` is read-only by default, refuses to write without `--approved-by`, `--approval-ref` and, off localhost, `--allow-shared`, classifies every proposed field as directly evidenced, deterministically recomputed, inferred or unavailable, and is unit-tested (`tests/test_dryrun_reconstruction.py`, 19 tests, plus a database test proving the dry run writes nothing). Policy: `docs/rce/HISTORICAL_RECONSTRUCTION_POLICY.md`.

## 26. Request

Gate 5 deployment approval is requested for DEV only, following the plan in section 23. No production deployment is requested.
