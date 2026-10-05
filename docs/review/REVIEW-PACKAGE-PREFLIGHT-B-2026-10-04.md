# Part B — Public-Data Verification, Grouped Cases, Shadow Proof
**Updated**: 2026-10-04 (Round 24, Fable continuation). Supersedes the Round 23
text of this file, whose audit cited the wrong module (see §2).
Local/disposable development only. No push, PR change, merge, shared
migration, deployment, firewall/account action, official finding change or
policy approval. `SEED_RULES_V4` inactive; the "1,298" interpretation
unresolved. This is a technical self-check, **not an independent review**.

## 1. Bases and local SHAs

| Repo | Branch (local, unpushed) | Base | Head |
|---|---|---|---|
| Backend | `feature/preflight-exceptions` | PR #110 head `ea92ea5c33a5075dc40b1473a64152bb52a5c3e5` (app code `264252582a63e19d14d0c715d23089fe6bab4916`) | last tested code commit `46b820e`; this document's own commit follows it (ledger Round 24 records the hash) |
| Frontend | `feature/preflight-exceptions` | PR #66 head `48031780ad4ce0b84126ae7187f49d1d1a8ee87b` | unchanged (no frontend work; §8) |

PR #110 and #66 are untouched. Part A checkpoint: `ba344360`. Round 23
checkpoint: `9e922d86`.

Commits this round, in order:

| Commit | What |
|---|---|
| `294a8a2` | No bulk supersession of exclusion / identity-conflict records |
| `101a62f` | A disappeared risk signal no longer verifies the entity |
| `9f5b17d` | Pipeline process marker is not a verified source (reports) |
| `7420748` | Exclusion name screening on the manual path; normalized candidates |
| `8e279df` | No pass from a source or reference-schema fault (LEIE, SAM, IQVIA) |
| `4b4e9b6` | Source-policy registry completed and integrated (annotation only) |
| `4878968` | Durable bounded rechecks; "verified with incomplete exclusion screening" recorded |
| `46b820e` | NPPES outcomes never reached the ledger on real evidence; corpus proof |

## 2. Actual call paths (traced, and a correction)

**Correction.** Round 23 cited `validation_engine.py` as proof of ambiguous-
identity handling. The delivery path never calls `ValidationEngine`. Its
callers are the legacy `Tefca/routes.py`, `review_engine.py` and
`qa_engine.py` golden cases. The same mistake — reading one module and
assuming the path — is what previously hid the SAM contract bug.

**Delivery path** (`delivery_runner` → review cycle → `arc_pipeline.verify_and_classify`):
`_resolve_and_gather_evidence` → `EvidenceService.build_evidence` →
`gather_sources` (`SourceConnectorManager.query_all_sources`: NPPES by NPI,
LEIE by NPI, `SAMGovConnector.verify` by UEI else legal name; plus `leie_org`
/ `sam_name` when there is no NPI; plus CMS PPEF and revocation) →
`evidence_assembly.assemble_dimensions` (D1–D6) → `source_policy.annotate_evidence`
→ `dimensions_to_verification_results` (`evidence_item_state`, worst state
wins per source) → `BucketClassifier.classify` (active rules v3) →
`verification_findings.record_from_evidence` (ledger) → `prior_risk` guards →
`ReviewRecord` + entity status.

**Manual path** (`review_service.run_review`): `probe_sources` (NPPES, PECOS
proxy, LEIE by NPI; **now** LEIE by organisation name when there is no NPI;
SAM is a permanent `NOT_CHECKED` stub) → `apply_persisted_exclusion_evidence`
(folds in the delivery path's persisted SAM/LEIE/revocation evidence, worse
state wins) → `source_policy.manual_sources_block` → `classify_with_db`.

**Coverage path** (`automated_verification.run_coverage_batch`): same
`build_evidence`, evidence rows only, no review record. First attempt only.

**Recheck path** (new, `rechecks.run_batch`): same `build_evidence`, appends
a generation, never classifies.

## 3. What is implemented, exercised, incomplete, unsupported

