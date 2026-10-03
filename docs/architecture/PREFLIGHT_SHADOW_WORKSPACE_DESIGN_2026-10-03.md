# Preflight, shadow reassessment and the analyst workspace — design (2026-10-03)

Branch `feat/preflight-shadow-pilot-2026-10-03`, based on
`fix/sam-verification-contract-2026-10-02` @ `b4c69d4` (so SEED_RULES_V3 and the
SAM evidence-contract fixes are present). Directive items 9 and 10 of the
2026-10-02 master continuation. Backend only: **no frontend screens are built
by this work**; every surface is an authenticated JSON route.

## 1. The three modules

| Module | What it does | Writes |
|---|---|---|
| `app/tefca_registry/rce/preflight.py` | Runs over Area 1 before `verify_and_classify`: schema, identifiers, conditional blanks, missing context. Reuses quality rules by id; implements only the checks no rule covers (`PF-*`). | `rce_preflight_run`, `rce_preflight_finding`, `rce_preflight_normalization` |
| `app/tefca_registry/rce/shadow_reassessment.py` | Re-runs **the classifier only** over each official review record's persisted `classifier_input` under a pinned baseline and candidate rule set; records per-entity deltas, direction, triggering source states; binds approvals to the package hash; designs successor publication. | `rce_shadow_comparison`, `rce_shadow_finding_delta`, `rce_shadow_approval`, `rce_successor_publication_event`; **`review_records` successor rows only in `SHADOW_PUBLICATION_MODE=local_test`** |
| `app/tefca_registry/rce/analyst_workspace.py` | Read model per entity: findings + governing requirement, persisted evidence with freshness, unanswered questions, context patterns, Epic's-lessons assessment slots, delivery-level cause groups. | nothing |

Routes: `app/tefca_registry/rce/preflight_shadow_routes.py`, registered in
`app/main.py` via `safe_load` (one additive line + comment). Models registered via
one additive import at the end of `app/tefca_registry/rce/models.py`.

## 2. Data model and migration

