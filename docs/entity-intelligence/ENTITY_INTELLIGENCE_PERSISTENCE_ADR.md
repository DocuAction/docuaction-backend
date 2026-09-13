# ADR — Persistence for Entity Identity & Location Intelligence

Status: **PROPOSED — decision required (morning gate D)**. Date: 2026-09-12. Scope: where and how `ei_*` tables live once the capability is integrated. Nothing in this ADR is applied; the shared DEV QA database and Alembic head `20260903_delivery_grants` are untouched.

## Context

The foundation defines five tables on a separate declarative base (`EntityIntelligenceBase`): `ei_source_deliveries`, `ei_evidence_observations`, `ei_evidence_comparisons`, `ei_historical_deltas`, `ei_system_evidence_assessments`. The platform's `Base.metadata` cannot see them, so the startup `create_all()` and Alembic autogenerate cannot create them by accident. The pending DDL script lives outside `alembic/versions/` and refuses non-local URLs.

Constraints that shape the decision:

- Tasks 1–6 are in active QA on a shared DEV database; no migration, even additive, may land there until QA completes (program rule).
- Observations are append-only evidence with provenance; they are re-derivable from preserved source files plus the program delivery.
- Some future sources (IQVIA under licence, Google) may forbid raw persistence; the model already carries `value_handling` per observation.
- Volume: one NPPES monthly bundle is ~8.9 M rows / ~10 GB; only organisations relevant to delivered entities are observed, so observation volume is bounded by program entities × sources × fields (tens of thousands, not millions). Preserved raw files are file storage, not rows.
- Reports, RBAC and the four contractual categories read the platform schema; the assessment must never be joinable into a category by accident.

## Options

### A — Integrate into the platform metadata later (single database, single Alembic chain)

Move the models onto `app.core.database.Base` in a controlled integration sprint; copy the DDL into `alembic/versions/` with a real `down_revision`; one migration, one database, one backup.

- Pros: one operational surface; transactions can span program entity and evidence; existing RBAC/session plumbing reused; simplest deployment.
- Cons: the migration touches the shared schema and must wait for the QA freeze to lift; a platform `create_all()` would then also create `ei_*` tables anywhere the app boots; harder to enforce "assessment is not a category" at the storage layer; licensed-source data would sit beside Government data in one backup set.

### B — Separate bounded context (own schema or own database, own migration chain)

Keep `EntityIntelligenceBase`; deploy to a dedicated PostgreSQL **schema** (`entity_intelligence`) in the same server, or a separate database, with its own Alembic environment (`alembic_ei/`) and its own revision chain. Cross-context reference is by `canonical_entity_id` only — no foreign keys into the platform tables.

- Pros: the QA freeze is never at risk (a separate chain cannot touch `public`); grants can differ (read-only for report roles, none for analyst roles until a UI exists); licensed or transient data can be retained or purged on its own schedule; the "not a category" rule is structural — no join path exists.
- Cons: two Alembic environments to run in release pipelines; no cross-schema transactions (acceptable: evidence is append-only and idempotent by `content_hash`); the integration sprint must add a read adapter so the platform can show assessments.

### C — File-backed evidence store, no relational persistence until approved

Persist only preserved source files (hashed) and the run output as JSON artifacts under the existing report-artifact storage. Re-derive on demand.

- Pros: zero schema change anywhere; trivially satisfies the freeze; nothing to migrate.
- Cons: no history queries (what changed since the prior delivery) without re-running; no prior-decision linkage; awkward for 10k+ entities; pushes the decision rather than making it.

## Recommendation

**Option B.** It is the only option under which the floor cannot move by construction, it matches the data-rights model (per-source retention and handling), and it keeps the assessment structurally outside the contractual workflow. Option A remains available later if the program prefers a single surface; the move is mechanical because no foreign keys are introduced now.

Decision needed from the program owner: B as recommended, or A with an explicit date after QA closure. C is recorded as the fallback if neither is approved.

## Consequences if B is adopted

1. Add `alembic_ei/` (separate `env.py`, `script_location`, `version_table = ei_alembic_version`) targeting schema `entity_intelligence`; the existing `alembic/` is not edited.
2. The DDL script in `app/core/entity_intelligence/migrations/` becomes the first revision; it keeps the local-only refusal for ad-hoc use.
3. Runtime: a second engine/session factory bound to the same server with `search_path=entity_intelligence`, created only when `ENTITY_INTELLIGENCE_ENABLED` is true; the platform engine is never used for `ei_*` access.
4. Grants: the application role gets DML on `entity_intelligence.*`; report roles get none until a report is approved to read assessments (none is today).
5. Backups: same server backup covers both schemas; the retention job purges `TRANSIENT_ONLY` observations (there are none until a licensed source is approved).
6. Rollback: `DROP SCHEMA entity_intelligence CASCADE` removes the capability with no effect on the platform.

## Validation performed tonight

- Metadata disjointness test (platform `Base.metadata.tables ∩ EntityIntelligenceBase.metadata.tables = ∅`).
- DDL applied to SQLite in tests; PostgreSQL validation status is recorded in `ENTITY_INTELLIGENCE_PERFORMANCE_REPORT.md` (`POSTGRESQL_ISOLATED_VALIDATION`).
- Migration refusal tests for Azure, private-IP, look-alike-local and `.local`-infix hosts.
