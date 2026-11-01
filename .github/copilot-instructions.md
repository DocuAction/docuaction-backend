# Copilot review guidance — DocuAction TEFCA ARC backend

This repository processes Government healthcare-directory deliveries. Review for
correctness and the invariants below, not style. These are the properties the
most costly past defects violated; weigh a change against them.

## Authorization (role floors)
- Reads that return delivered values (entity names, NPIs, source records, the
  issue ledger) sit at the **reviewer** floor. Counts-only reads sit at
  **viewer**. State-changing or privileged actions (coverage runs, snapshot
  persist, candidate backfill) sit at **program_manager/qalead**.
- A new `GET /deliveries/{id}/…` route that returns identifiers must be at the
  reviewer floor and covered by the role matrix in `tests/test_rbac_roles.py`
  (its `VIEWER_READS` / `WRITE_ENDPOINTS` sets — a delivered-values read must not
  be in `VIEWER_READS`), mirroring the drill-down routes. Flag any read of
  delivered values gated only at viewer, or any new route with no explicit
  `require_role`.
- "No self-QA" separation of duties: the application layer refuses a self
  submit/approve, and a DB-level enforcement is specified in migration
  `20261012_sod_db_enforcement` — which is marked DRAFT and is NOT yet applied
  to DEV or PROD, and depends on the manual `SOD_DB_OWNERSHIP_PREREQUISITE.sql`.
  Treat the DB control as PENDING deployment, not an existing runtime invariant;
  flag any code path that lets the same identity both submit and approve, and do
  not rely on the DB layer to catch it until its deployment is verified.

## Transaction isolation
- A failure in a best-effort side write (issue-ledger finding, audit row,
  supplementary enumeration) must not poison the surrounding transaction. Such
  writes/reads belong inside `async with db.begin_nested()` so a SQL error
  rolls back to a savepoint; the `except` that "must not fail the batch/report"
  is only safe if the session stays usable afterward. Flag a bare
  try/except around DB I/O on a shared `AsyncSession` with no savepoint.
- Read every response value off an ORM object BEFORE a later `commit()` that
  could expire it; a post-commit lazy attribute read triggers implicit async
  I/O and fails. Flag response assembly that reads ORM attributes after an
  intervening commit.

## Delivery scoping
- Every delivery-scoped query is keyed to ONE `source_intake_id`; an entity can
  be promoted by more than one delivery, so a finding or lookup must target the
  curated record of the SCREENING delivery (pass `intake_id`/`source_record_id`),
  never "the latest curated record across all deliveries". Flag an entity-only
  lookup that ignores the delivery.

## Idempotency
- Writes that can be retried (coverage batches, candidate findings, backfills)
  must be idempotent: an undecided finding already open for the same
  entity-per-delivery is reused, not duplicated; batches take the per-intake
  advisory lock (`_try_advisory_batch_lock`) so a concurrent or repeated run
  cannot double-write. Flag an INSERT with no dedup/lock on a retryable path.
- Snapshot sequence is `max+1` under a UNIQUE(job_id, sequence); a concurrent
  persist must lose cleanly (409), never duplicate a sequence.

## Evidence provenance (append-only)
- `tefca_dimension_evidence` is append-only: re-running writes a NEW generation;
  prior rows are never updated or deleted. The "current" outcome is the LATEST
  row per entity. Flag an UPDATE/DELETE of evidence, or a "current" read that
  does not pick the latest generation.
- Provenance (edition date, dataset anchor, sha256) is carried from the row's
  own `original_values`, never re-derived or guessed. A value that cannot be
  recovered stays absent/NULL, not backfilled.

## Source-unavailable handling
- "Attempted" is not "answered". A source returning UNAVAILABLE/failed is an
  attempt with no answer, never clearance. The SAM public-extract supplementary
  screen is a DISTINCT source: a candidate is a potential match awaiting an
  analyst (never a confirmed exclusion); a no-candidate/absent result is never
  exclusion clearance; its availability never stands in for the SAM primary API.
  Flag language or logic that treats an attempt or a non-match as a pass/clear.

## Report integrity
- Delivery-scoped reports pin ONE reconciliation snapshot and must reconcile:
  a section may count fewer entities than the population, never more. Generation
  fails closed on a scope leak. A degraded sub-read is disclosed as a stated
  limitation, never rendered as a silent zero. Flag a report that recomputes
  live instead of pinning, or that hides an unreadable section as zero.

## Scope / data safety
- Synthetic test data only in tests and fixtures (`*.synthetic-test.docuaction.invalid`).
  Never real NPIs/names/addresses. No change should touch the October
  production delivery, open a database firewall, or call an external source at
  scale from a test path.