Alembic revision **`20261003_preflight_shadow`**, file
`alembic/versions/20261003_preflight_shadow_workspace.py`,
`down_revision = "20260930_alembic_version_read"` (main's head as committed).
It is a **sibling** of the unmerged reporting (`20261001_report_generation_jobs`) and
IQVIA (`20261002_iqvia_observations` → `20261003_iqvia_upload_durability`) revisions;
when combined, relink `down_revision` onto whichever lands last. Verified: from an
empty database, `alembic upgrade head` applies the full chain (27 upgrades) with 0
errors; `downgrade -1` / `upgrade head` of this revision is clean.

Tables created (none collides with any `iqvia_*`, `report_generation_jobs`, or
existing `rce_*` table — checked by grep across the main, IQVIA and reporting
worktrees' migrations):

```
rce_preflight_run                one preflight pass per delivery
rce_preflight_finding            one finding; applicability/execution/evidence/disposition are SEPARATE columns
rce_preflight_normalization      original + derived + method, beside the untouched original
rce_shadow_comparison            pinned package: rule versions, evaluation date, intake, evidence refs, hashes
rce_shadow_finding_delta         per-entity NEW/REMOVED/CHANGED/UNCHANGED/NOT_REPRODUCIBLE + direction
rce_shadow_approval              ANALYST / INDEPENDENT_QA, bound to package_hash, unique per role per package
rce_successor_publication_event  every attempt: REFUSED / PUBLISHED / ALREADY_PUBLISHED, keyed on package_hash
```

Grants: `SELECT, INSERT` only to the runtime role (`DB_APP_ROLE`), same discipline as
`20260921_september_snapshot`. No UPDATE/DELETE. Downgrade refuses if any row exists.

## 3. Preflight

**Four dimensions, never collapsed.** Every finding carries `applicability`
(`applies` / `does_not_apply` / `unresolved`), `execution` (`done` / `unavailable` /
`insufficient`), `evidence` (JSONB of what was observed) and `disposition` (`open` /
`informational` / `blocked`) as separate columns with CHECK constraints.

**Reuse before reimplementation.** Fifteen quality rules are called by id
(`SCH-001`, `SCH-003`, `ID-001/002/003/005`, `NPI-002/003/004`, `REQ-001/002/003`,
`INT-001/002`, `CON-003`) and recorded under `rule_ref`. Only the gaps are new:
`PF-SCH-001..007` (missing / extra / reordered / renamed columns, delimiter,
encoding or mojibake, BOM), `PF-OID-001` (id neither OID nor UUID), `PF-HCID-001`
(not `urn:oid:` + OID), `PF-CCN-001` (CCN shape), `PF-CTX-001` (no NPI **and** no
`hl7orgrole` → NPI-requirement applicability `unresolved`, execution `insufficient`,
disposition `informational`), `PF-CTX-000` (delivery-level count of blank
`hl7orgrole`, recorded once).

**Classification gate.** `BLOCKED` only when a column of the locked 41-field map is
missing (`PF-SCH-001`); otherwise `CLEAR_WITH_FINDINGS` or `CLEAR`. A renamed-looking
column (`PF-SCH-004`) is reported, never auto-mapped.

**Originals untouched; derivations recorded separately.** Whitespace trim, ZIP
zero-pad (`FMT-001`), state upper-casing (`FMT-002`), tab normalisation (`FMT-004`)
and the `active` round-trip form (`CON-003`) are written to
`rce_preflight_normalization` with `original_value`, `derived_value`, `method`,
`rule_ref`. Nothing in Area 1 is modified (proven by a byte-level snapshot test).

**Why PF-CTX-001 is informational, not open.** `quality_rules.npi_required()`
deliberately returns False on a blank role so no case is ever created for a blank
role alone (the matrix correction of 2026-10-02). Preflight preserves that
behaviour and records the *applicability* as unresolved — a statement about what
the delivered data can prove, not a finding against the record.

Routes (reviewer floor — findings carry original delivered values):
`POST/GET /api/tefca/rce/deliveries/{intake_id}/preflight`,
`GET /api/tefca/rce/preflight-runs/{run_id}/findings?category=&disposition=`,
`GET /api/tefca/rce/preflight-runs/{run_id}/normalizations`.

## 4. Shadow reassessment

**Pinned at build time:** baseline rule version; candidate (a stored version, or a
what-if rule list, hashed into the package); evaluation date; intake; every official
review record compared (id, review_id, entity, its own rule version, its evidence
`generation_timestamp`); the delivery's `source_snapshot` references;
`official_baseline_hash` (SHA-256 over the classification state of the pinned rows);
`package_hash` (SHA-256 over the canonical JSON of pins + deltas + rule hashes).

**What is re-run:** `BucketClassifier.classify` only, over the persisted
`classifier_input` (RCE path) or `sources`/`fields` (manual path). **Zero connector
calls** (tests patch every connector method to raise during a build). **Zero official
rows written** (tests fingerprint `review_records`, `rce_issues`,
`tefca_dimension_evidence` before/after).

**Reproducibility check:** the baseline rules are re-run too; if they do not
reproduce the official bucket, the entity is `NOT_REPRODUCIBLE`,
`manual_review_required`, and never compared as if like-for-like.

**Delta vocabulary:** `NEW` (B1 → non-B1; `predecessor_finding_id` is null — no
predecessor finding is required), `REMOVED`, `CHANGED`, `UNCHANGED`,
`NOT_REPRODUCIBLE`. **Direction** by bucket severity (B1 < B2 < B3 < B4):
`STRICTER` / `MORE_PERMISSIVE` / `NEUTRAL` — both kinds of change are reviewed.
**EIN/FEIN/IRS**: any delta whose input or rules name an `ein` / `fein` / `irs` /
`tax_id` signal is `manual_review_required` and is withheld from publication.

**Per-source triggering states.** Each delta's `detail.source_states` carries, per
source, the classifier `status` **and** the persisted `disposition` behind it, with a
`not_found_origin` of `pending_hit_REVIEW`, `clean_screen_NOT_FOUND`, `CONFLICT` or
`unknown_disposition_not_persisted`. This exists because
`arc_pipeline._DISPOSITION_TO_STATE` maps both a clean `NOT_FOUND` screen and a
pending `REVIEW` hit to the single state `not_found`, so a rule written against
`not_found` fires on both. **A shadow comparison would have surfaced exactly this
over-disqualification before official application**: the real v3-vs-v2 comparison in
`tests/test_shadow_reassessment.py` reports, on the same delivery, one entity with a
confirmed exclusion (`pending_hit_REVIEW`) and one with a clean name screen
(`clean_screen_NOT_FOUND`) both moving between B4 under v3 and B2 under v2
(`[shadow v3->v2] confirmed: CHANGED/MORE_PERMISSIVE B4->B2; clean screen:
CHANGED/MORE_PERMISSIVE B4->B2`). The test asserts only that the movement is
detected and reported with its direction and originating states; whether v3 is right
to disqualify on a clean screen is a translator/rules question owned by the SAM
branch, and this work does not touch the translator or the rules.

**Approvals:** `ANALYST` (reviewer floor) and `INDEPENDENT_QA` (qalead, checked
in-route so the refusal names the role). Each binds to the exact `package_hash`
(mismatch → 409), requires a rationale, is unique per role per package, refuses the
same person for both roles ("segregation of duties: … may not give the … approval",
409), and refuses a **stale** package (any pinned official row changed since build).

**Successor publication (design, local test only):**
1. `SHADOW_PUBLICATION_MODE` must equal `local_test`; otherwise a `REFUSED` event is
   written and the call raises — official application is outside this code's
   authorization by construction.
2. Idempotent retry is checked **before** approvals and staleness: a `PUBLISHED`
   event for this package returns the same successor ids with an
   `ALREADY_PUBLISHED` event and writes nothing (a successor is itself a newer
   official row, so the package is stale by construction afterwards; a retry must
   never be refused for that).
3. Both approvals for the current package hash; stale check.
4. Under the review-id allocation lock, one **new** `review_records` row per
   actionable delta (`NEW`/`REMOVED`/`CHANGED`, not `UNCHANGED`, not
   `NOT_REPRODUCIBLE`, not `manual_review_required` — those are returned as
   `withheld`), carrying the candidate bucket/rule/version, a
   `[SHADOW-SUCCESSOR of REV-…]` rationale, and a `verification_results.
   shadow_successor` block naming comparison, package hash, candidate rules hash and
   predecessor review id. **The predecessor row is never modified.**
5. A `PUBLISHED` event with predecessor and successor review ids; an audit row.

Routes: `POST /api/tefca/rce/shadow/comparisons` (reviewer),
`GET …/comparisons/{id}` and `…/deltas?kind=&direction=` (viewer — buckets, rules,
hashes, no delivered values), `POST …/comparisons/{id}/approvals` (reviewer; QA role
needs qalead), `POST …/comparisons/{id}/publish` (qalead).

## 5. Analyst workspace

`GET /api/tefca/rce/deliveries/{intake_id}/workspace?entity_id=&limit=&offset=`
(reviewer floor). Per promoted entity:

- **findings**: the current-run `rce_issues` on its delivered line (stage derived by
  `exception_ledger.stage_for`), each with its **governing requirement** (quality rule
  id + version + description, the field map's `documented` and `docuaction` text,
  necessity, correction authority) and a link to the existing disposition route;
  NPI values are masked.
- **review**: the latest classified `review_records` row with the `review_rules` row
  that produced it (name, version, effective/retired dates, conditions) and links to
  the existing claim / determination / independent-QA / history routes — the workspace
  never bypasses them.
- **evidence**: latest-generation `tefca_dimension_evidence` items, each with
  `freshness` (`REUSED_FRESH` / `STALE` / `UNKNOWN_AGE`) against
  `WORKSPACE_EVIDENCE_FRESHNESS_DAYS` (default 30), so fresh evidence is reused rather
  than re-requested.
- **unanswered_questions**: templated per issue type, bucket and dimension
  disposition (e.g. an unconfirmed exclusion match → "Confirm whether the
  exclusion/debarment record is this organisation…").
- **context_patterns**: `RELATED_ORGANIZATIONS` (shared TEFCAID family),
  `MIXED_BUSINESS_MODEL` (several exchange-purpose classes), `VIRTUAL_CARE_POSSIBLE`
  — every one `auto_reject: false`.
- **contextual_assessments** (Epic's lessons): `traffic_volume`, `exchange_balance`,
  `geographic_plausibility` read **only** an authorized traffic-evidence input via an
  injectable provider. No such persisted record type exists; the default provider is
  `None`, so each slot reports `assessment: unavailable, reason: authorized traffic
  evidence not provided`. Nothing is derived from registry or delivery data and no
  traffic-monitoring obligation is asserted — contract scope is not expanded.

Delivery-level **cause_groups** (same rule + issue type + field across the delivery)
say "investigate once"; per-record disposition and independent QA remain per record.

## 6. Authorization summary

| Surface | Floor | Why |
|---|---|---|
| preflight run/read, normalizations, workspace | reviewer | returns delivered values (same principle as the delivery-workflow allowlist in `tests/test_rbac_roles.py`, where the four GETs are listed with this justification) |
| shadow build, ANALYST approval | reviewer | — |
| shadow read, deltas | viewer | buckets/rules/hashes only |
| INDEPENDENT_QA approval, publish | qalead | independent QA; publication is a qalead act and is refused outside local test mode anyway |

No PUT/PATCH/DELETE route was added under `/rce/`
(`tests/test_official_delivery_workflow.py` still passes).

## 7. What is local-only, and what stays outside authorization

- **Local-only:** successor publication (`SHADOW_PUBLICATION_MODE=local_test`), used
  by `tests/test_shadow_reassessment.py` against a disposable database.
- **Outside authorization, by construction:** official application of successor
  findings in any other mode (refused, with a persisted `REFUSED` event); any change
  to `review_records` other than appending a successor; any edit to Area 1,
  `rce_issues` or `tefca_dimension_evidence`; any connector call during a comparison.
- **Not built:** frontend screens for any of the three surfaces; a persisted
  authorized-traffic-evidence record type (the assessment slots are ready to consume
  one when it is defined and authorized).

## 8. Tests and reproducible commands

```
cd <shadow-be worktree>
export DATABASE_URL=postgresql+asyncpg://docuaction_owner:docuaction_owner@127.0.0.1:5499/test_lq_main
export SECRET_KEY=<64 chars>  ALLOWED_HOSTS='*'  ENTITY_RESOLVER_SOURCE=db  DB_APP_ROLE=docuaction_app
python -m alembic upgrade head
python -m pytest tests/test_preflight.py -q               # 3 passed
python -m pytest tests/test_shadow_reassessment.py -q -s  # 7 passed (prints the real v3->v2 line)
python -m pytest tests/test_analyst_workspace.py -q       # 5 passed
python -m pytest tests/test_rbac_roles.py -q              # 31 passed, 2 skipped (frontend source absent)
python -m pytest tests/test_official_delivery_workflow.py -q   # 85 passed
python -m pytest tests/test_rules_engine.py -q            # 49 passed
```
