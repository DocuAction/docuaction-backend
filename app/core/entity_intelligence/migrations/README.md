# Entity Intelligence — pending migrations (NOT registered with Alembic)

These scripts are written in Alembic's operations style but live **outside**
`alembic/versions/` on purpose. The shared DEV QA database must not receive
this DDL during the current Tasks 1–6 QA cycle, and the application's Alembic
head must stay at `20260903_delivery_grants`.

Apply only to a local, ephemeral or CI PostgreSQL:

```
python -m app.core.entity_intelligence.migrations.apply --database-url postgresql+asyncpg://...   # local only
```

or, in tests, `EntityIntelligenceBase.metadata.create_all(engine)` (SQLite or
PostgreSQL). When the capability is scheduled for controlled integration, the
script is copied into `alembic/versions/` with a proper `down_revision`, in a
migration sprint of its own.

Tables: `ei_source_deliveries`, `ei_evidence_observations`,
`ei_evidence_comparisons`, `ei_historical_deltas`,
`ei_system_evidence_assessments`. No existing table is modified.
