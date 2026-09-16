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
| M-3 | Medium | backend | `verification_findings` writes NPI-005/006 under the current run; `recompute_hold_status`; reconciliation E == C | A post-promotion HIGH verification finding re-holds a promoted Area 2 row and makes reconciliation fail permanently (probe E). | No; decide before a review cycle runs on DEV | **Fixed (Decision 2, sections 3, 9, 10).** `recompute_hold_status` never rewrites a promoted record; blocking findings route through `post_promotion_verification`; sampling frame excludes `in_review` entities. | `test_post_promotion_verification.py`, `test_sampling_eligibility_review_required.py` |
| M-4 | Medium | backend | `promotion._add_missing_identifiers` NPI-only | A delivered TEFCAID/HCID/AAID for a matched entity that has none is silently ignored (probe H). | No | Partially: `apply_confirmed_submitted` now updates the entity mirror column. Family-identifier addition at promotion remains a follow-up. | — |
| M-5 / F7 | Medium/Low | backend | in-request `promote_delivery` from the two POST routes; max+1 sequences | Concurrent promotions of one intake are unguarded; duplicate-sequence race surfaced as 500. | No | Partially: `IntegrityError` → 409 with guidance on both routes; unique constraints already prevent duplicates. Advisory lock around the drain is a follow-up. | — |
| M-6 | Medium | frontend | `login/page.tsx` `?next=` | Prefix filter admitted `/\t/evil.com` (parser strips tab) → open redirect after a real sign-in. | No (DEV) / Yes (prod) | **Fixed.** Control characters refused; destination taken from the URL parser only when the resolved origin and protocol equal ours. | `login-next.test.jsx` (9 tests) |
| M1 (obs) | Medium | backend | `error_handler.generic_exception_handler` runs outside `RequestContextMiddleware` | Unhandled 500: header `X-Request-ID` missing, body id random, log id different. | No (condition) | **Fixed.** Middleware stores the accepted id on `request.state`; the handler reads it; every error response sets the header from the body id. | `test_unhandled_500_carries_the_same_request_id_in_header_body_and_log` |
| M2 (obs) | Medium | backend | `ErrorKeepingSampler`; observability doc | "5xx always kept" is false for server spans (status known only at end). | No (doc + decision) | **Doc corrected**; recommendation: `OTEL_TRACES_SAMPLER_ARG=1.0` on DEV. | existing sampler tests (documented limit) |
| M3 (obs) | Medium | backend | `telemetry.span` default `record_exception=True` | Exception events exported message and stack trace verbatim. | Before `OTEL_ENABLED=true` | **Fixed.** `record_exception=False`; redacted event with class + `safe_exception_text`; no stack trace. | `test_span_exception_event_carries_no_message_payload_or_stacktrace` |
| M4 (obs) | Medium | backend | distro `LoggingHandler` on `docuaction` logger | Second, unredacted log export path to App Insights. | Before `OTEL_ENABLED=true` | **Fixed.** `RedactingLogRecordProcessor` via `log_record_processors`. | `test_exported_log_records_are_redacted_and_carry_no_stacktrace` |
| M5 (obs) | Medium | backend | `logging_config._KV_SECRET` | Missed `client_secret=`, `access_token=`, `key: value`, JSON, Basic, URL credentials. | No (condition) | **Fixed** (all probed forms). | `test_redact_text_masks_every_probed_credential_form` |
| M6 (obs) | Medium | backend | `reports/routes.py` legacy `/html|pdf|csv|docx` at viewer vs artifact download at reviewer | Same bytes reachable by a viewer through legacy routes; reviewer floor rationale misleading. | No (decision) | **Fixed (Decision 1, sections 3, 8).** Every content route, current and legacy, raised to `reviewer` and audited on denial. | `test_report_authorization.py` |
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

## 3. Decisions — resolved by directive (2026-09-16, second round)

Both open decisions from section 3 were resolved by explicit instruction
rather than left to a recommendation, and are now implemented:

