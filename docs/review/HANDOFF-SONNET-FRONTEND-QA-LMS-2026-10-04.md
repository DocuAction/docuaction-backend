# Hand-off: frontend wiring, QA addendum, LMS proposal
**Prepared**: 2026-10-04 (Round 24). **For a separate Sonnet continuation.**
Nothing in this file has been built. The backend it describes exists on
local branch `feature/preflight-exceptions` (backend) and is not pushed.

Boundaries that still apply to that continuation: local commits only on the
frontend `feature/preflight-exceptions` branch (base `48031780`); no push,
PR change, merge, deploy, account or policy action; shadow default; nothing
described here is live on DEV, so every QA case below is **Blocked until a
build containing this branch is deployed** and must say so.

## 1. Backend surfaces now available (read these, do not re-derive)

| Need on screen | Endpoint / field | Role floor |
|---|---|---|
| Source policies, official vs proposed | `GET /api/tefca/rce/source-policies` -> `sources[].official` / `.proposed_inactive`, `any_policy_approved` | viewer |
| Delivery preflight result | `GET /api/tefca/rce/deliveries/{intake_id}/preflight`; findings `GET /api/tefca/rce/preflight-runs/{run_id}/findings?category=&disposition=` | reviewer |
| Whether preflight was enforced for a job | job timeline: a `PREFLIGHT` stage event exists only when enforcement was on; `status` COMPLETED / SKIPPED (held) / FAILED (blocked), `failure_reason` | existing |
| Reference-snapshot preflight (IQVIA) | existing snapshot status route now returns `reference_preflight` (`gate`, `enforced`, `findings[]`, `held_checks[]`, `bound`) and `reconciliation.refusal_reasons[]` | reviewer |
| Per-evidence policy + dates | `GET /api/tefca/rce/deliveries/{intake_id}/workspace` -> `entities[].evidence.items[].source_policy` (`official.approval_status`, `official.freshness`, `proposed_inactive.freshness`, `evidence.as_of/retrieved_at/verified_at`), `evidence.freshness_window_basis` | reviewer |
| Held / unresolved on a review | review record `verification_results.verification_claim` (`exclusion_screening_incomplete[]`, `official`, `proposed_inactive`), `.prior_risk_not_cleared` (`prior_review_id`, `signals[]`, `why_not_cleared`, `open_blocking_finding`), `.source_policy` | reviewer |
| Technical groups + drill-down | existing verification coverage per (source, outcome) and its drill-down/CSV (`VerificationTab`) -- one group per (source, outcome), affected count, entity list | existing |
| Rechecks | `POST /deliveries/{intake_id}/rechecks`, `POST /rechecks/{id}/approve`, `POST /rechecks/{id}/run-batch`, `POST /rechecks/{id}/resume`, `GET /rechecks/{id}`, `GET /rechecks/{id}/items`, `GET /rechecks/{id}/items.csv` (all under `/api/tefca/rce`; 409 unless `ENABLE_CONTROLLED_RECHECKS`) | request: reviewer; approve/run/resume: qalead; read: viewer/reviewer |
| Manual review, NPI-less entity | review response `verification.oig_leie` now carries `matched_by: "organisation_name"`, `potential_hit`, `disposition`, `reason`; `confidence.sources_name_screen_only` | existing |

## 2. Frontend work (prefer existing screens; nothing new unless stated)

| # | Screen (file) | Change | Notes |
|---|---|---|---|
| F1 | `deliveries/detail/tabs/OverviewTab.js` | "Source readiness" card: preflight gate (Clear / Clear with findings / Blocked / Not run), enforced vs shadow, link to findings | "Not run" must not read as clear |
| F2 | `deliveries/detail/tabs/TimelineTab.js` | Render the `PREFLIGHT` stage; SKIPPED shows "Held: open preflight findings" with `failure_reason` | stage only exists when enforced |
| F3 | `deliveries/detail/tabs/ExceptionsTab.js` | Preflight findings list with the four dimensions as four columns (applicability, execution, disposition, evidence) -- never one status | reuse existing table |
| F4 | `deliveries/detail/tabs/VerificationTab.js` | Per source: Verified, Not found, Unavailable, Not checked as SEPARATE counts with denominators; add "Screened by name only" and "Not applicable" as their own columns; show `pipeline_runs` separately, never as a source | "verified" excludes unavailable/unsupported/not evaluated |
| F5 | `VerificationTab.js` (same) | Technical groups: (source, outcome) rows with affected count -> existing drill-down; a "Request recheck" action on Unavailable groups (reviewer) | action hidden when flag off (409) |
| F6 | `workspace/page.js` + `TefcaReviewWorkspace.js` | Evidence rows: show Retrieved / Verified / As-of dates and a policy chip: "Policy unapproved - freshness unknown" (official) with the proposed value in a tooltip labelled "Proposed, inactive" | keep existing REUSED_FRESH label but caption it "operational reuse window" |
| F7 | `workspace/CaseActions.js` / review detail | Banner when `prior_risk_not_cleared` or `verification_claim` present: plain-language reason, link to the prior review, list of incomplete controls | no approve shortcut from the banner |
| F8 | `reviews/page.js`, `my-reviews/page.js` | Unresolved exclusion candidates listed INDIVIDUALLY (one row each: source, match basis, candidate vs confirmed); no multi-select, no bulk action on these rows | bulk closure is refused server-side too |
| F9 | `iqvia/page.js` | Show `reference_preflight` gate, findings, held checks and `refusal_reasons`; disable Approve with the reason when reconciliation refuses | affiliation journey stays "Unsupported" |
| F10 | new small page or a section of an admin/settings screen | Read-only source-policy table: Official (Unapproved) vs Proposed (Inactive) per source, mapping/schema version, effective date | no edit control exists or should exist |
| F11 | recheck panel (modal from F5) | Request (rationale + trigger reference), status, pinned versions, items drill-down + CSV; Approve/Run/Resume for qalead only; requester sees "awaiting a different approver" | show `is_compliance_approval: false` wording: "Re-evaluation only" |