| Requirement | State | Evidence |
|---|---|---|
| SAM keyed on UEI/CAGE, name fallback as candidate | Implemented, exercised on the delivery path | `evidence_assembly._sam_disposition`; `test_sam_e2e_delivery_path.py` (pre-existing); corpus B02/B03 |
| Ambiguous identity never confirms or clears | Implemented, exercised | `identity_ambiguous` → REVIEW → `not_found` → not B1; corpus B03 |
| OIG screening when NPI is absent — delivery path | Implemented, exercised | `gather_sources` `leie_org`; corpus B09/B10 |
| OIG screening when NPI is absent — manual path | **Was missing; fixed** (`7420748`) | `_leie_org_name_screen`; `test_exclusion_name_screening` |
| Name variants generate candidates | **Was missing; fixed** (`7420748`) | exact string lookup missed "X, L.L.C." vs "X LLC"; `normalize_org_name` (deterministic, no threshold) |
| A name-only clean screen is not a pass | Implemented; manual-path coverage count fixed | NOT_FOUND not PASS; `sources_name_screen_only`; corpus B10 |
| Worst-state-wins, persisted vs live | Implemented, exercised | `_PERSISTED_PRECEDENCE`; `test_sam_manual_review_asymmetry.py` (pre-existing) |
| Disappearance does not clear a prior signal | **Was missing; fixed** (`101a62f`) | reproduced B4/in_review → B1/verified; `prior_risk.py`; 5 tests |
| No bulk closure of exclusion / identity findings | Routes are single-item; **one bulk channel closed** (`294a8a2`) | `publish_successors`; `risk_signals`; 4 tests |
| Technical grouping separate from compliance adjudication | Implemented (existing coverage (source, outcome) + drill-down); measured on the corpus | 21 technical records → 5 groups; risk signals remain one review record each |
| HTTP 200 error body is not an answer | NPPES existing; **LEIE and SAM fixed** (`8e279df`) | `test_reference_source_faults` (18) |
| Reference-snapshot preflight | IQVIA **added** (`8e279df`); CMS PPEF existing (`validate_schema`); OIG list **added** | `reference_preflight.py`; 19 tests |
| Source policy, official vs proposed | **Completed and integrated** (`4b4e9b6`) | §5 |
| Durable, idempotent, bounded rechecks | **Added** (`4878968`) | §6 |
| NPPES findings reach the ledger | **Was silently broken; fixed** (`46b820e`) | wrong dimension literal; corpus B11/B12 |
| Verified requires completed exclusion screening | **Recorded, NOT enforced** — policy decision | §4, §11-P1 |
| Manual path live SAM lookup | Unsupported (stub `NOT_CHECKED`); consumes persisted evidence only | named limitation |
| IQVIA affiliation journey | Unsupported; nothing fabricated | policy entry + existing route capability text |
| Recheck trigger: approved mapping change | Implemented as a refusal | no policy is approved, so it cannot fire |
| Recheck trigger: new approved snapshot | CMS PPEF completed ingest only; validation tested, a full run on real PPEF data not exercised | named limitation |
| Retention rule | `POLICY_UNAPPROVED`; nothing deleted; indefinite retention not approved | §7 |
| QA gate reads the new annotations | **Incomplete**: `qa_gate` still reads the issue ledger only | §11 |
| Frontend, QA addendum, LMS | **Not built**; hand-off written | §8 |

EIN, TIN and SSN are not used anywhere above and no finding is raised for
their absence.

## 4. False-pass risks found on the real paths

1. **Disappearing signal** — cycle 1 SAM exclusion → B4 / in_review; cycle 2
   clean → B1 / **verified**, with no human decision. Fixed.
2. **Source fault read as a clean list** — an OIG HTTP 200 error page was
   indexed as an empty exclusion list; every lookup returned "not excluded"
   for 24 hours. Fixed. SAM: a 200 error body read as "zero exclusions". Fixed.
3. **Empty reference snapshot reconciles** — an IQVIA extract with a renamed
   identity column rejected every row and then reconciled (0 == 0) as an
   approvable empty snapshot. Fixed (cannot reconcile; enforced refusal behind
   the flag).
4. **NPPES findings never recorded** — `npi_outcome_from_evidence` matched
   `"D1_IDENTITY"`; real evidence says `"IDENTITY"`. No deactivated-NPI,
   not-found or unavailable outcome reached `rce_issues` from any real run,
   so the QA gate's blocking-finding check never saw one. Fixed.
5. **Report counted a process marker as a verified source** —
   `rce_arc_pipeline: 100% verified` for every entity including B4. Fixed.
6. **Bulk supersession** — a permissive what-if rule set could supersede
   exclusion records together with technical ones (local-test mode). Fixed.