1. **Decision 1 — legacy report authorization.** Every current, legacy, alias,
   direct-artifact and regeneration route that can return a report's actual
   content is raised to `reviewer` — not just the M6 items, but a second
   legacy subsystem this directive's own audit surfaced
   (`app/Tefca/routes.py`'s deprecated `/api/tefca/reports/*` and
   `/api/tefca/priority/{case_id}/report`, which rendered the SAME content
   through a different renderer, still at `viewer`). `GET /{report_id}`
   answers with metadata and a stated `availability` reason below reviewer,
   never a partial or silently-empty body. Denials are audited
   (`require_role_audited`, a wrapper around the existing `require_role`, not
   a new authorization mechanism). Frontend controls on both consumers
   (`ReportsTab.js`, the delivery detail Reports tab; `reports/page.js`, the
   Contract Reports page, which had **no** client-side gate at all) now hide
   generation and download below reviewer with a stated reason. Full detail,
   including the exact consumer-impact list: section 8.
2. **Decision 2 — append-only post-promotion verification.** A finding
   discovered after promotion never rewrites `rce_disposition_events` or
   `record_status` again (the M-3 defect: `curation.recompute_hold_status`
   now explicitly skips any record with `canonical_entity_id` set). BLOCKING
   findings (confirmed deactivation, an invalid active identifier, a material
   identifier conflict) set the entity's `verification_status` to
   `in_review`, open exactly one analyst work item (idempotent per finding),
   exclude the entity from a QA final-classification approval
   (`qa_gate.submit_qa_review`) while unresolved, and create a new
   reconciliation snapshot — the original snapshot is never modified.
   NONBLOCKING findings (NPI not found, verification unavailable) are written
   and visible but never hold, exclude or force a work item. Full detail:
   section 9.

## 4. Corrected SHAs

Recorded in the PR descriptions at push time and in the final pre-merge
response.

## 5. Azure Blob storage RBAC — scope correction analysis (read-only; no assignment changed)

**Current assignment.** Principal `f5d178b9-287e-42d9-a528-05aa3fdea434` (the
`docuaction-dev` App Service system-assigned identity) holds **Storage Blob
Data Contributor** at the **account** scope on `stdocuactiondev4065`
(`rg-docuaction-DEV`). No other identity holds a data-plane Storage role at
or under that scope.

**What the app actually does with it.** The account has exactly one
container, `report-artifacts`, used exclusively by
`app/core/storage/artifact_store.py` (`AzureBlobArtifactStore`) through
`app/reports/data/artifact_registry.py`. Every write uses `overwrite=False`;
there is no `delete_blob`, `os.remove`, or any other delete/overwrite call
anywhere in the application against a store path (grep confirmed). Blob
names are `<report_id>-<ext>/<version>/artifact.<ext>`, unique per version
and never reused. **The application has no functional need for delete**, at
the container or the blob level.

**Storage Blob Data Contributor's actual grant** (from the role definition):
container read/write/delete, `blobServices/generateUserDelegationKey`, and
blob read/write/delete/move/add. Delete and the container-level create and
delete are entirely unused by this application.

**Protection currently in place at the storage layer** (read 2026-09-16):
versioning disabled, blob soft delete disabled, container soft delete
disabled, no immutability policy or legal hold on `report-artifacts`. That
means the account-scope role is not the only gap: **even the account owner
has no recovery path today** if a credential were ever misused to delete a
blob, independent of which identity holds which role.

**Least-privilege proposal (not applied — decision only):**
1. **Container-scope the assignment.** Storage Blob Data Contributor scoped
   to the `report-artifacts` container resource id, replacing the
   account-scope assignment. Azure supports RBAC at container granularity
   for this role, and no code change is needed since the SDK call is
   unaffected by scope. This alone removes the identity's ability to touch
   any future container added to the account.
2. **Prefer a custom role without delete**, scoped to the container: blob
   read, blob write, blob add, and container read as its only actions. This
   removes blob delete, container delete, and generate-user-delegation-key —
   none of which the code calls — while keeping everything
   `AzureBlobArtifactStore` needs (`list_blobs`, `download_blob`,
   `upload_blob` with `overwrite=False`).
3. **Independent of the RBAC question, enable storage-layer protection now**:
   blob soft delete (for example, seven days) and container soft delete on
   the account. This is the higher-value, no-regret control — it protects
   against misconfiguration or a compromised credential even under the
   current account-scope role, and needs no application change.