Each needs unit tests in the existing style; keep accessibility (labels,
keyboard focus) consistent with the workspace.

## 3. QA addendum (separate from Adam's existing workbook -- do not edit it)

Create `DocuAction_QA_Addendum_Preflight_PartB_2026-10-04.xlsx` (or `.md`)
with these cases. Every case: Blocked until deployed; role; fixture; exact
page and control; expected result; Pass / Fail / Blocked criteria; evidence
location. Fixtures are the synthetic files in `tests/fixtures/seeded/`.

| ID | Role | Page -> control | Fixture / input | Expected |
|---|---|---|---|---|
| PB-01 | Analyst | Deliveries -> Upload | `seed-02-missing-identity-field.psv`, enforcement ON | Job fails at Preflight; Overview shows "Blocked"; no Quality/Curation stage |
| PB-02 | Analyst | Deliveries -> Upload | `seed-03-duplicate-header.psv`, enforcement ON | Blocked, finding PF-SCH-008 naming the duplicated column |
| PB-03 | Analyst | Deliveries -> Upload | `seed-01-clean.psv`, enforcement OFF | No Preflight stage in Timeline; delivery processes as today |
| PB-04 | Analyst | Delivery -> Exceptions | a delivery with a bad id shape | One finding row with four separate dimension columns |
| PB-05 | Analyst | IQVIA -> Stage | extract with `hco_hce_id` (renamed) | Reference preflight Blocked (REF-SCH-004); Approve disabled with reason |
| PB-06 | Analyst | IQVIA -> Stage | extract without `ORG_NPI` | Imports; "NPI matching held" shown; not blocked |
| PB-07 | Analyst | Verification tab | delivery verified during a SAM outage | SAM shows Unavailable count, NOT verified; denominators visible |
| PB-08 | Analyst | Review detail | entity classified during SAM outage | Banner "exclusion screening incomplete: SAM.gov"; official vs proposed stated |
| PB-09 | Analyst | Verification -> Unavailable group -> Request recheck | rationale + incident reference | Job "Pending approval"; no lookups performed |
| PB-10 | Analyst | same job -> Approve | -- | Refused: a different person must approve |
| PB-11 | QA Lead | same job -> Approve -> Run batch | SAM restored | Items show prior Unavailable -> new answer; nobody becomes Verified |
| PB-12 | QA Lead | same trigger requested again | -- | Same job returned; no duplicate job, no duplicate evidence |
| PB-13 | Analyst | Manual review of an NPI-less entity | name matches a list entry (synthetic) | OIG shows "candidate - analyst determination required", never "excluded" or "clear" |
| PB-14 | Analyst | Reviews list | several exclusion candidates | Individually listed; no bulk-close control |
| PB-15 | Viewer | Source policies | -- | Every source "Unapproved"; proposed values labelled "Inactive"; no edit control |
| PB-16 | Analyst | Workspace evidence row | any | Three dates shown separately; freshness "unknown (policy unapproved)" |
| PB-17 | Analyst | Keyboard only | PB-08 screen | Banner and links reachable and announced |

## 4. LMS proposal (draft only -- mark every item PROPOSED, not published)

Draft against the screens actually implemented in section 2, after they
exist. Suggested module outline:
1. "A technical problem is not a compliance finding" -- preflight, the four
   dimensions, blocked vs held.
2. "What Verified does and does not mean" -- verified vs unavailable /
   unsupported / not evaluated / not applicable; name-only screens.
3. "Exclusion candidates" -- potential hit vs confirmed; why each is decided
   individually; why a clean later run does not clear an earlier signal.
4. "Rechecks" -- re-evaluation, not approval; two-person rule.
5. "Source policy" -- unapproved vs proposed; three dates.
Do not state that any policy is approved, that enforcement is on, or that
anything is deployed.

## 5. Contract mapping to carry into the QA/LMS text
Source: `qa-evidence/PROJECT_CONTRACT_CONTEXT.md` (do not restate from memory).
Task 2 (methodology: discrepancy taxonomy, handling of incomplete /
variable-quality submissions) -> sections 1-2, 5 of the LMS outline and
PB-01..08. Tasks 3/4 (four discrepancy categories) -> a technical/source
fault is none of the four and must not be reported as one. Task 5 (priority
reviews: root cause, recurrence) -> technical groups and rechecks. Tasks 1
and 6: no change.
