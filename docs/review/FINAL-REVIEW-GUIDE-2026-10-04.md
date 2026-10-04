# Final review guide — Parts A/B + Rounds 25–27 (2026-10-04, R28-5)

For an independent reviewer of `feature/preflight-exceptions`, stacked on
`feat/reporting-architecture-2026-10-01` (PR #110 backend / PR #66
frontend). **This guide is this session's own self-check, written by the
same session that made the changes. It is supporting evidence for your
review, not a substitute for it, and it approves nothing.**

## 1. Before/after behaviour, the changes that matter most

| Area | Before | After |
|---|---|---|
| Delivery-file preflight | No separate file-level check beyond quality-engine holds; no distinction between "the file has a problem" and "an organisation has a problem" | A dedicated check (schema, identifiers, missing context), default OFF (`ENABLE_PREFLIGHT_ENFORCEMENT`/the `STAGE_PREFLIGHT` stage), reporting Clear / Clear with findings / Blocked, never conflated with a discrepancy finding about an organisation |
| Source policy | No versioned record of what each external source's freshness/approval policy is | A registry distinguishing an official (in-force) policy from a proposed (inactive) one per source, read-only in the UI, every proposed policy still `PROPOSED_INACTIVE` |
| Exclusion screening gaps | A SAM.gov (or other exclusion-source) outage during verification was invisible — the entity simply read "verified" | The gap is named: "Verified — checks incomplete," the specific source named, and the SAME fact is now visible in the API, the UI badges, and the HTML/CSV/workbook report exports (not merged back into a bare "verified" anywhere) |
| A disappeared risk signal | A prior exclusion/identity-conflict signal that stopped reproducing on a later cycle silently cleared the entity | The entity stays `in_review`; the later review record carries `prior_risk_not_cleared` (signals, why not cleared, whether an independent QA approval exists) until a human adjudicates AND independent QA approves |
| Bulk actions on exclusion/identity-conflict findings | No bulk-closure control existed in the UI, but nothing had directly proven the SERVER refuses one | Two direct proofs added: the shadow-reassessment comparison-wide publication withholds exclusion/identity-conflict records explicitly; the other multi-id route in the app (bulk work-assignment) is proven to touch only `assigned_to_user_id`, never classification/resolution/reportable fields |
| Exclusion-candidate triage | The data-layer filter (`unresolved_exclusion_candidate=True`) existed with no frontend control at all | A labelled checkbox on Supervisor Operations, a per-row signal-in-words column, and the case drawer reusing the analyst's own existing uncleared-concern banner — still read-only, still no bulk action anywhere |
| Rechecks | No durable, bounded, maker/checker-controlled way to re-ask a source that had been unavailable | `rce_recheck_jobs`/`items`: request (reviewer) → approve (a DIFFERENT QA Lead) → run in bounded batches; the requester is refused approval server-side; a job where no entity resolved is correctly `FAILED`, not `SUCCEEDED` |
| Viewer rate limiting | Viewer shared the anonymous/unauthenticated traffic tier (60 req/min) — two ordinary page loads could trip it | Moved to the same tier as `contributor` (200 req/min) — still below every operational role, still enforced |
| NPPES findings reaching the ledger | A real NPPES finding on a real evidence run was not reaching the exception ledger | Fixed; proven on the seeded corpus, not merely unit-tested in isolation |
| Report-figure provenance | A pipeline PROCESS marker could be read as if it were a verified SOURCE | Separated explicitly — a process marker is never presented as source verification |

## 2. Security / RBAC changes requiring attention

- No role's floor was lowered. The Viewer rate-limit change (above) is
  the only Viewer-facing change, and it moves Viewer UP from sharing the
  anonymous tier, not down.
- `3083ced` ("fix RBAC floor registration") — review this commit
  specifically; it is a floor-registration correction for the new
  Part A/B routes, not a broadening of any existing floor. Confirm no
  route's required role changed as a side effect.
- Maker/checker is enforced server-side in three places this round
  touches: QA cannot approve their own determination (`qa_gate`,
  pre-existing, re-confirmed); a recheck requester cannot approve their
  own request (`rechecks.approve_recheck`, new); bulk work-assignment
  cannot alter compliance fields (new, directly tested).
- The exclusion-candidate queue (section 1) is deliberately read-only at
  every role that can see it (`require_role("viewer")` floor) — confirm
  no action beyond viewing was added for any role on that specific view.

