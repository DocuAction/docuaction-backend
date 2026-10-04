# Part B — Public-Data Verification, Grouped Cases, Shadow Proof
**Prepared**: 2026-10-04 (Round 23, Part B) — local/disposable development
only; no push, PR, merge, migration dispatch, deployment, or official
finding/policy change made under this task.

> **This checkpoint is NOT a complete Part B, and says so explicitly in
> §9.** It is an honest audit of what already exists for sections 1/3/4
> (with real citations), one concrete new piece of infrastructure for
> section 2, and an explicit list of what section 5's corpus extension
> and section 6 (frontend + LMS) still need — not attempted this round.
> Preserving unfinished work as labeled WIP, per instruction, rather than
> presenting a thin pass over six large areas as finished.

## 1. Exact backend/frontend bases and local SHAs

| Repo | Branch | Base (Part A) | This round's local commits |
|---|---|---|---|
| Backend (PR #110, untouched) | `feature/preflight-exceptions` | `ba34436` (Part A checkpoint) | `6b1862c` (source-policy registry) |
| Frontend (PR #66, untouched) | `feature/preflight-exceptions` | `4803178` (= PR #66 head `48031780`'s branch tip) | none this round |

Neither PR #110 nor #66 was modified or pushed. `feature/preflight-exceptions`
remains local-only in both repos, confirmed via `git branch -vv` (no
upstream tracking).

## 2. Public-data matching (task §1) — AUDITED, substantially pre-existing

EIN/TIN/SSN are correctly never required, inferred, or fabricated anywhere
found in this audit. Concrete, cited evidence:

- **SAM.gov is matched on UEI/CAGE, never NPI** — `connectors.py`
  (`SAMGovConnector`, line ~809): "SAM is keyed on UEI/CAGE, never NPI."
  `lookup_by_npi` for SAM explicitly fails closed with a stated reason
  rather than guessing (line ~1082-1085).
- **Ambiguous name matches never confirm or clear** — `connectors.py`
  (~line 915): "Returns `ambiguous: True` when SAM matches more than one
  entity." `validation_engine.py` (~line 339-346, ~403-420): an ambiguous
  match is explicitly treated the same as source-unavailable — "fail
  closed, route to a human, never auto-classify" — and is excluded from
  the confidence-score calculation as a confirmed result either way.
  This is the literal mechanism the task's §1 instruction asks for
  ("may never alone confirm, clear, auto-close, or produce a
  verification pass") — already built, not new work this round.
- **Missing NPI does not remove a record from name screening** —
  `evidence_service.py` (~line 268-315, docstring "ORGANISATION-LEVEL
  SCREENING WHEN NO NPI EXISTS"): LEIE and SAM are additionally queried
  by organisation name under `leie_org`/`sam_name` keys when no NPI
  exists. The comment documents a REAL prior defect found and fixed
  (a positional-argument `TypeError` silently swallowed by
  `_safe_lookup`, which meant "the org-level check silently never ran
  and D3 fell through to INSUFFICIENT_EVIDENCE for every NPI-less
  entity — a check that appeared wired up and was not"). `evidence_assembly.py`
  (~line 440-530) then assembles this into `NOT_FOUND` (not `PASS`) for a
  name-only clean result — explicitly weaker evidence than an NPI match,
  documented as such ("NOT_FOUND rather than PASS... must not read as an
  equivalent clearance").

**No new public-data-matching code was written this round** — the audit
found the specific defect classes this task's §1 warns against (fabricated
negatives from an unscreened NPI-less entity, ambiguous matches read as
clearances) already identified and fixed in prior rounds, with the fix's
own reasoning preserved in comments rather than silently applied. This
section's work this round was verification, not construction.

## 3. Honest outcomes and versioned source policy (task §2) — ONE NEW PIECE

**What already existed**: per-connector `API_VERSION` constants; per-
rule-set `RULE_SET_VERSION`/`FIELD_MAP_VERSION`; a real
`source_version_snapshots` DB table (`scripts/phase6_population_enrichment.py`,
`register_source_versions`) tracking `version_label`/`source_as_of`/
`source_file_hash` per ingested source. None of this is a POLICY in the
sense this task means: nothing records who approved relying on a given
schema/mapping/identity-method/freshness-window for a live classification
decision.

**What this round built**: `app/Tefca/source_policy.py` — a versioned
registry with exactly the two views the task specifies:

- `OFFICIAL_POLICIES`: every entry (`NPPES_BULK`, `PECOS_PROXY`, `USPS`,
  `IQVIA`) is `POLICY_UNAPPROVED` today, grounded in
  `qa-evidence/PROJECT_CONTRACT_CONTEXT.md`'s own statement that no COR
  acceptance has occurred. `official_view()` returns
  `freshness=FRESHNESS_UNKNOWN` **unconditionally** for an unapproved
  source — proven NOT to be computable from a recent `as_of` date
  (`test_a_very_recent_as_of_date_does_not_make_an_unapproved_source_current`),
  which is the exact trap the task names ("do not treat source cadence
  alone as an approved freshness deadline").
- `PROPOSED_POLICIES`: concrete candidate values, each `PROPOSED_INACTIVE`.
  NPPES bulk mapping pinned to `"V2"` per the task's own instruction
  (`test_nppes_bulk_proposed_mapping_is_pinned_to_v2`). PECOS formalized
  as `PROXY_NOT_PECOS`, citing the already-existing
  `connectors.PECOS_BACKING` constant rather than restating the fact
  independently. USPS carries an explicit `proves_identity=False`/
  `proves_occupancy=False` pair plus a permitted-scope statement tied to
  the existing signed agreement (not a new credential claim). IQVIA
  names the affiliation-journey gap specifically
  (`IQVIA_AFFILIATION_JOURNEY_UNSUPPORTED`) while leaving the delivered
  HCO/HCP facts themselves `mapping_version="delivered-facts-only"` —
  never calling the whole source unsupported.
- `both_views()` produces official and proposed against **identical**
  pinned evidence (`as_of`/`retrieved_at`/`verified_at`, tracked
  separately, never collapsed) — proven directly
  (`test_proposed_and_official_use_identical_pinned_evidence`).

**17 tests, all passing, no DB/network required.** **Not wired into any
live decision path this round** — `validation_engine.py`/`connectors.py`
are unchanged; this is additive reporting infrastructure the existing,
already-fail-closed connectors do not currently need in order to be safe
(see §2 above). Wiring it into an actual UI/report surface is named in §9
as remaining work, not attempted this round.

## 4. Exclusion-specific safeguards (task §3) — AUDITED, substantially pre-existing

- **Independent screening, individually adjudicated**: `SAMGovConnector`'s
  registration (v3) and exclusion (v4) checks are documented as "two
  independent legs" (`connectors.py` ~line 1501) — a registration success
  does not imply the debarment question was ever answered
  (`validation_engine.py` ~line 347-354, `sam_exclusion_unknown`).
- **Unresolved identity is reported as a limitation, not a clearance**:
  `evidence_assembly.py`'s `INSUFFICIENT_EVIDENCE` disposition (~line
  453, 515-524) is explicit: "the check could not be performed and is
  NOT reported clean."
- **No bulk closure / no inherited disposition for exclusion findings**:
  `review_service.py` (~line 215-247, `PERSISTED_EXCLUSION_DIMENSION`,
  `_PERSISTED_PRECEDENCE`) documents and fixes the EXACT failure mode
  this task's §3 warns against: "a confirmed SAM exclusion persisted at
  B4 by the bulk path was followed by a NEWER manual ReviewRecord at B1"
  — i.e., a bulk-classified exclusion finding being silently overridden
  by a later, less-informed path. The fix makes the WORSE state always
  win (`excluded` ranks 0, the lowest/worst in `_PERSISTED_PRECEDENCE`,
  so it can never be outranked by a later `clear`) — proven in
  `tests/test_sam_manual_review_asymmetry.py` (pre-existing, re-read not
  re-written this round).
- **Reinstatement requires actual evidence, not silence**:
  `connectors.py`'s `OIGLEIEConnector._reinstated` (~line 694-696) reads
  an explicit `reinstatement_date` field from the LEIE record itself —
  there is no code path anywhere in this audit that clears an exclusion
  because it simply stopped appearing in a later snapshot.

**No specific gap was found and fixed in this section this round** — the
audit's purpose here was to confirm the task's safeguards against the
actual code, with citations, rather than assume compliance. One item
explicitly NOT verified this round: the task's "archive snapshots under
approved retention rules... document the retention decision needed" —
no retention-policy code or document was found or written; named as an
open item in §9, not fabricated as POLICY_UNAPPROVED in `source_policy.py`
only because retention is a storage/ops decision, not a verification-
source policy, and belongs in a different module than the one built
this round.

## 5. Grouped technical causes and controlled rechecks (task §4) — AUDITED

`app/tefca_registry/rce/dq_review_bridge.py` already implements:
- A root-cause-like **group key** (`case_key`, combining rule ids/issue
  types/severity per record — ~line 213-267) distinct from a raw
  per-issue list.
- **Idempotent grouping**: `_existing_case(db, group["case_key"])` is
  checked before a new case is created (~line 313-314) — a second run
  over the same findings does not duplicate a case.
- Append-only audit (`reg_audit_record`, ~line 444) and a derived
  priority/severity rollup per group.

`exception_ledger.py` / `scripts/exception_inventory.py` already provide
the drill-down and CSV export the task asks for, with the three
populations (processing failures / verification outcomes / review cases)
kept explicitly separate and never summed (`exception_inventory.py`'s own
module docstring).

**Not verified this round, named as a gap**: a "controlled recheck after
source recovery or approved mapping/rule change" TRIGGER — grouping and
idempotent case creation clearly exist, but this audit did not find (and
did not have time to search exhaustively for) a specific, bounded
recheck-trigger mechanism distinct from simply re-running the whole
pipeline. This is named honestly rather than asserted either way.

## 6. Integrated proof (task §5) — PARTIAL

- Part A's 8-seed manifest (`tests/fixtures/seeded/`) remains the proven
  corpus for SCHEMA/IDENTIFIER/MISSING_CONTEXT preflight scenarios —
  unchanged, not re-litigated.
- This round's 17 `source_policy` tests are a parallel, real proof set
  for the Freshness/approval-status dimension specifically (stale vs.
  unknown freshness; official-never-current-from-cadence-alone).
- **NOT extended this round**: the task's explicit additional scenario
  list — HTTP 200-with-error-body/429/timeout/outage (already covered at
  the unit level by `tests/test_sam_failure_diagnosis.py`, pre-existing,
  but not re-expressed as seeded-manifest entries); ambiguous identity
  with conflicting corroboration; seeded exclusions and deactivated
  NPIs; missing IQVIA affiliation data as a manifest seed; forbidden
  bulk compliance closure as a manifest seed; recheck retry/idempotency;
  maker/checker refusal as a manifest seed. None of these were built as
  new seeded-manifest entries this round — doing so honestly, at the
  depth the task's "hard gates" demand (zero loss of seeded signals,
  zero false passes, every changed outcome explained), is a substantial
  further body of work, not attempted here rather than rushed.
- Shadow mode remains default; `SEED_RULES_V4` remains inactive; no
  production delivery or shared database was touched.

## 7. Frontend and QA/LMS handoff (task §6) — NOT ATTEMPTED

No frontend code was read or written this round. No QA cases, no LMS
draft. This is named directly rather than implied by omission: **Part
B's §6 has not been started.**

## 8. API/UI completion

Backend: `app/Tefca/source_policy.py` is a pure Python module with no
route exposed yet — `official_view()`/`proposed_view()`/`both_views()`
exist as callables, not as an API endpoint. No new route was added this
round. Frontend: none.

## 9. Policy decisions and remaining blockers — explicit

1. **Whether `source_policy.py` should be exposed via an API/UI surface**,
   and to whom (reviewer-only? everyone?) — not decided here.
2. **The retention-policy gap named in §4** — no retention rule exists;
   per this task's own instruction this should record `POLICY_UNAPPROVED`
   and avoid introducing automated deletion, but no code or document for
   it was written this round.
3. **Whether a recheck-trigger mechanism exists or needs building**
   (§5) — not resolved, named as unverified rather than assumed either
   way.
4. **The full corpus extension and the entire frontend/LMS deliverable**
   (§6/§7) are the largest remaining blockers to calling Part B complete.
5. As with Part A: no decision was made about ever turning any of this
   round's work from shadow/proposed into an active, official policy —
   that remains a COR-facing decision outside this session's authority.

## 10. Contract mapping

`qa-evidence/PROJECT_CONTRACT_CONTEXT.md` (same document Part A used) is
the authority for this round's central claim that no source policy is
approved today — Task 2/Deliverable 2 (the COR-reviewed Review
Methodology and Control Framework) has not been accepted, so
`POLICY_UNAPPROVED` is not a pessimistic default invented for this
module; it is the honest current state. This round does not resolve any
of that file's five open questions.

## 11. Is this ready for independent review?

**Partially, and only the parts explicitly marked as such.** The
`source_policy.py` module and its 17 tests (§3) are a complete, scoped,
independently-reviewable unit. The §2/§4 audits (§4 above) are
reviewable as claims-with-citations — a reviewer can check each cited
file:line against the actual code. **The task as a whole is NOT
complete and is NOT ready to be presented as a finished Part B**: the
corpus extension (§5), the frontend, and the LMS handoff (§6/§7) have
not been started. Recommend treating this checkpoint as a status update
and one small verified increment, not a Part B sign-off.