7. **OPEN — verified while exclusion screening was incomplete.** Under the
   ACTIVE v3 rules, B1 (hence `verified`) is reached with SAM unavailable,
   never checked or insufficient, and with CMS revocation unavailable
   (RULE-001 / RULE-002; only OIG is required). Reproduced in
   `test_verification_claim` and on corpus seeds B04–B06. The rules are
   approved text and were not changed. Every affected record now carries
   `verification_claim`; `ENFORCE_COMPLETE_EXCLUSION_SCREENING` (default off)
   withholds `verified`. **Decision required (§11-P1).**

## 5. Source policy — official versus proposed

`app/Tefca/source_policy.py`, registry version `2026-10-04.2`. Ten entries:
NPPES Registry API, NPPES Dissemination File (mapping pinned **V2**, offline
only — no loader exists or is added), PECOS proxy (distinguished from CMS
PPEF), CMS PPEF, CMS revocation, OIG LEIE, SAM.gov, USPS (verified permitted
scope: address validation; proves neither identity nor occupancy; the signed
agreement and access are retained), IQVIA (only the affiliation journey
unsupported), Evidence retention.

- **Official view**: every entry `POLICY_UNAPPROVED`, freshness `UNKNOWN` —
  never computed from a recent date.
- **Proposed view**: concrete candidate values, every entry
  `PROPOSED_INACTIVE`. No write route exists.
- Both views are produced against the same `as_of` / `retrieved_at` /
  `verified_at`.
- **Integration (annotation only; asserted not to change any disposition or
  bucket)**: assembled evidence, the review-record snapshot (beside, never
  inside, `classifier_input`), the manual review result, each analyst-
  workspace evidence row, and `GET /api/tefca/rce/source-policies`.
- The workspace's existing 30-day window is now labelled
  `OPERATIONAL_DEFAULT_NOT_APPROVED_POLICY`; its `REUSED_FRESH` value is kept.

## 6. Rechecks

`rechecks.py`, `recheck_models.py`, migration `20261004_recheck_jobs`
(local/disposable only). Re-evaluation, never approval: no review id, no
ReviewRecord, no classifier, no entity marked verified; a risk signal moves
the entity to `in_review`. Idempotent (unique trigger key; unique job/entity
item), bounded (2,000 entities, 200 per batch, 3 attempts), maker/checker,
stale-baseline refusal, circuit breaker when the source is still down,
reaper-based crash recovery, pinned versions. Routes refuse unless
`ENABLE_CONTROLLED_RECHECKS`; no scheduler runs one. 16 tests.

## 7. Retention

No approved retention rule was found. Recorded as `EVIDENCE_RETENTION`:
`POLICY_UNAPPROVED`; existing evidence preserved; **no automated deletion
introduced**; indefinite retention of every payload is **not** thereby
approved. Authority still required (not read in this session, so not cited):
the contract's records clause / applicable schedule, confirmed with the COR;
which payloads are records, the period per class, and who may dispose.

## 8. API versus frontend completion

- **Backend/API: complete for the items in §3 marked implemented or fixed.**
- **Frontend: nothing built.** Frontend branch is at its base.
- **QA addendum and LMS: not written.** Adam's existing workbook is untouched.
- Hand-off for a Sonnet continuation, with endpoints, the eleven screen
  changes, seventeen QA cases and the LMS outline:
  `docs/review/HANDOFF-SONNET-FRONTEND-QA-LMS-2026-10-04.md`.

## 9. Seeded corpus and combined shadow result

`tests/fixtures/seeded/manifest_b.json`: 12 pipeline seeds (one synthetic
delivery through the real pipeline, deterministic fakes for every source) and
16 component seeds, each naming an existing test.
`tests/test_seeded_corpus_b_2026_10_04.py`, at `46b820e`:

| Gate | Official view | Proposed view (inactive) |
|---|---|---|
| G1 lost seeded risk signals (exclusion, ambiguous identity, name candidate, deactivated NPI) | **0** | 0 |
| G2 false verification passes from seeded source faults | **3** (B04, B05, B06 — SAM error body / 429 / timeout → B1 RULE-002 → verified) | **0** |
| G3 outcomes that differ between views | 3, each explained by a recorded `sam_gov: UNAVAILABLE` gap | |
| G4 original delivered data | unchanged | unchanged |
| G5 phase-1 evidence and review records | unchanged | unchanged |