## 3. Preflight enforcement and shadow defaults

- `ENABLE_PREFLIGHT_ENFORCEMENT`: default OFF. The preflight CHECK always
  runs and records findings; only blocking on them is gated by this flag.
- `ENFORCE_COMPLETE_EXCLUSION_SCREENING`: default OFF (this is policy
  decision P1 — see section 9).
- `ENABLE_CONTROLLED_RECHECKS`: default OFF.
- Shadow reassessment (`preflight_shadow_models.py`,
  `shadow_reassessment.py`): a candidate rule-set comparison runs in a
  read-only, PROPOSED view against the SAME population the official view
  already processed; nothing from a shadow run is ever written back into
  an official record. `LOCAL_TEST_MODE` publication exists only for
  local/test use, confirmed gated separately from any real publication
  path.
- None of these three flags was turned on anywhere this round except
  transiently, locally, for this session's own testing (documented in
  `docs/review/OPERATOR-PREREQUISITE-CHECKLIST-2026-10-04.md` section 5).

## 4. Public-data matching and exclusion safeguards

- No EIN, TIN or SSN is required, stored or matched anywhere in this
  work. Confirmed by direct code read, not merely by absence of a field
  in one screen.
- An entity with no NPI is screened against the OIG exclusion list BY
  NAME (normalized; punctuation/designator variants collapse, DISTINCT
  organisations do not) — this is P6 in the decision table (section 9):
  built, tested, shipped, not yet explicitly confirmed as an intended
  behaviour change by the program owner.
- A name-only match is a CANDIDATE, never an automatic finding — it
  classifies B4 pending an analyst (P2 in the decision table) and is
  never shown as "excluded" or "clear."
- The exclusion-candidate queue (section 1) names the evidence in words
  ("possible exclusion (OIG LEIE)") — never raw matching internals, never
  a confirmed-exclusion claim for an unconfirmed candidate.

## 5. Source-policy / freshness annotations

- Every source carries two policy entries side by side: official
  (in force) and proposed (inactive). Today every official entry reads
  "Unapproved," so freshness reads "Unknown (policy unapproved)"
  everywhere — this is P4 in the decision table.
- Three separate dates are tracked per piece of evidence (retrieved from
  the source; verification run; source data as of) and are never merged
  into one "date checked" value, in the API, the UI, or any export.

## 6. Recheck authorization and recovery

- Request: Analyst (reviewer) or above. Approve: QA Lead, NOT the
  requester (server-enforced, tested). Run: QA Lead, in bounded batches.
- A finished job's own outcome is reported per targeted entity
  (`STILL_UNAVAILABLE`, `ANSWERED_NO_SIGNAL`, etc.) — `ANSWERED_NO_SIGNAL`
  is explicitly documented as a re-evaluation result, never a
  verification pass; no entity is marked verified BY a recheck itself.
- Recovery/retry: a job where the entity reference could not resolve at
  all now correctly reports `FAILED` (not `SUCCEEDED` with an empty
  result) — found and fixed this round while rebuilding the Part A/B
  proof; recorded with its root cause in
  `tests/fixtures/seeded/LIVE-PARTB-RECIPE-2026-10-04.md`.

## 7. Verification completeness across APIs, reports and UI

Traced end to end, not assumed consistent across layers:
- **Pipeline/API**: `verify_and_classify`'s result carries
  `entity_marked_verified` and the full completeness block alongside the
  unchanged classification fields.
- **Registry/workspace APIs**: `_attach_completeness` adds the qualified
  label without rewriting the stored `verification_status` column.
- **Reports (HTML/PDF/CSV/workbook)**: the entity-status rollup splits
  bare "verified" into verified / verified-incomplete / verified-
  completeness-unrecorded, summing back to the same total; the per-source
  `TefcaVerification` sheet reports the raw disposition verbatim
  (`unavailable` stays `unavailable`) and was already correct before this
  work.
- **UI**: distinct badge states per completeness category, distinct
  outcome labels (`CONFIRMED`/`NOT_LISTED`/`UNAVAILABLE`/`NOT_APPLICABLE`/
  `INSUFFICIENT_EVIDENCE`/`NOT_EVALUATED`) — none of the latter five ever
  renders as a pass.
- **Where this round's own work closed a gap**: the exclusion-candidate
  queue (section 1) is the one place that, before this round, had the
  FACT available at the data layer with no UI surface at all — now
  closed, confirmed live.

