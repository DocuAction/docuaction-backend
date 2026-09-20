# QA ledger 2026-09-19 — defect-to-code matrix and remediation record

Source of truth: `DocuAction_50_Record_QA_Defect_Ledger.md` (61 confirmed defects, 5 to validate),
delivery job `31fba38d-bb9a-4240-bc3b-4744c4f7618e` on DEV.

Evidence baseline at the start of remediation (2026-09-20):

| Item | Value |
|---|---|
| Backend main / DEV live | `37db323` (DEV `/health.git_sha`; DEV Release run 35402318563) |
| Frontend main | `3955223` (#47, #48, #49 merged 2026-09-18) |
| Frontend DEV live | `80de8b6` (deploy run 35170597962, 2026-09-17) — **#47/#48 not on DEV when the ledger was taken** |
| Backend PRs #75 / #76 | merged 2026-09-18 (report scope + ReviewCycle persistence); reused, not duplicated |
| Frontend PR #47 / #48 | merged (assignee crash; delivery-scoped verification report); reused |
| PR #109 | does not exist in either repository |
| Migrations | head `20260918_pp_verification`; **no new migration in this work** |
| DEV settings | `ENABLE_SCHEDULER=false`, `ENABLE_AUTOMATED_VERIFICATION_COVERAGE` unset (OFF), `ENABLE_QA_MONITOR` unset (OFF), `REPORT_ARTIFACT_BACKEND=local` |

Branches: backend `fix/qa-ledger-2026-09-19`, frontend `fix/qa-ledger-2026-09-19`.

Status vocabulary: **FIXED** (code + regression test on this branch), **FIXED-PARTIAL** (control fixed, a
stated residual remains), **CONFIG** (DEV setting, operator action), **BLOCKED** (needs DEV data/session),
**NOT-REPRODUCED**, **DECISION** (governance choice, not code), **DEFERRED** (P2 cosmetic, documented).

## Root-cause groups

| Group | Root cause | Defects |
|---|---|---|
| RC-1 | Report identity not fail-closed: `next_report_id` restarted at `001` on error; `store_report` swallowed the UNIQUE violation; links written for a colliding id; downloads authorised by report id only; registry-wide sections in a scoped report; no provenance header | QA-033, 034, 035, 036 |
| RC-2 | Synchronous long operations with a 30 s client abort read as failure; no idempotency; stale notice | QA-031, 032, 048 |
| RC-3 | Cross-delivery rollups: open items counted any case for an entity the delivery contains; QHIN population was the *sampling frame*, not placement | QA-039, 049, 051, V03 |
| RC-4 | Identity and provenance not surfaced (UUIDs, no delivery context, timeline without time) | QA-040, 042, 043, 052, 061, 027 |
| RC-5 | Audit payload shape: server sent `at` and nested type/resource/correlation; UI read other keys | QA-023, 024, 025, 026, 028, 029, 030, V04 |
| RC-6 | Naive UTC datetimes serialised without offset | QA-010 |
| RC-7 | Decision controls: preselected CONFIRM / APPROVE, no-op reclassify, QA form shown to the maker | QA-055, 057, 058 |
| RC-8 | Stale-response race in filtered lists | QA-009 |
| RC-9 | Presentation | QA-001..008, 011..016, 018, 020..022, 037, 038, 041, 044..047, 050, 053, 060, V05 |
| RC-10 | QA monitor probe hit a protected endpoint unauthenticated; monitor flag off | QA-059 |

## Matrix

| ID | Status | Root cause / evidence | Files | Test |
|---|---|---|---|---|
| QA-001 | DEFERRED | Record drawer prints lineage JSON (`RecordsTab.RecordLineagePanel`). Structured lineage exists on the Lineage tab; the Lineage row action now goes there (QA-022). | — | — |
| QA-002 | FIXED | `Mono` wrapped anywhere | `frontend: deliveries/detail/lib.js` (`Mono nowrap`), `RecordsTab.js` | guardrails/build |
| QA-003 | FIXED | same | `RecordsTab.js` (nowrap + title) | — |
| QA-004 | DEFERRED | `SidePanel` modes exist (480 / 840 / 1600 px); content layout inside full-screen not restructured | — | — |
| QA-005 | FIXED-PARTIAL | Copy control added for correlation ids and detail JSON on Audit History; UUID wrapping unchanged elsewhere | `AuditTab.js` | `qa-ledger-review-surfaces.test.jsx` |
| QA-006 | DEFERRED | 17-column table, `minWidth = cols × 140`; horizontal scroll present | — | — |
| QA-007 | FIXED | issue/rule codes `Mono nowrap` | `ExceptionsTab.js` | — |
| QA-008 | DEFERRED | description clipped in table; full text in drawer | — | — |
| QA-009 | FIXED | no request-sequence guard; slower earlier response overwrote the filtered one | `ExceptionsTab.js` (`requestSeq`) | `qa-ledger-review-surfaces.test.jsx` |
| QA-010 | FIXED | `rce_issues.created_at` naive UTC serialised without offset → browser parsed as local | `exception_ledger._iso`, `delivery_routes._iso` | `test_identity_provenance_and_controls.py` |
| QA-011 | FIXED | API already returns `source_rce_id`; drawer showed only the UUID | `ExceptionsTab.js` | — |
| QA-012 | FIXED-PARTIAL | `field_name` now shown as "Affected field"; submitted/existing/normalized already shown; "expected rule" text not added | `ExceptionsTab.js` | — |
| QA-013 | DEFERRED | rule text in `quality_rules.py:661` | — | — |
| QA-014 | DEFERRED | exception assignment is case-level (`/reviews/{id}/assign`, now exposed in Supervisor drawer QA-053); no per-exception control | — | — |
| QA-015 | DEFERRED | `tefca-registry/entity/page.js` has no delivery lineage; the workspace section A carries it | — | — |
| QA-016 | DEFERRED | hand-written `'—'` on the registry entity page | — | — |
| QA-017 | CONFIG + DESIGN | Coverage pass gated by `ENABLE_AUTOMATED_VERIFICATION_COVERAGE` (default OFF, unset on DEV); delivery runner deliberately does not run it (documented policy decision, PR #75). Manual trigger `POST …/verification-coverage/run` (program_manager) exists. **Operator decision: set the flag on ONE DEV instance.** | — | — |
| QA-018 | DEFERRED | `SecurityBadgeBar.pecosBackingNote` + `VerificationTab` repeat the backing note | — | — |
| QA-019 | DEFERRED (explained) | Verification tab mixes per-delivery coverage states with the global `/api/tefca/status` connector probe (`SecurityBadgeBar`); layout-level bar on other pages is global by design and says so | — | — |
| QA-020 | FIXED | `changed_fields` is structurally empty on `CREATED_NEW_ENTITY` (`promotion.py:490-503`) | `RecordsTab.changedFieldsText`, `LineageTab.js` | `qa-ledger-review-surfaces.test.jsx` |
| QA-021 | DEFERRED | see QA-004 | — | — |
| QA-022 | FIXED | `DataTable` row action fell back to `onRowClick` | `DataTable.js` (`onRowAction`), `RecordsTab.js` | — |
| QA-023 | FIXED | server field is `at`; UI read `timestamp/created_at/occurred_at` | `AuditTab.js`; `delivery_routes.delivery_audit_route` | both suites |
| QA-024 | FIXED | type/resource nested in `detail` | `delivery_routes` (`event_type`, `resource_type`, `resource_id` first-class) | `test_identity_provenance_and_controls.py` |
| QA-025 | FIXED | correlation nested in `detail` | same + copy control | same |
| QA-026 | FIXED | raw JSON inline | `AuditTab.js` (`summaryText`, expandable detail, copy) | `qa-ledger-review-surfaces.test.jsx` |
| QA-027 | FIXED | stage actor is `worker_id()` (`host:pid`) | `identity.actor_facts`; audit route `actor_class`, `executing_service`, `human_initiator` | same |
| QA-028 | FIXED-PARTIAL | two naming styles; display `label` added (`_audit_label`); stored vocabulary unchanged | `delivery_routes` | same |
| QA-029 | FIXED | route returned the intake's *latest* job as `job_id` | `identifiers {requested_job_id, latest_job_id}`; UI states a mismatch | same |
| QA-030 | FIXED-PARTIAL | no search/filter/sort/export | `AuditTab.js` (client-side over the server-capped 1000 entries; CSV/JSON export scoped to the delivery) | same |
| QA-031 | FIXED | synchronous generation vs 30 s client abort; no idempotency | `routes.generate` (`idempotency_key` replay), `ReportsTab.js` (120 s, poll, key reuse) | `test_report_delivery_isolation_p0.py`, `reports-tab.test.jsx` |
| QA-032 | FIXED | notice never cleared on refresh | `ReportsTab.js` | `reports-tab.test.jsx` |
| QA-033 | FIXED | job-detail reports carried artifact UUIDs only | `delivery_routes._reports` (format, filename, size, sha256, status) | `test_report_delivery_isolation_p0.py` |
| QA-034 | FIXED (P0) | see RC-1 | `report_snapshot.py`, `generator.py`, `delivery_report_links.py`, `reports/routes.py`, `delivery_routes.py`, `ReportsTab.js` | `test_report_delivery_isolation_p0.py` (12) |
| QA-035 | FIXED | entity-status / coverage / QHIN sections registry-wide when cycle-scoped; no reconciliation | `report_data_service.py`, `report_reconciliation.py` (fail closed `REPORT_SCOPE_UNRECONCILED`) | same |
| QA-036 | FIXED | header omitted job/intake/snapshot/build/source hash | `csv_engine.py`, `base.html`, `report_reconciliation.delivery_provenance` | same |
| QA-037 | FIXED-PARTIAL | queue had no delivery column; server filter `intake_id` exists but no UI selector | `operations/page.js` (Delivery column; provenance in drawer) — **delivery filter selector deferred** | — |
| QA-038 | DECISION | `deadline_status` (NO_DEADLINE/ON_TRACK/DUE_SOON/PAST_DUE) exists; thresholds have no approved values (module note); needs contract SLA values | — | — |
| QA-039 | FIXED | `_review_counts` OR-clause counted any case for the delivery's entities | `delivery_routes._review_counts`, `delivery_jobs._review_counts`, `status_model.review_state` (labelled breakdown) | `test_delivery_rollups_isolation.py` |
| QA-040 | FIXED | UUIDs only | `identity.py`; `supervisor_ops` (principal), `operations/page.js` | `test_identity_provenance_and_controls.py` |
| QA-041 | DEFERRED | `/api/tefca/registry/findings` has no delivery filter; page is entity-scoped by design; demo text keyed on `data_source=MOCK` | — | — |
| QA-042 | FIXED | DTO lacked delivery/entity | `case_assignment._dto`, `my-reviews/page.js` | `test_identity_provenance_and_controls.py` |
| QA-043 | FIXED | cycle cases carry no `case_classification` payload key | `_dto` (`final_classification`, rule, version, resolution) | same |
| QA-044 | FIXED | two-branch label | `my-reviews/page.js` (`actionLabel`) | `qa-ledger-review-surfaces.test.jsx` |
| QA-045 | DEFERRED | `record_status=CLEAN` is the parse/curation state; workspace prints the raw enum in section B | — | — |
| QA-046 | NOT-REPRODUCED | `workspace/page.js:734-743` renders `determination.rationale`; needs the DEV payload for `REV-2026-000247` | — | — |
| QA-047 | FIXED | warning not drillable | `qhin_workload.qhin_rollup` (`unresolved.items`), `assignment/page.js` | `test_delivery_rollups_isolation.py` |
| QA-048 | FIXED | see RC-2; server side already idempotent (advisory lock, sample reuse) | `OverviewTab.js` | — (server idempotency covered by `test_review_cycle_report_scope_isolation.py`) |
| QA-049 | FIXED | rollup used the sampling frame as population | `qhin_workload.placement_units` | `test_delivery_rollups_isolation.py` |
| QA-050 | FIXED | free-text ids; no directory | `workflow_routes.analyst_directory`, `assignment/page.js` (picker, gating) | `test_identity_provenance_and_controls.py` |
| QA-051 | FIXED | same as QA-049 | same | same |
| QA-052 | FIXED | items lacked delivery/sample/source; timeline lacked time | `supervisor_ops._work_items`, `audit_timeline`; `operations/page.js` | same |
| QA-053 | FIXED | no assign control | `operations/page.js` (`AssignControl` → existing `/reviews/{id}/assign`) | — (route covered by `test_case_assignment.py`) |
| QA-054 | BLOCKED (reason now visible) | D5 = `evidence_assembly._dimension_tefca` facets; the rationale names the unresolved facet and is now rendered in section C. Which facet failed for `DocuAction QA Subparticipant 044` needs the DEV record. | `workspace/page.js` | — |
| QA-055 | FIXED | `useState('CONFIRM')` | `CaseActions.js` | `qa-ledger-review-surfaces.test.jsx` |
| QA-056 | DECISION | new determination outcomes (insufficient evidence, request evidence, defer) are a methodology/vocabulary change; not implemented | — | — |
| QA-057 | FIXED | static bucket list incl. current | `qa_gate.record_analyst_determination` (refuses no-op), `CaseActions.js` (targets, labels) | both suites |
| QA-058 | FIXED | QA form gated on role only | `workflow_routes.qa_eligibility`, `CaseActions.js` (read-only awaiting state) | both suites |
| QA-059 | FIXED-PARTIAL + CONFIG | `check_api_endpoints` probed viewer-gated `/api/tefca/dashboard/summary` → now `/api/tefca/status`; `ENABLE_QA_MONITOR` unset on DEV (operator: set on ONE instance) | `qa_engine.py` | `test_identity_provenance_and_controls.py` |
| QA-060 | DEFERRED | sampling panel omits `sample_id`/status (data present in `board.sampling.plans`) | — | — |
| QA-061 | FIXED | holder UUID; undated duplicate-looking events | `supervisor_ops.audit_timeline` (event id, kind, UTC time, role, correlation), `operations/page.js` | `test_identity_provenance_and_controls.py` |
| QA-V01 | CONFIG | see QA-017 | — | — |
| QA-V02 | BLOCKED | needs DEV entity/relationship rows | — | — |
| QA-V03 | FIXED (explained) | 55 = cases for entities across other queues; now only cases created against the delivery, with a breakdown by queue source in the review-state sentence | see QA-039 | same |
| QA-V04 | CONFIRMED (by design) | `request_context.correlation_id()` falls back to `job_id` for non-request work; audit entries now label `correlation_id` separately from `resource_id` | — | — |
| QA-V05 | FIXED-PARTIAL | `SidePanel` has Escape, focus trap, restore; now labelled by its heading (`aria-labelledby`); Expanded-mode screenshot/keyboard walk still needs a DEV session | `SidePanel.js` | — |

Totals: FIXED 39 · FIXED-PARTIAL 7 · CONFIG/DESIGN 3 · DECISION 2 · BLOCKED 2 · NOT-REPRODUCED 1 · DEFERRED 12 (66 items).

## Adjacent defects found and handled

- `next_report_id` counted rows instead of taking the maximum: after any delete or failed insert the next id could collide (fixed with the P0).
- `StatusBadge` in Supervisor Operations received `status=` instead of `state=` — every "Why" badge rendered indeterminate (fixed).
- `supervisor_ops.audit_timeline` loaded the whole `tefca_reg_audit_log` table into memory (now filtered in SQL).
- Isolated test cluster must run with `timezone = 'UTC'` (Azure default) for naive `now()` columns to agree with `utcnow()` writers.

## Operator actions (not code)

1. Set `ENABLE_AUTOMATED_VERIFICATION_COVERAGE=true` on exactly one DEV instance, or run
   `POST /api/tefca/rce/deliveries/{intake}/verification-coverage/run` as a program manager (QA-017/V01).
2. Set `ENABLE_QA_MONITOR=true` on exactly one DEV instance (QA-059).
3. Deploy frontend main to DEV (frontend PRs #47/#48 are merged but DEV still serves `80de8b6`).

## Final evidence SHAs (added 2026-09-20, checkpoint review)

- Frontend PR #50 head `a3db3bc8fe9cd59f1d42df9a857869000e48da52` on base `395522381967f12446573d30c7253388e6341b8f`; CI audit + e2e pass (analyze / dependency-review skipped by the entitlement gate, as on main).
- Backend PR #81: code through `e49f7e28ea88850f45ca2a1032cdd42241b5254e` on base `37db323423409de95f407ee3d1ea97242de7b985`; CI CodeQL, analyze, dependency-review, fixture, pytest, render, sast pass. The commit adding this section also adds the `isolation-postgres` CI job so the P0 database-backed suites run against PostgreSQL in CI instead of being skipped; the PR head after that commit is the final backend SHA.
- Migrations introduced by either PR: **none** (`git diff --name-only 37db323..HEAD -- alembic/` is empty; head remains `20260918_pp_verification`).
- Worker / scheduler impact: none of `delivery_scheduler.py`, `delivery_runner.py`, `automated_verification.py`, `stage_events.py`, `main.py` changed; the backend image serves API and scheduler together, so one backend deployment covers both.
- Local full backend suite (isolated PostgreSQL 18, UTC): 3,731 passed, 11 failed, 71 skipped; 10 failures reproduce identically on unmodified `37db323` (9 × `test_automated_verification*.py`, 1 × `test_promotion_one_pass.py`), 1 fixed on this branch. Acceptance harness: scenario A 11/11 checks (3 received, 3 held, distinct codes, equation holds, no invalid identifier promoted); scenario C 12/12 checks (184 rows, 184 unique lines, 183 CREATED + 1 HELD, API/CSV agree, report linked).