Marked verified: official {B01, B04, B05, B06}; proposed {B01}. Enforcing
the proposed view on the same population reproduced the shadow prediction
exactly and changed no bucket. Technical measure: 21 unavailable/insufficient
(entity, source) records fall into 5 cause groups. Recovery recheck of the 3
SAM faults: 2 answered with no signal, 1 risk signal (the exclusion hidden
behind the 429), 0 entities verified, repeat trigger returned the same job.

**G2 is not met in the official view.** That is finding §4-7, stated rather
than masked. These are corpus results, not universal accuracy.

### Part A totals, reconciled
Part A's document said "501 tests selected … 493 passed, 3 failed, 7
skipped". The selection was **503**, not 501: 493 passed + 3 failed + 7
skipped = 503, with 3,948 deselected, 4,451 collected. Command:
`pytest tests/ -k "delivery or preflight or traceability or quality or
stage_event or shadow"`, database `test_journey_1003_fresh` as
`docuaction_owner`, working tree that became `cef5226`. The 7 skips: 1
`test_delivery_delta` (no sandbox database for its concurrency test), 4
`test_rbac_delivery_fields` (nothing below viewer), 1
`test_traceability_migration` and 1 `test_traceability_migration_ownership`
(connection was not a superuser; both pass under a superuser, re-run this
round). The 3 failures were `test_automated_verification_cross_delivery_isolation`,
pre-existing — see §10.

## 10. Regression

**The single combined full-suite regression did NOT complete.** It was
started at `46b820e` against a brand-new database and was stopped by the
host tool because the machine ran critically low on memory (not a test
failure). It was not restarted. What it had done when stopped: batch 1 of 2
at 17% -- 338 tests run, 300 passed, 29 skipped, **9 failed, not identified
by name** (the summary and JUnit file are written only at the end). Eight
known pre-existing failures sort into that range (below); the ninth is
unidentified. **A complete combined regression is therefore still owed**
before this branch is treated as regression-clean.

What did run to completion this round (each bound to the commit it ran at;
database `test_journey_1003_fresh` on the disposable port-5533 instance
unless stated):

