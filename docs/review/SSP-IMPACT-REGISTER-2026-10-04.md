# System Security Plan — impact register for the Part A/B changes

**Date:** 2026-10-04. **Status:** impact register and proposed amendment
text. **Not a redline, not a revised SSP, not submitted to anyone.**

## 1. Which SSP was submitted to ONC — NOT ESTABLISHED

The submitted version and its submission record could not be identified from
the files on this machine. No document was opened beyond these candidates,
and none was modified.

| # | File | Version stated inside | Dated inside | SHA-256 (first 16) | Where |
|---|---|---|---|---|---|
| 1 | `DocuAction_TEFCA_ARC_System_Security_Plan.docx` | 1.0 | 2026-07-15 | `0438502d40950a2f` | Downloads |
| 2 | `DocuAction_TEFCA_ARC_System_Security_Plan (1).docx` | 1.1 | 2026-07-15 | `f6e346358484a670` | Downloads |
| 3 | `DocuAction_TEFCA_ARC_System_Security_Plan (2).docx` | 1.2 | 2026-07-16 | `9da5aeafcb5e3e39` | Downloads |
| 4 | `SSP_v1.2.docx` | 1.2 | 2026-07-16 | `9da5aeafcb5e3e39` (identical to #3) | backend repo `docs/compliance/` |
| 5 | `TEFCA_ARC_System_Security_Plan_updated.docx` | 1.1 | 2026-07-20 | `3695fbcf4fa4ae2c` | Downloads |
| 6 | `TEFCA_ARC_System_Security_Plan_updated (1).docx` | 1.1 | 2026-07-20 | `d51a659e66632ca3` | Downloads |
| 7 | `AGT-SSP-001_System_Security_Plan.docx` | 1.0 (Document ID AGT-SSP-001) | 2026-07-28 | `7ab102913f4166ba` | backend repo `docs/compliance/` |

What is known: #3/#4 (version 1.2) carries a **Distribution** table naming
two HHS/ONC Contracting Officer Representatives, which makes it the most
likely candidate. That is an inference from the document's own distribution
list. A distribution list is not a transmittal record. Two later documents
(#5/#6 dated 20 July, and #7 dated 28 July with an unsigned approval block)
exist and say "version 1.1" and "version 1.0", so the numbering does not
order them.

**Missing, and needed before any amendment is issued:** the transmittal
itself — the email, portal upload or letter to ONC with the attached file
(or its hash) and the date. With that, the matching file above becomes the
baseline and section 4 below can be turned into a redline.

Until then this register maps impacts to the section numbers of **#4
`SSP_v1.2.docx`**, with the equivalent section of **#7 AGT-SSP-001** in
brackets, and both are marked "candidate".

## 2. What changed in the system (source of the impacts)

Local, unpublished, undeployed branch `feature/preflight-exceptions`
(backend and frontend). Nothing below is live.

- A readiness check of delivered files and of reference files (preflight),
  enforceable behind a default-off setting.
- Source outcome, classification and verification completeness reported as
  three separate facts.
- Durable, bounded rechecks with a two-person rule, behind a default-off setting.
- A versioned source-policy register (official vs proposed), read-only.
- New audit-relevant records: preflight runs and findings, recheck jobs and
  items, stage events for the readiness stage.
- User-facing pages no longer print raw structured data.

## 3. Impact register

Severity is the severity of the **documentation gap** if the change is
deployed and the SSP is not updated. "Code-supported" means tests in the
repository demonstrate the behaviour locally. "Azure / operator" means it
depends on live configuration nobody has verified in this work.

| ID | SSP area (v1.2 § / AGT-SSP-001 §) | What the SSP candidate says | What the change does | Gap | Severity | Basis | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| S-01 | System boundary and components (§2.1, §3.1 / §4.1, §5) | Application, database, storage, identity | Adds two background capabilities inside the existing API and database: readiness check, recheck jobs. Adds database tables (preflight runs/findings, recheck job/item). No new service, host or network path | Component inventory does not list them | Low | Code-supported | System owner | Open — amendment text in §4 |
| S-02 | External interconnections (§3.2, §13 / §4.3) | NPPES, SAM.gov, OIG LEIE, PECOS ("API key pending COR provision"), AI classification, SendGrid | No new external system is added by this change. But the register the change introduces lists sources the SSP candidate does not: CMS provider enrollment file (PPEF), CMS revocation list, USPS address check, IQVIA reference data (licensed, file import), and NPPES both as live lookup and as a data file. PECOS is described in the application as inferred from NPPES / CMS public files, not as a keyed API | Interconnection and third-party tables appear out of date relative to the application as built. **Pre-existing gap surfaced by this work, not created by it** | **High** | Code-supported (source list); the live connections themselves are Azure / operator | System owner | Open — needs owner confirmation of which sources are actually connected in each environment |
| S-03 | Data flows (§4.2 / §6) | Import → Verification → Classification → Human decision → Reporting | Inserts a readiness stage before quality checks when enforced; adds a recheck flow that appends new evidence and never edits old; reports split "verified" by completeness | Flow table omits the readiness stage and the recheck flow | Medium | Code-supported | System owner | Open — amendment text in §4 |
| S-04 | Data flows — outbound content (§3.2 note, §4.2) | "Government verification APIs receive individual entity identifiers" | Organisations without an NPI are now also screened by **name** against the OIG list on manual review (name sent/compared, no additional identifier). No EIN, TIN or SSN is collected or required | Statement is still true in substance; "identifiers" should read "identifiers or organisation names" | Low | Code-supported | System owner | Open |
| S-05 | Roles and segregation of duties (§5.1 / §8.1 AC) | Eight roles with levels 1–8; "QA Lead: cannot modify entity data or classification decisions"; "Analyst: cannot classify entities" | Recheck: request at reviewer, approve/run at QA Lead, requester can never approve (enforced server-side and tested). Recheck drill-down lists sit at reviewer; job-level reads at viewer | The role table in the candidate does not match the application's role ladder as built (application order: viewer, contributor, manager, reviewer, senior analyst, QA lead, program manager, admin) and does not describe maker/checker for determinations, IQVIA approval or rechecks. **Largely pre-existing** | **High** | Code-supported (role floors and maker/checker are tested) | System owner | Open — reconcile the whole table, not just the new rows |
| S-06 | Access decision raised for review (§6.1 AC) | — | Two recheck drill-down reads were registered as documented exceptions to the "no read above viewer" rule, on the same basis as the existing coverage drill-down | An access decision made by the developer, flagged for independent review | Medium | Code-supported | Independent reviewer | Open |
| S-07 | Audit events (§9.1 / §8.3 AU) | Authentication, account lifecycle, authorization, entity operations, administrative, security | New recorded events/records: readiness run and findings, readiness stage event, recheck requested / approved / run / stopped / resumed with requester and approver, pinned rule-set, layout and policy versions | "Audited events" list omits them | Medium | Code-supported for the records themselves. Whether they reach the central log store and its retention is Azure / operator | System owner | Open — amendment text in §4 |
| S-08 | Evidence handling and integrity (§4.2, §9.2) | "Each result stored with SHA-256 evidence hash" | Rechecks append a new evidence generation and never edit or delete the prior one; a later clean result does not clear an earlier concern; exclusion/identity-conflict records cannot be superseded in bulk | Strengthens the stated control; SSP should say evidence is append-only across rechecks | Low | Code-supported | System owner | Open |
| S-09 | Background jobs and recovery (§11 / §8.6 CP, §8.19 SI) | Backup and recovery of database, code, keys, documents | Recheck jobs are durable in the database, idempotent per trigger, bounded per job and per batch, stop when a source is still unavailable, and a dead worker's job is reclaimed. No scheduler runs a recheck: a batch runs only when a QA Lead asks | SSP does not describe job durability, bounded retries or the circuit breaker | Medium | Code-supported. Behaviour on an App Service restart mid-batch was tested by simulation only, not on Azure | System owner | Open |
| S-10 | Retention (§11 "Policy-defined"; AGT-DRP-007) | Database backups 35 days; documents "policy-defined" | The source-policy register carries an evidence-retention entry whose official status is **unapproved**. No automatic deletion was introduced and no indefinite retention is claimed | Retention authority for verification evidence is not on file | **High** (governance, not code) | Policy gap | System owner / COR | Open — needs a decision, not a code change |
| S-11 | Source-policy governance (no matching section) | Not described | Read-only register: official entries are all unapproved (freshness "unknown"); proposed entries are inactive. No route can approve a policy; approval requires a recorded person and time | New governance mechanism not described in the SSP | Medium | Code-supported | System owner | Open — amendment text in §4 |
| S-12 | Feature settings / configuration management (§2.3 / §8.5 CM) | — | Three default-off settings: readiness enforcement, rechecks, complete-exclusion-screening (the last is a proposed policy and must stay off) | Configuration baseline should list them and who may change them | Medium | Code-supported for the defaults. The value in each live environment is Azure / operator | Operator | Open |
| S-13 | File security and failure handling (§4.2 stage 1, §12.1 / §8.19 SI) | "CSV file uploaded via HTTPS. Schema validated." | Readiness check blocks a structurally faulty delivered file when enforced; a faulty reference file (IQVIA) cannot be approved and says why; a refused OIG list never replaces the last good list; a source answering with an error body, a rate limit or a timeout is "unavailable", never a pass | SSP understates the fail-closed behaviour now present | Low | Code-supported | System owner | Open |
| S-14 | Information shown to users (§8.3 headers / §6.5 SI) | — | Pages render structured details as labelled text instead of raw structured data; the technical form is available only through an explicit copy action | No SSP change required; noted for completeness | Low | Code-supported; checked in a local browser | — | No action |
| S-15 | Compliance statements (§14 Accessibility, §15.5 FedRAMP) | Accessibility and FedRAMP sections exist | This work makes **no** FedRAMP-readiness, full-control-coverage or Section 508 conformance claim. New panels were checked with automated role/label tests and one browser run only | Ensure nothing in an amendment cites this work as evidence of conformance | Medium | Statement of limits | System owner | Open |
| S-16 | AI classification (§3.2, §4.2 stage 3) | "Entity context sent to AI service. Classification recommendation returned." | The delivery path traced in this work classifies with the rule-based bucket classifier; no AI call was observed on that path | Possible pre-existing mismatch. **Not verified** across all paths in this work | Medium | Unverified | System owner | Open — needs a dedicated check |

## 4. Proposed amendment text (draft, to apply only once the baseline is confirmed)

Written against the section numbers of candidate #4 (`SSP_v1.2.docx`).

**Revision History — new row (draft).** "1.3 | [date of issue] | [author] |
Adds delivery and reference-file readiness checks, verification
completeness reporting, controlled rechecks with segregation of duties,
and the source-policy register. Updates external interconnections, roles
and audited events."

**§2.1 Architecture Layers — add.** "Processing includes a readiness check
of each delivered file and each imported reference file before
classification. When enforcement is enabled, a structurally faulty file
stops at this stage. Controlled rechecks run as durable, bounded jobs in
the application database; no recheck runs unless an authorised user starts
a batch."

**§3.2 External Interconnections — replace the table only after the owner
confirms the connected sources per environment.** Rows to confirm: NPPES
(live lookup); NPPES data file; CMS provider enrollment file; CMS
revocation list; OIG LEIE; SAM.gov; USPS; IQVIA reference data (licensed
file import, no outbound call).

**§4.2 Entity Review Data Flow — add rows.** "1a. Readiness — delivered
file checked for layout and identifier shape; findings recorded with
applicability, execution, evidence and disposition; blocked files do not
proceed when enforcement is enabled." "2a. Recheck — on request and
independent approval, a source is queried again for entities it did not
answer for; new evidence is appended; prior evidence is not altered; no
entity is marked verified by a recheck."

**§5.1 Application Roles — add after the table.** "Segregation of duties:
the user who records a determination cannot approve it; the user who
registers a reference snapshot cannot approve it; the user who requests a
recheck cannot approve it. These rules are enforced by the server."

**§9.1 Audited Events — add row.** "Verification operations | Readiness
run and findings; readiness stage outcome; recheck requested, approved,
run, stopped and resumed, with requester, approver and the pinned rule-set,
layout and policy versions."

**New §9.3 Source policy register (draft).** "The application holds a
read-only register of source policies. Each source has an official entry
and a proposed entry. No policy is approved at the time of writing; while
unapproved, evidence freshness is reported as unknown. A policy can be
approved only by a recorded person and time; the application provides no
function to approve one."

**§11 — add.** "Retention of verification evidence is governed by
[policy to be approved]. Until it is approved, no automatic deletion of
verification evidence is performed."

## 5. Change log for this register

| Date | Change |
|---|---|
| 2026-10-04 | First issue. Baseline not established; 16 impacts recorded; draft amendment text prepared against candidate v1.2. |

## 6. What this register does not do

It does not modify any SSP file. It does not establish which SSP was
submitted. It does not verify any live Azure setting, network rule, log
retention, backup or key configuration. It does not claim FedRAMP
readiness, complete control coverage or Section 508 conformance. It was
written by the same session that made the code changes and is not an
independent assessment.

## 7. Round 26 update (2026-10-04) — additional impacts since first issue

The SSP baseline question from §1 is UNCHANGED and remains unresolved: no
transmittal record was located this round either. Nothing below selects a
candidate by inference; it is appended against the same unresolved baseline.

| ID | SSP area (candidate v1.2 § / AGT-SSP-001 §) | What changed | Evidence | Severity | Owner | Status |
|---|---|---|---|---|---|---|
| S-17 | Roles / rate limiting (§7 Authentication and Identity Management, "Login Protection" / §8.1 AC, §8.18 SC) | The Viewer role was found sharing its request-rate tier with unauthenticated/unknown traffic (60 req/min, 10-burst), reproduced in a real browser as a false rate-limit failure on two ordinary page loads. Moved to the same tier as "contributor" (200 req/min, 30-burst) — still enforced, still below every operational (staff) role's tier. | `app/core/rate_limiter.py`; `tests/test_rate_limit_roles_and_preflight.py::test_viewer_is_authenticated_not_anonymous_and_is_not_free_tier` (unit-tested; browser re-confirmation not yet done this round) | Low | System owner | Open — the SSP candidate does not describe per-role rate-limit tiers at all; this is a gap the whole feature surfaces, not only this fix |
| S-18 | Audit / exclusion screening (new; no clear existing section) | A read-only, individually-listed queue for cases carrying an unresolved exclusion/identity-conflict signal now exists at the data layer (`work_queue(unresolved_exclusion_candidate=True)`), proven incapable of any bulk action. A direct test also proves the one other multi-id route in the application (bulk work assignment) cannot alter a case's classification, resolution or reportable status. | `app/tefca_registry/supervisor_ops.py`; `tests/test_supervisor_operations.py`; `tests/test_no_bulk_closure_2026_10_04.py::test_bulk_assignment_is_the_only_other_multi_id_route_and_it_only_assigns` | Low | System owner | Open — strengthens, does not change, the segregation-of-duties and no-bulk-closure statements already in §6 of this register |

No FedRAMP, full-control-coverage, Section 508 or live-Azure-configuration
claim is added by this update. These two items are local, unpushed,
undeployed code and test changes only.

## 8. Round 27 update (2026-10-04) — one additional impact; baseline still unresolved

The SSP baseline question from §1 is UNCHANGED and remains unresolved
this round too: no transmittal record was located. The precise evidence
still needed, restated rather than left implicit: a transmittal record
(an email, a COR acknowledgement, or a portal submission receipt) naming
WHICH of the seven candidate files (six distinct) was the one actually
submitted to ONC, and when. Absent that record, this register continues
to carry its impacts against candidate v1.2 as the most-likely (not
confirmed) baseline, exactly as §1 states. No baseline is selected by
inference this round either, and no claim is made that this SSP update
is complete — it is an impact register against an unconfirmed candidate,
not a finished amendment.

| ID | SSP area (candidate v1.2 § / AGT-SSP-001 §) | What changed | Evidence | Severity | Owner | Status |
|---|---|---|---|---|---|---|
| S-19 | Audit / exclusion screening (same area as S-18, Round 26) | S-18 (Round 26) described a backend-only, data-layer queue with no frontend control — "a backend queue without a usable UI is not complete," per this round's own directive. That frontend control now exists: a clearly-labelled checkbox on Supervisor Operations, a per-row signal-in-words column, and the case drawer reusing the analyst's own existing "earlier concern has not been cleared" banner. No new approval action, no bulk control, no new role capability — this closes the UI gap S-18 already flagged; it does not change the segregation-of-duties or no-bulk-closure facts already recorded in §6/S-18. | `src/app/tefca-arc/operations/page.js`; `src/app/tefca-arc/components/VerificationCompleteness.js` (export only, no behaviour change); `tests/e2e/live-exclusion-ui-round27.spec.mjs` (live browser confirmation) | Low | System owner | Open — same disposition as S-18: strengthens, does not change, §6's existing statements |

**No new entry for the Part A/B seeding-script reconstruction (R27-3):**
that work added test fixtures and a new Playwright spec only — no
application code changed, so it carries no SSP impact of its own. It is
recorded instead in `docs/review/REGRESSION-MANIFEST-2026-10-04.md` and
the backend's own `tests/fixtures/seeded/LIVE-PARTB-RECIPE-2026-10-04.md`.

No FedRAMP, full-control-coverage, Section 508 or live-Azure-configuration
claim is added by this update. This round's only application change is
local, unpushed, undeployed code and test changes.