## 8. Migration compatibility and rollback effects — CORRECTED 2026-10-04 (R29-1)

**This section previously claimed "Alembic head unchanged by any commit
in this stack," comparing against the wrong reference point
(`a6bf241`, an older commit, not this PR's actual base). That claim was
wrong and is withdrawn here, not merely softened.** Independent review
caught it by comparing directly against this PR's real base, `ea92ea5`
(PR #110/#66's own head) — see
[`docs/review/MIGRATION-INVENTORY-2026-10-04.md`](./MIGRATION-INVENTORY-2026-10-04.md)
for the full, corrected account; summarized here:

- Base Alembic head (`ea92ea5`): `20261003_preflight_shadow`. Candidate
  head: `20261004_recheck_jobs`. **Three new revisions**, in order:
  `20261004_preflight_exec_held` and `20261004_stage_event_preflight`
  (each widens one existing CHECK constraint — no column or table
  added/removed), and `20261004_recheck_jobs` (creates two new tables,
  `rce_recheck_job`/`rce_recheck_item`, with their own FKs, CHECK
  constraints, and a `SELECT, INSERT, UPDATE`-only grant — never
  `DELETE` — to the `DB_APP_ROLE` role).
- **Downgrade WAS exercised this round**, against a real throwaway
  database, for all three (`tests/test_recheck_and_preflight_
  migrations_2026_10_04.py`, new this round) — not merely assumed safe
  because the upgrade direction is additive. Result: all three
  downgrade cleanly while empty; `recheck_jobs` EXPLICITLY REFUSES its
  downgrade once a row exists (a named `RecheckJobsPreconditionError`);
  the two CHECK-widening migrations have NO such guard and fail with a
  raw, unguarded Postgres `CheckViolation` if dependent data exists —
  a real, demonstrated, asymmetric gap between the three, documented in
  full in the migration inventory linked above, not smoothed into one
  "additive, therefore safe" claim.
- Nothing in this stack changes `DB_APP_ROLE` enforcement, the owner/app
  role split, or any PRE-EXISTING grant — confirmed directly this round
  (privilege queries against the new tables only; no change to any
  other table's grant was made or tested, because none was touched).

## 9. Remaining policies and limitations — P1 described explicitly, as required

**P1 — should "verified" require every applicable exclusion control to
have actually answered?**
- **Stored status, unchanged**: `TefcaRegEntity.verification_status`
  still reads `verified` for an entity whose exclusion screening was
  incomplete (a source was unavailable). This column is NOT rewritten by
  anything in this work, anywhere, under any flag. Confirmed again this
  round: the seeded corpus's B01/B04/B05/B06 (the one "clean" seed plus
  the three SAM-fault seeds — four, not three, because B01 also carries
  an unavailable CMS PPEF enrolment check this corpus never fakes) still
  read `verified` in the stored column.
- **How incompleteness is represented**: as a SEPARATE, additive fact —
  `verification_completeness` / the qualified UI label
  "Verified — checks incomplete" / the split report figure — computed
  from the same underlying evidence every time it is displayed, never
  written into the stored status column itself.
- **Whether a consumer can still misinterpret it**: YES, if it reads
  ONLY the bare `verification_status` column directly (bypassing
  `_attach_completeness`, the report's own split, or the UI's badge
  logic) — such a consumer would see `verified` with no indication of
  the gap. Every consumer THIS WORK TOUCHED was checked and does not do
  this (section 7). Any consumer NOT touched by this work — a future
  report, an external export, a direct database query by someone else —
  is NOT protected by a database constraint or a view; it is protected
  only by every current code path reading the completeness function
  rather than the bare column, which is a code-review discipline, not an
  enforced invariant. This is the real, current risk P1 asks whether to
  close by rule rather than by consumer-by-consumer discipline.
- The full P1–P6 decision table — question, observed behaviour,
  recommended option, alternative, impact, authority needed, affected
  QA cases — is `docs/review/P1-P6-DECISION-TABLE-2026-10-04.md`.
  **None of the six is approved by this work. This guide does not
  approve them either.**

## 10. What this guide is, and is not

This guide, the regression manifest, the operator checklist and every
other document in this stack are this session's OWN account of its own
work. They are evidence a reviewer can check against the code — file
paths, test names, exact commands are given throughout specifically so
they CAN be checked — not a claim that review has already happened.
Independent review of the actual diff, by a person who did not write it,
is the next and only gate this stack is waiting on.