| Commit | Command (pytest ...) | Result |
|---|---|---|
| `294a8a2` | `test_no_bulk_closure`, `test_shadow_reassessment`, `test_shadow_real_v4_candidate` | 12 passed |
| `101a62f` | `test_prior_risk_not_cleared` | 5 passed |
| `101a62f` | `test_sam_e2e_delivery_path`, `test_sam_manual_review_asymmetry`, `test_sam_verification_contract`, `test_delivery_runner_events` | 42 passed |
| `9f5b17d` | `test_report_coverage_excludes_pipeline_marker`, `test_reports`, `test_review_reports` | 87 passed, 2 skipped |
| `7420748` | `-k "leie or exclusion or evidence_assembly or review_service or manual_review or probe or coverage_note or organisation or no_npi"` | 103 passed |
| `8e279df` | `test_reference_source_faults` | 18 passed |
| `8e279df` | `test_reference_preflight`, `test_iqvia_import` | 34 passed |
| `8e279df` | `test_iqvia_import_durability`, `test_iqvia_routes` | 26 passed (11 of these fail at the PR #110 head) |
| `8e279df` | `-k "sam or leie or connector or nppes or exclusion"` | 360 passed, 6 skipped, 1 failed (`test_no_nppes_bulk_loader_was_introduced`, caused by the Round 23 registry; fixed in `4b4e9b6`) |
| `4b4e9b6` | `test_source_policy`, `test_ppef_bulk_ingest_gate` | 59 passed |
| `4b4e9b6` | `-k "evidence or workspace or review_service or manual or shadow or arc or classif or sam_ or prior_risk or bulk_closure or policy"` | 637 passed, 18 skipped, 6 failed -- see note |
| `4b4e9b6` | `test_chunked_gather_correctness` (after ignoring `verified_at`) | 1 passed |
| `4878968` | `test_verification_claim`, `test_rechecks`, `test_prior_risk_not_cleared`, `test_traceability_migration` | 29 passed, 1 skipped |
| `4878968` | `test_prod_convergence_integration`, `test_prod_managed_migration_integration` on a clean throwaway cluster (port 5534) | 9 passed (17 revisions to `20261004_recheck_jobs`, one head) |
| `4878968` | `test_traceability_migration`, `..._ownership` as superuser | 2 passed |
| `46b820e` | `test_seeded_corpus_b` | 2 passed |
| `46b820e` | `-k "corpus_b or verification_finding or automated_verification or post_promotion or npi_outcome or deactivat or qa_gate or qa_approval or issue_ledger or exception_ledger"` | 134 passed, 10 failed -- see note |

**CORRECTION (Round 25, 2026-10-04).** The explanation below for the eight
coverage-test failures ("their hand-built evidence uses the dimension literal
`D1_IDENTITY`") was WRONG. The cause is that those tests never set
`ENTITY_RESOLVER_SOURCE=db`, so no reference resolved. The full regression has
since completed and every failure is classified in
`INTEGRATED-QA-READINESS-2026-10-04.md` §4, which supersedes this section.

**Note on the failures.** The port-5533 database is reused across runs and
this round's real-pipeline tests commit to it, so data-dependent tests
(`test_delivery_delta` x3, `test_qa_gate` x1, `test_job_detail_contract` x1)
fail there. To separate cause from pollution, base `ea92ea5` (a detached
worktree) and head were each run on a NEW database on the clean cluster for
`test_automated_verification`, `test_automated_verification_cross_delivery_isolation`
and `test_qa_gate`: **identical on both -- 31 passed, 1 skipped, 8 failed**
(5 + 3; `test_qa_gate` passes). Those 8 are pre-existing at the PR #110
head: their hand-built evidence uses the dimension literal `D1_IDENTITY`,
so nothing is persisted. They need a database, so CI's no-DB `pytest` job
skips them and the isolation job does not include them. **Not fixed here.**
`test_job_detail_contract::test_reviewer_gets_the_evidence_blocks` also
fails with this round's changes stashed; it was not re-run on a clean
database before the run was stopped, so it is unclassified.

Not run under any commit: the one later commit (snapshot status now returns
`reference_preflight`; documents) -- compiled only. No CI. No DEV.

## 11. Policy decisions and acceptance gaps

- **P1 (highest)**: should `verified` require every applicable exclusion
  control (SAM, CMS revocation) to have answered? Today it does not. Turning
  `ENFORCE_COMPLETE_EXCLUSION_SCREENING` on with no SAM key configured would
  withhold `verified` for every entity — so this is a decision about the
  rules and about SAM access together, not a switch to flip.
- **P2**: a name-only exclusion candidate classifies B4 (v3 RULE-005 on
  `not_found`) pending an analyst. Confirm that B4-pending is the intended
  presentation of an unconfirmed candidate.
- **P3**: RULE-002 (priority 20) precedes RULE-003 (30), so a minor address
  variance with PECOS unavailable is B1, not B2. Confirm intended.
- **P4**: approve, amend or reject each proposed source policy, including
  freshness windows and the retention authority.
- **P5**: enable preflight enforcement (deliveries and reference snapshots)
  and rechecks, and under which approval gate.
- **P6**: manual reviews now screen NPI-less entities against the OIG list by
  name — a behaviour change to confirm.
- **Gaps**: `qa_gate` does not read `prior_risk_not_cleared` /
  `verification_claim`; the ledger fix (§4-4) will begin writing NPPES
  findings on real runs for the first time, so volumes should be reviewed
  before any deployment; the 8 pre-existing coverage-test failures (§10) are
  unaddressed; **the full combined regression did not complete (§10)**; reader-level truncation of ONC deliveries (Part A) is still
  not separately seeded; nothing here has run in CI or on DEV.

## 12. Contract mapping
Source: `qa-evidence/PROJECT_CONTRACT_CONTEXT.md` (located; not restated from
memory). Task 2 (methodology and control framework; "incomplete, inconsistent
and variable-quality submissions"; discrepancy taxonomy): preflight, source
policy, honest outcomes. Tasks 3 and 4 (four discrepancy categories): a
source or schema fault is none of the four and is kept out of them. Task 5
(priority reviews: root cause, recurrence): technical groups and rechecks.
Tasks 1 and 6: unaffected. The file's five open questions are not resolved
here.

## 13. Ready for independent review?
**The backend work in this package can be reviewed as a unit**, with §4-7
and §11 as open decisions rather than defects to sign off, and with one
condition: the full combined regression was interrupted and is still owed
(§10).
Part B as a whole is **not complete**: the frontend, QA addendum and LMS
proposal are handed off, not built. Nothing should be merged, migrated or
deployed from this branch before that review and the explicit approvals.