**Recommendation:** apply items 1 and 3 as part of the Gate 5 controlled DEV
sequence (after `merge`), before `REPORT_ARTIFACT_BACKEND=azure` is set;
item 2 (the custom role) as a fast follow so the identity's Storage grant on
DEV never includes delete. None of this is applied by this response.

## 6. 184-record delivery — exact findings breakdown (isolated database)

Intake `749f1edc-b874-4ffd-aff7-d4316ae23c09` (job `8c5f2774-aca2-4ecd-abca-510b4e45a1a0`,
run `dd242602-a497-4df4-9526-05549404c822`). 184 source records, **376
findings total**, **161 of 184 records (87.5 percent) carry at least one
finding**, 23 records are entirely clean.

| Rule | Name | Severity | Correction authority | Occurrences | Unique records | Undecided | Resolved |
|---|---|---|---|---|---|---|---|
| NPI-002 | NPI_LENGTH_INVALID | HIGH | HUMAN_REQUIRED | 1 | 1 | 1 | 0 |
| BUS-002 | TEST_RECORD_SUSPECTED | MEDIUM | HUMAN_REQUIRED | 1 | 1 | 1 | 0 |
| FMT-003 | ZIP_STATE_MISMATCH | MEDIUM | HUMAN_REQUIRED | 2 | 2 | 2 | 0 |
| INT-002 | PART_OF_UNRESOLVED | MEDIUM | HUMAN_REQUIRED | 17 | 17 | 17 | 0 |
| SCH-004 | ENCODING_ANOMALY | MEDIUM | HUMAN_REQUIRED | 1 | 0 (intake-level) | 1 | 0 |
| FMT-001 | ZIP_LEADING_ZERO_STRIPPED | LOW | AUTO_SAFE (auto-corrected) | 11 | 11 | 0 | 11 |
| BUS-003 | PARTICIPANT_PARENT_IS_QHIN | INFORMATIONAL | NO_CORRECTION | 118 | 118 | 118 | 0 |
| CON-002 | MISSING_PURPOSES_OF_USE | INFORMATIONAL | NO_CORRECTION | 32 | 32 | 32 | 0 |
| CON-003 | INACTIVE_RECORD | INFORMATIONAL | NO_CORRECTION | 17 | 17 | 17 | 0 |
| CON-005 | ADDRESS_TEXT_IS_A_LABEL | INFORMATIONAL | NO_CORRECTION | 123 | 123 | 123 | 0 |
| FMT-005 | CONTACT_PHONE_FRAGMENT | INFORMATIONAL | NO_CORRECTION | 3 | 3 | 3 | 0 |
| NPI-001 | NPI_NOT_SUPPLIED | INFORMATIONAL | NO_CORRECTION | 49 | 49 | 49 | 0 |
| SCH-002 | COLUMN_EMPTY_IN_DELIVERY | INFORMATIONAL | NO_CORRECTION | 1 | 0 (intake-level) | 1 | 0 |
| **Total** | | | | **376** | | 365 undecided + 11 resolved | |

Severity totals: HIGH 1, MEDIUM 21 (across 20 unique records), LOW 11 (11
records, auto-corrected), INFORMATIONAL 343 (150 unique records).

**Confirming the arithmetic you asked about explicitly:** 376 total − 1 HIGH −
343 INFORMATIONAL = 32 remaining — that subtraction is correct. Those 32 are
**not** all one severity: they split into **21 MEDIUM** (BUS-002 1, FMT-003 2,
INT-002 17, SCH-004 1) **and 11 LOW** (FMT-001, auto-corrected). 21 + 11 = 32.
The table above is the authoritative source; this paragraph exists only to
state the split in the terms your question used.

**Curation outcome:** CLEAN 173, CORRECTED 10 (the FMT-001 auto-corrections,
minus the one landing on the held row), HELD 1. **Disposition outcome:**
CREATED 180, MATCHED_UNCHANGED 3, HELD 1 — 184 total, matching the acceptance
evidence.

