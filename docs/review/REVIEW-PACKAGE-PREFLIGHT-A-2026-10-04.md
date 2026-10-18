# Preflight Enforcement — Part A Checkpoint
**Prepared**: 2026-10-04 (Round 22, Part A, step 4) — local/disposable
development only; no push, PR, merge, migration dispatch, deployment, or
shared-database change made under this task.

## 1. Baselines and current local SHAs

| Repo | Base branch | Base SHA | New local branch | Local work |
|---|---|---|---|---|
| Backend (PR #110) | `feat/reporting-architecture-2026-10-01` | `ea92ea5c33a5075dc40b1473a64152bb52a5c3e5` (app-code revision `264252582a63e19d14d0c715d23089fe6bab4916`) | `feature/preflight-exceptions` (local only, not pushed) | Delta doc (`31c6898`) + this round's implementation (uncommitted at the time this draft was written — see §6) |
| Frontend (PR #66) | `feat/reporting-architecture-2026-10-01` | `48031780ad4ce0b84126ae7187f49d1d1a8ee87b` | `feature/preflight-exceptions` (local only, not pushed) | No frontend change this round |

CI re-checked once against both base SHAs at the start of this round:
backend 10/10 pass; frontend all applicable checks pass (`analyze`/
`dependency-review` skip per the documented GHAS-entitlement gate,
unchanged). Neither PR was modified under this task — confirmed by not
touching either worktree's tracked branch, only the new local
`feature/preflight-exceptions` branch.

## 2. Completed controls and actual enforcement paths

**The gap** (confirmed by tracing code, not inferred from a docstring):
`preflight.run_preflight()` had exactly one caller in the whole
application — an admin-triggered dry-run route
(`preflight_shadow_routes.py`) — never the real
`delivery_runner._run_after_area1` stage loop, never `intake.ingest_delivery`,
never the IQVIA reference-snapshot staging path. Full trace:
`docs/review/DELTA-2026-10-04.md`.

**What this round implemented**:
- A new `STAGE_PREFLIGHT` stage (`delivery_job_model.py`, additive --
  `stage` is a plain `String(32)` column, no CHECK constraint, no
  migration needed for this part).
- A new `_stage_preflight` function (`delivery_runner.py`), gated by a
  new, default-OFF feature flag `ENABLE_PREFLIGHT_ENFORCEMENT`
  (`config.py`, same pattern as `ENABLE_DEV_RESTORE_ORIGINAL`). **When the
  flag is off (the default, unchanged for every official delivery today),
  the `STAGE_PREFLIGHT` tuple entry is not even added to the stage loop**
  — not merely a no-op inside it — so the stage-event timeline for a
  flag-off delivery is byte-for-byte identical to before this round
  (proven by the pre-existing, UNMODIFIED
  `test_a_clean_delivery_writes_every_stage_event_and_is_ready`, which
  still passes; see §4).
- When the flag is on: `GATE_BLOCKED` raises `PreflightBlockedError`
  (new, `preflight.py`), which the existing stage-loop failure handler
  treats like any other stage failure — closed FAILED, loop `break`s, no
  later stage runs. `GATE_CLEAR_WITH_FINDINGS` returns `held=True`, the
  SAME shape `_stage_promotion` already uses when `promote_delivery`
  declines rather than fails — closed SKIPPED, not FAILED, and the loop
  continues into `STAGE_QUALITY` and every later stage. `GATE_CLEAR`
  completes normally. Both non-trivial outcomes reuse an existing,
  already-proven pattern in this same file rather than inventing a new
  one.
- **Two DB-level CHECK constraints had to be widened** — found by a real
  `CheckViolationError` in a real local test run, not by code reading
  alone: `rce_preflight_finding.execution` (to admit the new `EXEC_HELD`
  value) and `rce_delivery_stage_events.stage` (to admit `'PREFLIGHT'`).
  Two small, additive migrations
  (`20261004_preflight_exec_held.py`, `20261004_stage_event_preflight.py`),
  both upgrade- and downgrade-tested against the disposable database used
  this round (port 5533). **Not dispatched against any shared or
  production database** — local/disposable only, per this round's
  boundary.
- **A genuine gap found and fixed in the preflight ENGINE itself**
  (`preflight.py`), not just its wiring: the task's own checklist names
  "duplicate or ambiguous headers" as a required check, and
  `_schema_findings` had none. Added `PF-SCH-008` (two delivered columns
  sharing one exact name — a parsing-trust problem, same severity class
  as a missing column, `DISP_BLOCKED`).

## 3. Tests and limitations

**New targeted tests, all passing against the disposable PostgreSQL 18
instance this session has used throughout (port 5533, NOT the shared
long-running instance on port 5499)**:

- `tests/test_preflight_enforcement_2026_10_04.py` (4 tests): flag-off
  byte-for-byte-unchanged timeline; flag-on clean delivery never blocks;
  flag-on per-record finding holds PREFLIGHT but the pipeline continues;
  flag-on missing-column finding blocks the whole delivery and no later
  stage runs.
- `tests/test_preflight.py` (+1 test): the new `PF-SCH-008`
  duplicate-header check, modeled on the file's own existing
  missing-column/rename test.
- `tests/test_seeded_fixture_corpus_2026_10_04.py` (9 tests) proving
  `tests/fixtures/seeded/manifest.json`'s 8-seed synthetic corpus against
  the real reader (`ingest_delivery`) and the real engine
  (`run_preflight`) — not a hand-built `RecordContext`. Covers: clean
  delivery, missing identity field, duplicate header, renamed header,
  reordered header, extra/unrecognized column, an unknown enum value
  (`sequoiaorgtype='Affiliate'`), and an identifier checksum failure (NPI
  failing the CMS check digit). The last two were marked `EMPIRICAL` in
  the manifest before running — this test recorded, rather than
  predicted, their actual observed codes
  (`UNKNOWN_SEQUOIA_ORG_TYPE`; `NPI_CHECKSUM_INVALID`), then the manifest
  was updated to match what was actually observed, not the reverse.

**Regression check**: the pre-existing
`tests/test_delivery_runner_events.py` (11 tests, unmodified),
`tests/test_official_delivery_workflow.py`, and the full
`tests/test_preflight.py`/`tests/test_shadow_reassessment.py`/
`tests/test_shadow_real_v4_candidate_2026_10_03.py` suites all still pass.
A full non-DB `pytest --collect-only` over `tests/` shows **4441 tests
collected, 0 collection errors** — nothing this round's edits broke an
import for, anywhere in the suite. A broader sweep across every
delivery/preflight/traceability/quality/shadow-named test file was run as
a final check; see §4 for its result.

**Limitations, disclosed**:
- CI has not run this round's code (local-only per this task's boundary;
  no push occurred). Local verification used PostgreSQL 18 (this
  session's disposable instance), not CI's exact `postgres:16` — the
  same documented substitution this session has used in every prior
  round lacking Docker access.
- `EXEC_HELD` (the new `EXECUTION` value) is added to the enum and its DB
  constraint, but **nothing in this round's code writes it yet** — it is
  reserved for Part B's record-level hold semantics (see the delta's P1
  note), not force-fit into Part A's delivery-level gate.
- The IQVIA reference-snapshot path (the task's "AND reference
  snapshots" requirement) is **NOT enforced this round** — `preflight.py`'s
  `_schema_findings` is hardcoded against the RCE 41-field map, and
  parameterizing it for IQVIA's different field shapes is named as a
  genuine, scoped Part B item in the delta (§6.2), not attempted this
  round to avoid a rushed, under-tested abstraction.
- "Truncation" and "record boundaries" (reader-level parsing concerns)
  are not separately proven by a dedicated seed in this round's corpus —
  the 8 seeds focus on the SCHEMA/IDENTIFIER/MISSING_CONTEXT dimensions
  preflight itself owns; reader-level truncation handling belongs to
  `intake.py`'s own parser and was out of this round's traced scope
  (named, not silently dropped).

## 4. Broader regression sweep (final check before this checkpoint)

A delivery/preflight/traceability/quality/shadow-named sweep
(`pytest tests/ -k "delivery or preflight or traceability or quality or
stage_event or shadow"`, 501 tests selected) was run twice against this
session's long-reused disposable instance (port 5533) and once more
against a genuinely FRESH database created and migrated from scratch for
this check (`test_journey_1003_fresh`, same port, new name) — the first
run surfaced 13 failures; re-running against the fresh database dropped
that to 3, confirming 10 of the 13 were accumulated cross-test pollution
on a database this session has reused across many rounds today (real
committed rows from synthetic deliveries, including this round's own new
tests, which — like the pre-existing `test_preflight.py` pattern they
follow — commit for real rather than roll back), not a code regression.

**One of the 13 WAS a real regression this round introduced, and was
fixed**: `tests/test_traceability_migration.py` hardcodes the alembic
chain's expected head revision (`HEAD`) and asserts `alembic upgrade
head` lands exactly there — this round's two new migrations moved the
true head past that hardcoded constant, exactly as every prior round's
new migration has required the same one-line bump (the file's own
comment history documents several). Bumped `HEAD` to
`20261004_stage_event_preflight`; confirmed neither new migration touches
the ownership or grants this test checks (both only widen a CHECK
constraint, unrelated to either). Re-ran in isolation: **1 passed**.

**The remaining 3 failures** (`test_automated_verification_cross_delivery_isolation.py`,
3 of its 4 tests) were isolated with a `git stash` of every file this
round touched, then re-run against the SAME fresh database with ZERO
code changes from this round applied: **the same 3 tests failed
identically**, confirming they are pre-existing and unrelated to this
round's work — not investigated further, as fixing a pre-existing,
unrelated failure is outside this task's scope.

**Final confirmed result** (fresh database, this round's code applied):
**493 passed, 3 failed (pre-existing, confirmed unrelated), 7 skipped**
(role/superuser-dependent tests this disposable setup's `docuaction_owner`
connection doesn't satisfy — the same tests pass under the `postgres`
superuser connection used elsewhere this round, e.g.
`test_traceability_migration.py`, §4 above). A full non-DB
`pytest --collect-only` over all of `tests/` shows 4441 tests collected,
0 collection errors throughout this round's edits.

## 5. Remaining work for Part B

1. IQVIA reference-snapshot preflight parameterization (delta §6.2) —
   either inject a field list into `_schema_findings` or write
   IQVIA-specific schema/identifier checks sharing the same validators.
2. Wire `EXEC_HELD` to an actual record-level hold code path (currently
   enum-only).
3. The NOT_VERIFIABLE/UNSUPPORTED split for the Verification dimension,
   and an UNKNOWN Freshness state (delta §5) — each needs its own
   call-site survey, not a blind rename.
4. A consistency test asserting every quality rule of a SCHEMA/
   IDENTIFIER/CONDITIONAL_BLANK/MISSING_CONTEXT category appears in
   preflight's `_REUSED_RULES` (delta §4's open policy question).
5. Decide (with the user/COR, not unilaterally) whether
   `ENABLE_PREFLIGHT_ENFORCEMENT` should ever be turned on for an
   official delivery, and under what review/approval gate — this round
   deliberately leaves it off and does not recommend a timeline for
   flipping it.

## 6. Contract mapping supported by the source document

`qa-evidence/PROJECT_CONTRACT_CONTEXT.md` was found (not blocked) and is
the authority this round's design decisions are checked against — see
`docs/review/DELTA-2026-10-04.md` §1 for the exact language relied on
("Do not assume all 41 directory fields can be verified by every external
source"; "Distinguish missing evidence, unsupported checks, ambiguous
matches and confirmed discrepancies"). This checkpoint does not resolve
any of that file's five open questions (closeout-window wording, the full
Task 6 rights clauses, the "1,298" interpretation, the deliverable-number
mapping, the workflow-to-task mapping) — none are touched by this round's
work, consistent with "keep the 1,298 interpretation unresolved."

## 7. What was NOT done, explicitly

No merge, no migration dispatch against any shared or production
database, no deployment, no firewall change, no account action, no PR
pushed. `ENABLE_PREFLIGHT_ENFORCEMENT` defaults to and remains `False`.
SEED_RULES_V4 untouched and still inactive. The completed IQVIA import
was not repeated. Round 10 (source-aware automation) remains paused, not
resumed.
