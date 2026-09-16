# Pre-merge independent review and corrections (2026-09-16, UTC)

Frozen SHAs reviewed: backend `1dbd014dd56bc58ce11988216e41b535c5a6d453`
(PR #65), frontend `473e7733150229a837f94e9653eda8fa5e654698` (PR #46).
Three reviewer agents that did not author the change performed a strictly
read-only first pass (no edits, no git state changes); probes ran only inside
rolled-back transactions on the isolated PostgreSQL 18 cluster. Findings were
then corrected on the same branches in one commit per repository (SHAs in the
PR description and in section 4).

Severity is the reviewer's; "Blocks" is the reviewer's DEV-merge verdict.
"Status" is what this commit did.

## 1. Findings matrix

| ID | Sev | Repo | Where | Finding (abbreviated) | Blocks | Status | Test |
|---|---|---|---|---|---|---|---|
| F1 / H-1 | High | backend | `identifier_decisions.decide`, `apply_confirmed_submitted`, `POST /identifier-decisions`, `curation.apply_disposition` | A decision needed no raised conflict; `identifier_type` and `selected_value` were free text; CONFIRM_SUBMITTED wrote an unvalidated (checksum-invalid or arbitrary) value as the ACTIVE NPI and superseded the registered one; no uniqueness check (500 on collision). Reproduced by two reviewers independently (probes A, A2). | Yes | **Fixed.** `decide` requires the latest event to be CONFLICT_RAISED (`NoOpenConflict`), restricts `identifier_type` to npi/tefcaid/hcid/aaid, refuses a third value on CONFIRM_SUBMITTED, requires a value on CORRECTED, validates every value a registry-writing decision would write (`IdentifierValueRefused`, NPI through the shared Luhn validator), refuses a value active on another entity (`IdentifierAlreadyRegistered` → 409), keeps the entity mirror column in step. Routes map the gate to 422/409 and `IntegrityError` to 409. Frontend no longer posts `selected_value` for CONFIRM_*. | `test_premerge_review_corrections.py` (7 tests), `exceptions-tab.test.jsx` |
| F2 | High (deploy condition) | backend | migration `_table_exists` guard; `main.py` startup `create_all`; workflow default | An evidence table created by a runtime `create_all` would be owned by the runtime role; the migration would silently adopt it and its grants would be void; no view would exist. | Yes as deploy condition | **Fixed + confirmed not reachable on DEV.** Migration refuses foreign-owned tables/view (`TraceabilityOwnershipError`, nothing applied); startup `create_all` excludes the five tables (`schema_guard.create_all_except_migration_owned`). DEV app setting `STARTUP_SCHEMA_MUTATION_ENABLED=false` (read 2026-09-16), so the runtime path was already closed there. Deploy sequence unchanged: migration first, image second. | `test_traceability_migration_ownership.py`, `test_startup_create_all_excludes_the_migration_owned_tables` |
| F3 | Medium | backend | `stage_events.close_stage`, runner `detail[*].error`, `job.error_reason`; served at viewer floor | Raw exception text (asyncpg `DETAIL: Key (...)=(value)`, SQL, parameters) persisted into evidence readable by a viewer. | No | **Fixed.** `logging_config.safe_exception_text`: domain exceptions keep a redacted message; driver/library exceptions keep the class name plus correlation id pointer. Used by `stage_events.safe_failure_text`, `_reason`, every runner error site and span exception events. | `test_stage_event_failure_reason_never_carries_sql_or_parameters`, `test_stage_events.py` |
| M-1 | Medium | backend | `curation.apply_correction` (unconditional CORRECTED); legacy `PATCH /issues` + `POST /promote` | A record with a second undecided HIGH finding was released and then promoted (probe D). | Yes unless legacy path gated | **Fixed.** `apply_correction` derives HELD/CORRECTED from `_blocking_by_record`; `promote_delivery` independently holds any record whose current run has an undecided HIGH/CRITICAL finding. | `test_apply_correction_keeps_the_record_held_while_another_high_finding_is_open` |
| M-2 | Medium | backend | `curation.apply_disposition` CORRECT on a conflict; `promotion._identifier_conflicts` | CORRECT never reached the registry and re-promotion re-raised the conflict forever (probe C). | No | **Fixed.** CORRECTED now writes the registry through the same validated path as CONFIRM_SUBMITTED; the decided-value skip also honours `selected_value`. | `test_corrected_writes_the_registry_and_does_not_reraise_the_conflict` |
| M-3 | Medium | backend | `verification_findings` writes NPI-005/006 under the current run; `recompute_hold_status`; reconciliation E == C | A post-promotion HIGH verification finding re-holds a promoted Area 2 row and makes reconciliation fail permanently (probe E). | No; decide before a review cycle runs on DEV | **Open decision (section 3).** | — |
| M-4 | Medium | backend | `promotion._add_missing_identifiers` NPI-only | A delivered TEFCAID/HCID/AAID for a matched entity that has none is silently ignored (probe H). | No | Partially: `apply_confirmed_submitted` now updates the entity mirror column. Family-identifier addition at promotion remains a follow-up. | — |
| M-5 / F7 | Medium/Low | backend | in-request `promote_delivery` from the two POST routes; max+1 sequences | Concurrent promotions of one intake are unguarded; duplicate-sequence race surfaced as 500. | No | Partially: `IntegrityError` → 409 with guidance on both routes; unique constraints already prevent duplicates. Advisory lock around the drain is a follow-up. | — |
| M-6 | Medium | frontend | `login/page.tsx` `?next=` | Prefix filter admitted `/\t/evil.com` (parser strips tab) → open redirect after a real sign-in. | No (DEV) / Yes (prod) | **Fixed.** Control characters refused; destination taken from the URL parser only when the resolved origin and protocol equal ours. | `login-next.test.jsx` (9 tests) |
| M1 (obs) | Medium | backend | `error_handler.generic_exception_handler` runs outside `RequestContextMiddleware` | Unhandled 500: header `X-Request-ID` missing, body id random, log id different. | No (condition) | **Fixed.** Middleware stores the accepted id on `request.state`; the handler reads it; every error response sets the header from the body id. | `test_unhandled_500_carries_the_same_request_id_in_header_body_and_log` |
| M2 (obs) | Medium | backend | `ErrorKeepingSampler`; observability doc | "5xx always kept" is false for server spans (status known only at end). | No (doc + decision) | **Doc corrected**; recommendation: `OTEL_TRACES_SAMPLER_ARG=1.0` on DEV. | existing sampler tests (documented limit) |
| M3 (obs) | Medium | backend | `telemetry.span` default `record_exception=True` | Exception events exported message and stack trace verbatim. | Before `OTEL_ENABLED=true` | **Fixed.** `record_exception=False`; redacted event with class + `safe_exception_text`; no stack trace. | `test_span_exception_event_carries_no_message_payload_or_stacktrace` |
| M4 (obs) | Medium | backend | distro `LoggingHandler` on `docuaction` logger | Second, unredacted log export path to App Insights. | Before `OTEL_ENABLED=true` | **Fixed.** `RedactingLogRecordProcessor` via `log_record_processors`. | `test_exported_log_records_are_redacted_and_carry_no_stacktrace` |
| M5 (obs) | Medium | backend | `logging_config._KV_SECRET` | Missed `client_secret=`, `access_token=`, `key: value`, JSON, Basic, URL credentials. | No (condition) | **Fixed** (all probed forms). | `test_redact_text_masks_every_probed_credential_form` |
| M6 (obs) | Medium | backend | `reports/routes.py` legacy `/html|pdf|csv|docx` at viewer vs artifact download at reviewer | Same bytes reachable by a viewer through legacy routes; reviewer floor rationale misleading. | No (decision) | **Open decision (section 3).** | — |
| F4 / L1 | Low | backend | `exception_ledger.dispositions_csv`, `csv_engine` | CSV formula injection via delivered names / reviewer text. | No | **Fixed.** `csv_engine.neutralise_row` on every data row (dispositions CSV, delivery processing CSV, SOW CSV). | `test_csv_cells_that_look_like_formulas_are_neutralised` |
| F5 | Low | backend | dispositions CSV response | No `Cache-Control: no-store, private`; AST guard scanned one file. | No | **Fixed.** `download_headers()` used; guard extended to `delivery_routes.py`. | `test_every_download_response_in_the_router_uses_the_helper` |
| F6 / L-6 | Low | tests | `seed_entity` vs acceptance rows | 13 tests failed on the shared isolated DB (unique index spans every status). | No | **Fixed.** Fixture removes colliding rows inside the rolled-back transaction. | `test_identifier_conflict.py` green |
| L2 (obs) | Low | backend | `delivery_report_artifacts` errors | Store exception text (local path / blob URL) returned to a contributor. | No | **Fixed** (class only). | — |
| L3 (obs) | Low | backend | `artifact_download` `version` | Unbounded int → asyncpg int32 error → 500 without audit. | No | **Fixed** (`ge=1, le=2_147_483_647`). | — |
| L-2 | Low | backend | `status_model` wording | "no findings" while only HIGH/CRITICAL are counted. | No | **Fixed** (wording; criterion key unchanged for the frontend). | — |
| L-4 | Low | backend | `reconciliation` rule lists | Unordered select embedded in the hashed detail. | No | **Fixed** (`order_by(rule_id)`). | — |
| L-5 | Low | backend | `npi_validator` | Accepted non-ASCII digits. | No | **Fixed.** | `test_npi_validator_rejects_non_ascii_digits` |
| L-9 | Low | frontend | `Tabs.js` tabpanel | Inline `outline: none` suppressed the focus ring. | No | **Fixed** (global `:focus-visible` rule applies). | — |
| L-12 | Low | frontend | `lib/api.js` | Server prose and body `request_id` unbounded. | No | **Fixed** (`capMessage`, `safeRequestId`). | — |
| L-8 | Low | frontend | `tests/e2e/stub-api.mjs` | Stub origin hardcoded. | No | **Fixed** (follows env). Deploy workflow still does not run the suites (follow-up). | — |
| F8, F9, F10, F11, L-3, L-7, L-10, L-11, L4–L7 (obs) | Low/Info | both | see reviewer reports | Offline downgrade script, audit ILIKE scan, LIKE metacharacters, nullable unique, tautological check, decided-skip trace, workflow heredoc quoting, dead buttons, `_MS.sampleRate`, SDK import guard | No | Follow-ups; none affects the acceptance scenarios. | — |

Verified-correct rows (Info) from the three reviews are kept in the reviewer
reports attached to the PR conversation; they cover: grants exactly as
contracted and refused for the runtime role; downgrade refusal with evidence;
REFERENCES precheck; view semantics; every route with a `require_role` floor
and field-level nulling for viewers; module gate coverage; health split without
secrets; no SQL injection surface; id resolution and failed-job handling;
artifact keys/locators not caller-controlled; no delete or overwrite path;
hash re-verification on download; snapshot-pinned regeneration; honest
`durable`; Jinja autoescape; identifier-only span attributes; excluded health
URLs; Luhn/80840 correctness; rule ordering; HELD rows never drained; conflicts
never silent; no double counting; READY only on a passed reconciliation; Tabs
and DataTable accessibility; `?job=` allow-listing; no `dangerouslySetInnerHTML`.

## 2. Test results after correction

See the PR description for the exact counts at the corrected SHAs (full backend
suite in two batches on the isolated cluster; frontend unit, guardrails, e2e
with axe, build, audit).

## 3. Decisions requested before merge

1. **M6 (report download floor).** Options: (a) raise the legacy per-format
   routes and artifact history to `reviewer` so viewers see report metadata
   but no report bytes (consistent with "viewer sees no delivered values";
   the frontend already shows a permission notice on 403); (b) lower the new
   artifact download to `viewer` and rewrite the rationale. Recommendation:
   (a), as a small follow-up commit before DEV journeys, since it changes what
   the viewer account can do today.
2. **M-3 (post-promotion verification holds).** Options: (a) verification
   findings do not change Area 2 `record_status`; they surface through the
   exception ledger and the `no_failed_required_verification` criterion;
   (b) they hold, and the equation grows a "held after promotion" population.
   Recommendation: (a). Not needed for the DEV acceptance journeys, which do
   not run a verification cycle.

## 4. Corrected SHAs

Recorded in the PR descriptions at push time and in the final pre-merge
response.