**Why only one record is held.** `blocks_promotion()` holds a record only
when an issue at CRITICAL or HIGH severity is still undecided
(OPEN, PROPOSED, or UNDER_REVIEW). Exactly one finding in this entire
184-record delivery is HIGH: NPI-002 NPI_LENGTH_INVALID, on source line 2.
Every other finding — the 21 MEDIUM, 11 LOW, and 343 INFORMATIONAL — is below
the holding threshold by rule design: MEDIUM findings such as INT-002 and
FMT-003 are HUMAN_REQUIRED and remain visible and actionable in the
exception ledger but do not block promotion; LOW FMT-001 is AUTO_SAFE and
was already corrected automatically; INFORMATIONAL findings such as the 123
CON-005 and 118 BUS-003 rows are evidentiary, NO_CORRECTION. The held row
(line 2) also carries six of these lower-severity findings, but only the
HIGH NPI-002 is why it is held — releasing it requires only a decision on
that one finding through the corrected identifier and disposition path.

**Resolved/unresolved:** every finding is OPEN except the 11 FMT-001
auto-corrections (RESOLVED by the AUTO_SAFE path at curation time). No
analyst action has been taken on this delivery in DEV yet — this is the
delivery exactly as it arrived.

**Analyst action required to close:** one decision on the line-2 NPI-002
finding, now correctly gated (it must go through a raised conflict or a
validated correction; there is no registered NPI for this OID, so the
correct path is CORRECT with a valid NPI, or REJECT / REQUEST_EVIDENCE if
the true value cannot be confirmed). Nothing else in the 376 findings blocks
promotion.

## 7. Role-account readiness (DEV, no passwords or tokens)

| Directive role | Account | Effective role | Level | Ready? |
|---|---|---|---|---|
| Test Admin | testadmin@docuaction.io | admin (test) | 8 | Usable for admin-only steps only — per instruction, admin must not substitute for a lower-privilege journey. |
| Program Manager | none at level 7 | — | — | Gap. qalead@docuaction.io is level 6 (QA Lead), one below program_manager (7). The Program Manager journey (deliverable submission, full audit log, cycle management, and the now program_manager-floored sync upload) cannot be exercised on a correctly-scoped account until one is created or an existing account is promoted. |
| Analyst (the disposition-writing role — this remediation's reviewer floor) | reviewer@docuaction.io | reviewer | 4 | Ready — matches the new reviewer floor on records, exceptions, dispositions and audit exactly. |
| Separate QA Lead (maker-checker, distinct from the analyst account) | qalead@docuaction.io | qalead | 6 | Ready — distinct account and role from reviewer@, satisfying the separation requirement. |
| Viewer / COR | viewer@docuaction.io | viewer | 1 | Ready — matches the viewer floor being tested (null evidence blocks, availability requires_role reviewer). |

**Net: 3 of 5 roles have a correctly-scoped account ready today** — Analyst
(reviewer), separate QA Lead, and Viewer/COR. Test Admin exists but is
admin-level, usable only for genuinely admin-scoped steps. **Program Manager
has no account at the correct level** — before that specific journey step,
either promote an existing account to program_manager or create a new one;
qalead@ should not be used as a silent substitute, since it would not
exercise the program_manager floor this remediation actually added (the
deprecated sync-upload path, deliverable submission, full audit log).

**Provisioning mechanism, fully determined (2026-09-16), not executed.**
Account creation and role assignment both go through the live DEV admin HTTP
API (`app/api/admin_users.py`: `POST /api/admin/users/invite`, admin-only),
not a direct database write. `program_manager` is already a first-class role
throughout the codebase (`ROLE_HIERARCHY`, `DEFAULT_MODULES_BY_ROLE`,
`scripts/verify_rbac_matrix.py`) — no code change is needed to create the
account. `scripts/reset_qa_passwords.py` only rotates passwords of accounts
already in its `ACCOUNTS` list; the correct sequence is (1) invite
`program_manager@docuaction.io` with `role: "program_manager"` via the admin
API, then (2) add that address to `ACCOUNTS` and run the reset script, which
generates a random password and writes it only to the gitignored
`docs/QA_CREDENTIALS.md` — the same safe, already-established pattern used
for every other QA account. **This was not executed**: it requires an admin
bearer token (`DOCUACTION_ADMIN_TOKEN` or an admin login), and no such
credential is available in this session. Obtaining one is an operator action,
the same category as the temporary firewall `/32` earlier in this
remediation — it needs you or another admin-token holder, not a workaround.

## 8. Decision 1 — consumer impact (requirement 10)

| Consumer | Before | After this change |
|---|---|---|
| `src/app/tefca-arc/deliveries/detail/tabs/ReportsTab.js` (delivery detail Reports tab) | Gated generation/download on `isContributor`, matching the OLD floor | Gated on `isReviewer`; download buttons replaced with a stated reason below reviewer |
| `src/app/tefca-arc/reports/page.js` (Contract Reports page) | **No client-side role gate at all** on generation or on any download button (table row actions, side-panel footer) | Generation and every download button gated on `atLeast(user.role, 'reviewer')`, with a stated reason; the client-side `generate()` call also refuses to fire below the floor as a second line of defence |
| `app/Tefca/routes.py` legacy `/api/tefca/reports/*` and `/api/tefca/priority/{case_id}/report` | `viewer` — reachable by any authenticated account, returning the same report content the current routes protect | `reviewer`, audited |
| `POST /api/reports/generate` | `contributor` (2), below reviewer (4) | `reviewer` |
| `docs/rce/DELIVERY_WORKFLOW_USER_GUIDE.md` | Silent on the report role floor | Section 18a documents the floor and the exception list |
| `tests/test_rbac_roles.py::test_no_tefca_read_endpoint_sits_above_the_viewer_floor` | Would have failed the six newly-raised legacy routes as an unreviewed regression | Extended the repository's own established `ALLOWED_ABOVE_VIEWER` exception list, with the same justification style as its existing entries |
| Nothing found reachable at a role between viewer and reviewer (`manager`, level 3) that called any of these routes | — | — |

No other consumer of any raised route was found (`grep` across both
repositories for every route path this decision touches).

## 9. Decision 2 — implementation detail

**New module** `app/tefca_registry/rce/post_promotion_verification.py`:
`record_finding` (the single entry point — writes the append-only finding,
and for a BLOCKING outcome, its consequences), `check_active_identifier_validity`,
`record_post_promotion_conflict`, `resolve_post_promotion_finding`.

**Vocabulary** (`quality_rules.NON_QUALITY_ISSUE_TYPES`): `NPI_DEACTIVATED`
(existing, authority raised from `HUMAN_REQUIRED` to `QA_REQUIRED` — safe,
since this outcome is exclusively post-promotion by construction),
`INVALID_ACTIVE_IDENTIFIER` and `MATERIAL_IDENTIFIER_CONFLICT` (new, both
`QA_REQUIRED`). `QA_REQUIRED` is reused deliberately: `curation.
transition_issue`'s existing independent-QA gate (a `qa_actor` distinct from
the resolving analyst) applies to these findings for free, with no new gate
to write or trust.

