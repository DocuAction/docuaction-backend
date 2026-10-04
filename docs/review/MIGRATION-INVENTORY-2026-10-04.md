# Migration inventory — corrected (2026-10-04, R29-1)

**This document corrects an error in this stack's own prior review
documents.** Earlier checkpoints (Round 26–28) stated "no new migration
was added this round" and, in `FINAL-REVIEW-GUIDE-2026-10-04.md`,
"Alembic head unchanged by any commit in this stack." Both are wrong
when read against the actual comparison an independent reviewer makes:
this PR's base, not this round's own starting point. Found by an
independent comparison against base `ea92ea5` (PR #110/#66's head), not
by this session re-reading its own prior claims unprompted.

## Exact heads

| | Alembic head |
|---|---|
| Base (`ea92ea5`, PR #110/#66's head) | `20261003_preflight_shadow` |
| Candidate (this stack's head) | `20261004_recheck_jobs` |

Confirmed directly: at `ea92ea5`, `20261003_preflight_shadow` is the
unique chain head (no other migration file at that commit names it as a
`down_revision`; verified by checking every file, not assumed from the
latest filename date).

## Three new revisions, in chain order

| Revision | File | Down-revision |
|---|---|---|
| 1 | `alembic/versions/20261004_preflight_exec_held.py` | `20261003_preflight_shadow` |
| 2 | `alembic/versions/20261004_stage_event_preflight.py` | `20261004_preflight_exec_held` |
| 3 | `alembic/versions/20261004_recheck_jobs.py` | `20261004_stage_event_preflight` |

## What each actually changes (read from the migration files, not assumed)

**1. `20261004_preflight_exec_held`** — drops and recreates
`ck_rce_preflight_finding_execution` on `rce_preflight_finding.execution`
to add the allowed value `'held'` (previously `'done'`, `'unavailable'`,
`'insufficient'`). No column added or removed. No existing row changes
(nothing writes `'held'` yet — `EXEC_HELD` is not wired to any code path
as of this stack).

**2. `20261004_stage_event_preflight`** — drops and recreates
`ck_rce_stage_event_stage` on `rce_delivery_stage_events.stage` to add
the allowed value `'PREFLIGHT'` (inserted between `PARSING` and
`QUALITY` in the stage order). No column added or removed. No existing
row changes (nothing writes this stage unless
`ENABLE_PREFLIGHT_ENFORCEMENT` is on, which it is not by default).

**3. `20261004_recheck_jobs`** — creates two new tables,
`rce_recheck_job` and `rce_recheck_item`, with their own primary keys,
foreign keys (`rce_recheck_job.intake_id` → `rce_source_intakes.id`
`ON DELETE RESTRICT`; `rce_recheck_item.job_id` → `rce_recheck_job.id`
`ON DELETE RESTRICT`), unique constraints
(`uq_rce_recheck_job_idempotency`, `uq_rce_recheck_item_job_entity`),
CHECK constraints on both tables' enumerated columns (`trigger_kind`,
`state` on the job; `state` on the item), and two indexes. Grants
`SELECT, INSERT, UPDATE` — explicitly never `DELETE` — on both new
tables to the role named by the `DB_APP_ROLE` environment variable; the
migration raises its own `RecheckJobsPreconditionError` and refuses to
run at all if that variable is unset, rather than guessing a role name.

**No existing table, column, or grant is altered or dropped by any of
the three.** This part of the earlier "additive" characterization is
correct — but additive-on-the-way-up does not, by itself, mean the
downgrade direction is safe, which is the claim the earlier documents
made and this one withdraws.

## Downgrade behaviour — demonstrated against a real database, not assumed

New test: `tests/test_recheck_and_preflight_migrations_2026_10_04.py`
(this commit), run against a disposable throwaway database created and
dropped by the test itself. Confirmed, not assumed:

| Revision | Downgrades cleanly while empty? | Downgrade with dependent data present |
|---|---|---|
| `preflight_exec_held` | Yes (confirmed) | **Fails with a raw, unguarded Postgres `CheckViolation`** — no named precondition. Demonstrated by inserting a `rce_preflight_finding` row with `execution='held'` and downgrading past this revision: the CLI exits non-zero with `CheckViolation` in its output, and the chain correctly stays at its prior revision (not left half-migrated) — but the FAILURE MODE itself is a raw database error, not a reviewed, intentional refusal message. |
| `stage_event_preflight` | Yes (confirmed) | **Same raw-failure behaviour.** Demonstrated by inserting an `rce_delivery_stage_events` row with `stage='PREFLIGHT'` and downgrading past this revision: same raw `CheckViolation`, same safe non-corruption, same lack of a named, reviewed message. |
| `recheck_jobs` | Yes (confirmed; `DROP TABLE IF EXISTS`, idempotent) | **Explicitly refuses** — a named `RecheckJobsPreconditionError` stating exactly how many rows exist (`"rce_recheck_job holds N row(s); downgrade refused."`), confirmed by inserting one job row and downgrading: the CLI exits non-zero with that exact message, and the chain correctly stays at head. |

**The asymmetry is real and is named here rather than smoothed over**:
one of the three migrations in this stack was written with an explicit,
reviewed downgrade precondition; the other two were not. Neither of the
two currently has any code path capable of writing the new values
outside this stack's own test fixtures (`EXEC_HELD` and the
`PREFLIGHT` stage both require code or flags this stack does not turn
on in any shared environment), so the exposure today is theoretical —
but "theoretical today" is a statement about current callers, not a
property of the migration itself, and is not the same claim as "the
downgrade is safe."

## Compatibility and rollback — evidence versus what is still assumed

**Evidenced by the new test, this round:**
- All three revisions upgrade cleanly in chain order against an empty
  database.
- All three revisions downgrade cleanly when no dependent data exists.
- `recheck_jobs`'s grant is exactly `SELECT, INSERT, UPDATE` — never
  `DELETE` — for the app role, queried directly via
  `has_table_privilege`, not read from the migration source alone.
- The failure MODE of each downgrade under dependent data (two raw,
  one named-refusal) is demonstrated, not inferred from the two
  migrations' upgrade-direction simplicity.

**Reused, pre-existing, valid evidence** (not re-run this round, per
"reuse existing compatibility tests where valid"):
- `tests/test_traceability_migration.py`'s own end-to-end test, re-run
  this round against the current head (`20261004_recheck_jobs`):
  confirms the FIVE EVIDENCE tables' ownership and grants are
  unaffected by any of these three revisions (a CHECK constraint is
  independent of ownership and grants, and `recheck_jobs` touches
  neither of those five tables) — 1 passed, this round, at head.
- `tests/test_prod_managed_migration_integration.py`'s own file
  comments record that its managed prepare/migrate/finalize gate was
  run end-to-end through 17 revisions ending at this exact head
  (`20261004_recheck_jobs`) and passed, in an earlier round, on a real
  superuser-provisioned cluster. This session did not re-run it (it
  requires `CONV_SUPERUSER_URL`, not set in this session's environment)
  and is not claiming to have re-confirmed it — it is cited as existing,
  valid, dated evidence, not as this round's own result.

**Still NOT evidenced, and not claimed to be:**
- A downgrade of the two CHECK-widening migrations has NOT been
  demonstrated safe when their failure is reached partway through an
  automated, unattended rollback (e.g., a CI job that treats a
  non-zero exit as "proceed to the next step anyway"). The failure
  mode is loud and the chain stays put — confirmed — but recovery
  FROM that state (an operator diagnosing a raw `CheckViolation` under
  time pressure) is a documentation gap this inventory names rather
  than closes.
- No claim is made, here or anywhere in this stack, that a full
  production rollback of this stack is safe merely because its upgrade
  direction is additive. The data-dependent restrictions above are the
  reason: whether rollback is actually safe depends on what has been
  WRITTEN since upgrading, which this document cannot know in advance
  for any specific deployment.

## What this corrects, specifically, in this stack's other documents

- `docs/review/FINAL-REVIEW-GUIDE-2026-10-04.md` section 8 — rewritten,
  see that file; this inventory is now its primary source.
- `docs/review/OPERATOR-PREREQUISITE-CHECKLIST-2026-10-04.md` section 3
  — rewritten.
- `docs/review/INTEGRATED-QA-READINESS-2026-10-04.md` — a new section
  added recording this correction; no earlier section's wording was
  silently altered (Round 26–28's own account of what THEY did is
  preserved; only the migration-status claim is corrected, in a new,
  dated section, the same way every other correction in this document's
  history has been handled).
- The ledger and QA handoff — a new, dated entry each, same pattern.
- PR #115's own description — corrected directly (editing a PR
  description does not rewrite history the way editing a merged
  document would).
