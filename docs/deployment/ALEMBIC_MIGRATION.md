# Alembic Migration Plan

**Status: documented, not implemented. Production startup is unchanged.**

## Current state

Production creates and updates schema by calling `Base.metadata.create_all()` at
application startup (`app/main.py:128`, `app/core/database.py:80`). Alembic is
already configured — `alembic.ini` is present and there are four migration files
under `alembic/versions/` — but the runtime does not use it.

```
alembic/versions/20260627_tefca_initial_schema.py
alembic/versions/20260627_tefca_dashboard_tables.py
alembic/versions/20260725_platform_config_tables.py
alembic/versions/20260725_tefca_registry_tables.py
```

So this is not "Alembic is missing". It is worse in one specific way: Alembic
exists and is not authoritative, which means the migration files and the live
schema can disagree with nobody noticing. A developer reading the migrations
would form an incorrect picture of the deployed database.

## Risk

| Risk | Consequence |
|---|---|
| No migration history | Cannot tell which schema version a given deployment is running |
| No rollback capability | A bad schema change cannot be reversed; only restored from backup |
| `create_all` adds but never alters | A changed column type or a dropped column is silently ignored |
| Silent divergence | Migration files describe a schema that may not match production |
| Startup-time DDL | Every instance attempts DDL on boot; concurrent starts race |

The third row is the one that bites in practice. `create_all()` creates missing
tables and missing columns. It does **not** alter an existing column, drop one,
add a constraint, or change a default. A model change of that kind appears to
deploy successfully and simply does not take effect — the application then runs
against a schema it believes it has.

## Plan

1. **Generate a baseline migration from the current production schema.**
   Autogenerate against a restored copy of production, not against a fresh
   database — the point is to capture what actually exists, including anything
   `create_all` produced that no migration file describes.
   ```bash
   alembic revision --autogenerate -m "baseline from production schema"
   ```
   Review the generated file by hand. Autogenerate is a starting point, not an
   answer; it routinely misses server defaults, index names, and enum changes.

2. **Stamp it as applied.** The baseline describes what is already there, so it
   must never actually run against production.
   ```bash
   alembic stamp head
   ```

3. **Switch startup from `create_all` to migrations.** Run
   `alembic upgrade head` as a deployment step, not at application startup.
   Running migrations on boot means every instance races to apply DDL during a
   scale-out or restart.

4. **Test on dev first**, including a rollback: apply, verify, `alembic downgrade
   -1`, verify, re-apply.

5. **Deploy to prod** with a verified database backup taken immediately before,
   and with the previous release tag ready to redeploy.

## Sequencing constraint

Steps 1 and 2 must be done against a schema snapshot that nothing is writing to.
If `create_all` runs between the autogenerate and the stamp, the baseline is
already stale.

## Rollback design going forward

Once Alembic is authoritative, schema changes should stay **forward-compatible**
with the previous application version wherever possible — additive, nullable
columns rather than renames or type changes. That keeps rollback to "redeploy the
previous tag" rather than "restore the database", which is the difference between
a two-minute recovery and an eight-hour one. This is the same pattern the Bulletin
Phase 4 registry columns used deliberately: 18 nullable columns added via
`ADD COLUMN IF NOT EXISTS`, no existing column modified.

## Not doing this yet, and why that is defensible

The current arrangement works because the schema is append-only in practice. It
stops being defensible the first time a column needs to change type or be
dropped. Treat that as the trigger: this plan should be executed before the next
non-additive schema change, not on a calendar date.

Tracked as a POA&M item under configuration management (AGT-CfMP-016).

Related: `docs/deployment/DEPLOYMENT_CHECKLIST.md`,
`docs/deployment/azure-deployment-guide.md`.