**The M-3 fix, precisely**: `curation.recompute_hold_status` now skips every
curated record with `canonical_entity_id is not None` — once promoted, that
function never touches `record_status` again, for any reason, including a
post-promotion finding sharing the same quality run. Its `still_held` count
is now computed only from records it actually still holds, not from the raw
blocking map (which can still legitimately include a promoted record's
finding for other purposes).

**Idempotency, exactly as required.** The work item is keyed on the
finding's own `issue_code` (`dq_review_bridge.open_post_promotion_case`,
under a transaction-scoped advisory lock) — deliberately NOT the
`(run, record, classification)` key `build_cases` uses for pre-promotion
work, which would have matched an already-resolved case for the same record
and silently created nothing for a genuinely new finding. A repeated
verification producing the identical outcome reuses the same issue (dedup
already existed in `verification_findings.record_npi_outcome`) and therefore
the same case — "exactly one," proven by
`test_duplicate_finding_delivery_writes_one_issue_and_one_case` and
`test_repeated_verification_after_resolution_opens_a_new_finding` (the
second one specifically proves a NEW finding after the first is resolved is
NOT swallowed by the older case-key scheme's ambiguity).

**Exclusion mechanisms, and what was deliberately not touched.** Two of the
three named exclusions are implemented and tested: `Completed — Clean`
(automatic — these findings are HIGH severity on the intake's current run,
which `delivery_jobs._open_findings`/`status_model` already count without
any code change) and final classification (`qa_gate.submit_qa_review`'s
`QA_APPROVE` now refuses when the review's entity is `in_review`). The third
— excluding an entity from a **new approved sample draw** — implemented in the
follow-up round the same day under explicit authorization; see section 10.

**Tests**: `tests/test_post_promotion_verification.py` (12 tests covering
blocking/nonblocking, duplicate delivery, repeated verification after
resolution, resolved-by-analyst with independent QA required, returned-by-QA,
external verification unavailable, historical snapshot unchanged, new
snapshot created, queue item created once, entity excluded while unresolved,
entity restored only after every blocking finding clears, and the
final-classification refusal). Migration `20260918_pp_verification` adds
four nullable audit columns to `rce_issues` (`correlation_id`, `build_sha`,
`before_state`, `after_state`) and widens `rce_reconciliation_snapshots`'s
trigger vocabulary for the two new snapshot triggers this decision needs,
with a downgrade that refuses rather than orphaning existing evidence rows.


## 10. Sample-draw eligibility (Decision 2 follow-up, authorized 2026-09-16)

**Where the rule lives.** `qhin_sampling.resolve_qhin_strata` is the one
function that decides the sampling FRAME for a delivery; it already excluded
HELD records by default and reported them as "unresolved" units with a
reason rather than filtering them away. The new rule is added in exactly that
shape: an entity whose `verification_status` is `in_review` (set only by
`post_promotion_verification.record_finding` for a BLOCKING finding, cleared
only by its resolve path after the analyst/QA workflow) is returned as an
unresolved unit with `exclusion = REVIEW_REQUIRED` and a stated reason. It is
never held, hidden, resolved, or rewritten; `record_status`,
`canonical_entity_id`, its findings, its work item and its disposition
history are untouched, and it stays in the delivery ledger, the analyst
queue, reconciliation and every report.

**What is deliberately untouched.** The engine (`sampling_engine.
CochranSampler`) is not modified: sample size, per-QHIN strata,
confidence/margin/proportion, finite-population correction, the
randomisation method and seed handling all run exactly as before on the
eligible frame (a test pins that the engine source contains none of this
change). `finalize_plan` is idempotent and never redraws: a plan drawn before
a finding keeps its membership, seed and `sample_entities` rows forever; only
a NEW plan (new parameters) sees the exclusion. Historical samples are
therefore unchanged by construction, not by a special case.

**Audit.** Every `sampling_plan_finalized` row now carries
`excluded_review_required` (count). When the count is non-zero a dedicated
`sampling_eligibility_excluded` row is written for that plan naming the
excluded entity ids, the exclusion code and the reason: the eligibility
decision as its own audit fact. `preview_plan` and the plan's
`strata_config` report the count too.

**Reconciliation correction found on the way.** The check "Every
determination traces to evidence" counted the analyst WORK ITEM the bridge
opens (a `review_records` row) as a determination without dimension
evidence, flipping the whole delivery's `passed` verdict, and therefore the
review-cycle gate, for one entity's open question. Work items are questions
by design (`classification_bucket`, `reviewer_resolution` and
`reportable_at` all NULL); both "determination" checks now exempt rows whose
`queue_source` is either bridge's. The disposition equation was never
affected (it balanced throughout; a test asserts it is identical before and
after a finding).

**No duplicate work items.** Sampling eligibility only reads; the single work
item per finding is opened once by `record_finding` and keyed on the
finding's `issue_code`. Repeated eligibility resolution and previews never
open another (tested).

**Tests**: `tests/test_sampling_eligibility_review_required.py` (9): unresolved
blocking finding excluded from a new draw (and `finalize_plan` refuses an
empty frame); nonblocking finding still eligible and drawn; resolved
blocking finding (independent QA required; same actor refused) restores
eligibility and the existing methodology decides membership; excluded entity
still visible in the exception ledger, `record_status` unchanged, and
`reconcile_delivery` still passes with an identical equation; historical
draw unchanged and never redrawn (same sample id, seed and members; a new
plan then has no eligible unit); repeated resolution/preview opens no second
work item; the eligibility decision and the plan's audit rows carry the
count, ids, code, reason and seed; the only route that draws an official
sample is `program_manager`-gated; the engine source is untouched.

**Remaining limitation.** `include_held` remains an explicit parameter for
the separate, still-open ONC question of whether HELD records belong in a
frame; this change does not answer it and does not interact with it.
